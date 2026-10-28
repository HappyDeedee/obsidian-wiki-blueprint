import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from wiki import Wiki, WikiError, digest, init, note, read_json
from demo import page, run


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        init(self.vault)
        self.wiki = Wiki(self.vault, "owner")
        self.source = "00-Inbox/随手放/source.md"
        self.target = "20-Wiki/实体/test.md"
        note(self.vault, self.source).write_text("原文：计划测试，预算未知。", encoding="utf-8")

    def proposal(self, content=None):
        return {"quotes": ["计划测试，预算未知"], "updates": [
            {"path": self.target, "content": content or page("fact_state: planned", [self.source])}]}

    def commit_one(self):
        tx = self.wiki.prepare(self.source, [self.target])
        return self.wiki.commit(tx["token"], self.proposal())

    def test_init_refuses_existing_work(self):
        before = note(self.vault, self.source).read_bytes()
        with self.assertRaises(WikiError): init(self.vault)
        self.assertEqual(before, note(self.vault, self.source).read_bytes())

    def test_full_demo_and_repeat(self):
        result = run(self.vault)
        self.assertEqual(result["status"], "REFERENCE_DEMO_PASS")
        self.assertTrue(run(self.vault)["reused_demo"])
        self.assertEqual(self.wiki.health()["unique_curated_sources"], 1)

    def test_source_unchanged_and_idempotency(self):
        before = digest(note(self.vault, self.source).read_bytes())
        receipt = self.commit_one()
        reused = self.wiki.commit(receipt["commit_key"], self.proposal())
        self.assertTrue(reused["reused"])
        self.assertEqual(before, digest(note(self.vault, self.source).read_bytes()))
        self.assertEqual(len(self.wiki._receipts()), 1)

    def test_same_version_new_prepare_reuses(self):
        first = self.commit_one()
        second = self.wiki.prepare(self.source, [self.target])
        self.assertEqual(first["commit_key"], second["commit_key"])
        self.assertTrue(second["reused"])

    def test_refresh_not_new_source(self):
        self.commit_one()
        note(self.vault, self.source).write_text("新增原文：计划测试，预算未知。", encoding="utf-8")
        tx = self.wiki.prepare(self.source, [self.target])
        self.assertEqual(tx["operation"], "refresh")
        self.wiki.commit(tx["token"], self.proposal(page("更新，但仍是计划", [self.source])))
        self.assertEqual(self.wiki.health()["unique_curated_sources"], 1)

    def test_source_version_conflict(self):
        tx = self.wiki.prepare(self.source, [self.target])
        note(self.vault, self.source).write_text("来源改了", encoding="utf-8")
        with self.assertRaisesRegex(WikiError, "SOURCE_CHANGED"):
            self.wiki.commit(tx["token"], self.proposal())
        self.assertFalse(note(self.vault, self.target).exists())

    def test_target_version_conflict_preserves_manual_edit(self):
        tx = self.wiki.prepare(self.source, [self.target])
        note(self.vault, self.target).write_text("人工新内容", encoding="utf-8")
        with self.assertRaisesRegex(WikiError, "TARGET_CHANGED"):
            self.wiki.commit(tx["token"], self.proposal())
        self.assertEqual(note(self.vault, self.target).read_text(encoding="utf-8"), "人工新内容")

    def test_rule_conflict(self):
        tx = self.wiki.prepare(self.source, [self.target])
        note(self.vault, "99-System/知识库规则.md").write_text("新版规则", encoding="utf-8")
        with self.assertRaisesRegex(WikiError, "RULE_CHANGED"):
            self.wiki.commit(tx["token"], self.proposal())

    def test_index_conflict(self):
        tx = self.wiki.prepare(self.source, [self.target])
        note(self.vault, "20-Wiki/知识索引.md").write_text("外部编辑", encoding="utf-8")
        with self.assertRaisesRegex(WikiError, "INDEX_CHANGED"):
            self.wiki.commit(tx["token"], self.proposal())

    def test_nonliteral_quote_rejected(self):
        tx = self.wiki.prepare(self.source, [self.target])
        p = self.proposal()
        p["quotes"] = ["已经上线且预算100"]
        with self.assertRaisesRegex(WikiError, "QUOTE_NOT_LITERAL"):
            self.wiki.commit(tx["token"], p)

    def test_crash_resume_and_changed_plan_rejection(self):
        tx = self.wiki.prepare(self.source, [self.target])
        p = self.proposal()
        with self.assertRaisesRegex(WikiError, "INJECTED_CRASH"):
            self.wiki.commit(tx["token"], p, fail_after=1)
        with self.assertRaisesRegex(WikiError, "DURABLE_PLAN_CHANGED"):
            self.wiki.commit(tx["token"], self.proposal("偷换方案"))
        self.assertEqual(self.wiki.commit(tx["token"], p)["verification"], "PASS")
        self.assertEqual(len(self.wiki._receipts()), 1)

    def test_recovery_preserves_external_edits(self):
        tx = self.wiki.prepare(self.source, [self.target])
        with self.assertRaises(WikiError): self.wiki.commit(tx["token"], self.proposal(), fail_after=1)
        note(self.vault, self.target).write_text("崩溃后人工改文", encoding="utf-8")
        with self.assertRaisesRegex(WikiError, "RECOVERY_CONFLICT"):
            self.wiki.commit(tx["token"], self.proposal())

    def test_busy_no_lock_stealing(self):
        with self.wiki.locked():
            with self.assertRaisesRegex(WikiError, "BUSY"):
                self.wiki.prepare(self.source, [self.target])

    def test_reader_and_proposer_cannot_curate(self):
        for role in ("reader", "proposer"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(WikiError, "ROLE_DENIED"):
                    Wiki(self.vault, role).prepare(self.source, [self.target])

    def test_paths(self):
        for bad in ("../outside.md", "/outside.md", "drive:bad.md", "a\\b.md", "a//b.md",
                    "a/../b.md", "a/NUL.md", "a/b. /c.md", "a/b.md:stream", ".wiki-state/config.md"):
            with self.subTest(path=bad):
                with self.assertRaises(WikiError): self.wiki.read(bad)

    def test_symlink_escape(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.vault / "20-Wiki" / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks needs OS permission")
        with self.assertRaises(WikiError):
            self.wiki.prepare(self.source, ["20-Wiki/escape/evil.md"])

    def test_unicode_pagination(self):
        text = "甲🙂乙丙"
        note(self.vault, self.source).write_text(text, encoding="utf-8")
        first = self.wiki.read(self.source, 0, 2)
        second = self.wiki.read(self.source, first["next_offset"], 2)
        self.assertEqual(first["text"] + second["text"], text)
        self.assertIsNone(second["next_offset"])

    def test_generated_and_system_rejected(self):
        with self.assertRaises(WikiError): self.wiki.prepare("40-输出/a.md", [self.target])
        with self.assertRaises(WikiError): self.wiki.prepare(self.source, ["99-System/规则.md"])

    def test_confirmation_received_not_adopted(self):
        self.commit_one()
        self.wiki.question("test-q", self.target, "是否仍是计划？")
        answer = self.wiki.answer("test-q", 1, "仍是计划", "测试用户现场原话")
        self.assertEqual(self.wiki._question("test-q")["status"], "received")
        self.assertTrue(self.wiki.answer("test-q", 1, "仍是计划", "重复")["reused"])
        with self.assertRaisesRegex(WikiError, "STALE_QUESTION"):
            self.wiki.answer("test-q", 2, "别的答案", "测试")
        with self.assertRaisesRegex(WikiError, "ANSWER_RECEIPT_MISMATCH"):
            self.wiki.resolve("test-q", answer["answer_id"], self.wiki._receipts()[0]["commit_key"])

    def test_answer_conflict_and_changed_target(self):
        self.commit_one()
        self.wiki.question("q", self.target, "问")
        answer = self.wiki.answer("q", 1, "答", "测试现场")
        with self.assertRaises(WikiError): self.wiki.answer("q", 1, "冲突答复", "另一次")
        note(self.vault, self.target).write_text("新内容", encoding="utf-8")
        with self.assertRaisesRegex(WikiError, "Question target changed"):
            self.wiki.prepare(answer["path"], [self.target], "topic_repair")

    def test_proposal_exclusive_creation(self):
        self.commit_one()
        self.wiki.question("q", self.target, "问")
        answer_id = digest(json.dumps(["q", 1, "答"], ensure_ascii=False, sort_keys=True, separators=(",", ":")))[:32]
        p = note(self.vault, "00-Inbox/待确认答复/" + answer_id + ".md")
        p.write_text("不要覆盖", encoding="utf-8")
        with self.assertRaises(FileExistsError): self.wiki.answer("q", 1, "答", "测试")
        self.assertEqual(p.read_text(encoding="utf-8"), "不要覆盖")

    def test_rollback_preserves_source_and_archives_output(self):
        before = note(self.vault, self.source).read_bytes()
        receipt = self.commit_one()
        self.wiki.rollback(receipt["commit_key"])
        self.assertEqual(before, note(self.vault, self.source).read_bytes())
        self.assertFalse(note(self.vault, self.target).exists())
        self.assertEqual(self.wiki.health()["unique_curated_sources"], 0)
        self.assertTrue(list((self.wiki.state / "rollback-archive").rglob("test.md")))

    def test_rollback_keeps_exact_original_bytes(self):
        original = "人工旧页\r\n第二行\r\n".encode("utf-8")
        note(self.vault, self.target).write_bytes(original)
        receipt = self.commit_one()
        self.wiki.rollback(receipt["commit_key"])
        self.assertEqual(note(self.vault, self.target).read_bytes(), original)

    def test_rollback_refuses_later_manual_edit(self):
        receipt = self.commit_one()
        note(self.vault, self.target).write_text("不要回滚掉我的编辑", encoding="utf-8")
        with self.assertRaisesRegex(WikiError, "ROLLBACK_CONFLICT"):
            self.wiki.rollback(receipt["commit_key"])
        self.assertEqual(note(self.vault, self.target).read_text(encoding="utf-8"), "不要回滚掉我的编辑")

    def test_query_is_readonly_and_not_absence_claim(self):
        self.commit_one()
        before = {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        query = Wiki(self.vault).query("不会出现的词")
        self.assertEqual(query["matched_total"], 0)
        self.assertFalse(query["semantic_answer"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()})

    def test_health_does_not_claim_semantics(self):
        self.commit_one()
        report = self.wiki.health()
        self.assertEqual(report["semantic_review"], "NOT_PERFORMED")
        self.assertFalse(report["whole_vault_accepted"])


if __name__ == "__main__":
    unittest.main()
