"""Tests for system/reference-host/curation_host.py.

These tests double as a usage manual: each one walks a realistic flow
against a throwaway vault + state dir.
"""
import json
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "system" / "reference-host"))
from curation_host import CurationHost, HostError


def make_host():
    tmp = Path(tempfile.mkdtemp())
    vault = tmp / "vault"
    (vault / "00-Inbox" / "随手放").mkdir(parents=True)
    (vault / "20-Wiki" / "实体").mkdir(parents=True)
    (vault / "99-System").mkdir(parents=True)
    (vault / "99-System" / "知识库规则.md").write_text(
        "---\ntitle: 知识库规则\nrule_version: public-v1\n---\n# 知识库规则\n",
        encoding="utf-8")
    state = tmp / "state"
    return CurationHost(vault, state), tmp


SOURCE_TEXT = "这是第一段来源正文，用于测试引用。\n第二段包含关键数字 5000 元。\n"


class ReferenceHostTest(unittest.TestCase):
    def setUp(self):
        self.host, self.tmp = make_host()
        self.vault = self.tmp / "vault"

    def tearDown(self):
        self.host.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _register(self, source_id="src-001", text=SOURCE_TEXT,
                  subdir="00-Inbox/随手放"):
        p = self.vault / subdir / "note.md"
        p.write_text(text, encoding="utf-8")
        rel = f"{subdir}/note.md"
        return self.host.source_register(source_id, "note", [rel],
                                         purpose="test"), rel

    def _full_read(self, host, token, source_id):
        out = []
        offset, limit = 0, 10
        while True:
            r = host.source_read(token, source_id, offset=offset, limit=limit)
            out.append(r["text"])
            if r["next_offset"] is None:
                break
            offset = r["next_offset"]
        return "".join(out)

    # -- leases ------------------------------------------------------
    def test_acquire_release_busy(self):
        a = self.host.writer_acquire("curator-1", "demo")
        self.assertEqual(a["status"], "ACQUIRED")
        b = self.host.writer_acquire("curator-2", "demo")
        self.assertEqual(b["status"], "BUSY")
        self.assertEqual(b["owner"], "curator-1")
        # only the owner may release
        with self.assertRaises(HostError):
            self.host.writer_release(a["token"], "curator-2")
        self.host.writer_release(a["token"], "curator-1")
        c = self.host.writer_acquire("curator-2", "demo")
        self.assertEqual(c["status"], "ACQUIRED")

    def test_expired_lease_can_be_reacquired(self):
        a = self.host.writer_acquire("curator-1", "demo", ttl=1)
        time.sleep(1.1)
        b = self.host.writer_acquire("curator-2", "demo")
        self.assertEqual(b["status"], "ACQUIRED")

    def test_renew_extends_own_lease(self):
        a = self.host.writer_acquire("curator-1", "demo", ttl=100)
        r = self.host.writer_renew(a["token"], "curator-1", ttl=600)
        self.assertGreater(r["expires_at"], a["expires_at"])
        with self.assertRaises(HostError):
            self.host.writer_renew(a["token"], "curator-2")

    # -- pending -----------------------------------------------------
    def test_pending_excludes_logs(self):
        (self.vault / "90-Logs").mkdir(exist_ok=True)
        (self.vault / "90-Logs" / "x.md").write_text("log", encoding="utf-8")
        with self.assertRaises(HostError):
            self.host.source_register("src-log", "note", ["90-Logs/x.md"])
        self._register()
        pending = self.host.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source_id"], "src-001")

    # -- happy path --------------------------------------------------
    def test_prepare_stage_commit_verify(self):
        _, rel = self._register()
        old = self.vault / "20-Wiki" / "实体" / "主题.md"
        old.write_text("# 主题\n\n旧结论。\n", encoding="utf-8")
        old_rel = "20-Wiki/实体/主题.md"

        tok = self.host.writer_acquire("curator-1", "demo")["token"]
        prep = self.host.prepare(tok, "src-001", old_pages=[old_rel])
        self.assertEqual(prep["rule_version"], "public-v1")
        self.assertEqual(prep["source"]["source_id"], "src-001")

        full = self._full_read(self.host, tok, "src-001")
        self.assertIn("5000", full)
        ctx = self.host.context_read(tok, old_rel)
        self.assertIn("旧结论", ctx["text"])

        staged = self.host.stage(tok)
        self.assertFalse(staged["counts_as_success"])
        self.assertTrue(any(s["kind"] == "source"
                            for s in staged["staged_ranges"]))

        proposal = {
            "source_id": "src-001",
            "operation": "new",
            "quotes": [{"path": rel, "text": "第二段包含关键数字 5000 元。"}],
            "updates": [{"path": "20-Wiki/实体/新主题.md",
                         "mode": "create",
                         "content": "# 新主题\n\n关键数字 5000 元。\n"}],
            "overview_decision": {"action": "unchanged",
                                  "reason": "单主题新增"},
        }
        res = self.host.commit(tok, proposal)
        self.assertEqual(res["status"], "committed")
        self.assertTrue(all(r["match"] for r in res["read_back"]))
        self.assertTrue((self.vault / "20-Wiki" / "实体" / "新主题.md").exists())

        v = self.host.verify(res["commit_key"])
        self.assertTrue(v["verified"])
        self.assertTrue(all(c["pass"] for c in v["checks"]))

        # idempotent recovery: same token + same params -> same commit_key
        res2 = self.host.commit(tok, proposal)
        self.assertTrue(res2["recovered"])
        self.assertEqual(res2["commit_key"], res["commit_key"])

        self.host.writer_release(tok, "curator-1")

    # -- guards ------------------------------------------------------
    def test_commit_rejects_unstaged_quote(self):
        self._register()
        tok = self.host.writer_acquire("curator-1", "demo")["token"]
        self.host.prepare(tok, "src-001")
        # read only the first 10 chars; the quote below was never staged
        self.host.source_read(tok, "src-001", offset=0, limit=10)
        proposal = {"source_id": "src-001", "operation": "new",
                    "quotes": [{"path": "x", "text": "第二段包含关键数字 5000 元。"}],
                    "updates": []}
        with self.assertRaises(HostError):
            self.host.commit(tok, proposal)

    def test_commit_rejects_changed_source(self):
        _, rel = self._register()
        tok = self.host.writer_acquire("curator-1", "demo")["token"]
        self.host.prepare(tok, "src-001")
        # source modified after prepare -> snapshot mismatch
        (self.vault / rel).write_text("被篡改的来源。\n", encoding="utf-8")
        proposal = {"source_id": "src-001", "operation": "new",
                    "quotes": [], "updates": []}
        with self.assertRaises(HostError):
            self.host.commit(tok, proposal)

    def test_commit_rejects_old_page_conflict(self):
        _, rel = self._register()
        old = self.vault / "20-Wiki" / "实体" / "主题.md"
        old.write_text("# 主题\n\n旧结论。\n", encoding="utf-8")
        old_rel = "20-Wiki/实体/主题.md"
        tok = self.host.writer_acquire("curator-1", "demo")["token"]
        self.host.prepare(tok, "src-001", old_pages=[old_rel])
        self._full_read(self.host, tok, "src-001")
        old.write_text("# 主题\n\n被别人改过的结论。\n", encoding="utf-8")
        proposal = {"source_id": "src-001", "operation": "update",
                    "quotes": [], "updates": []}
        with self.assertRaises(HostError):
            self.host.commit(tok, proposal)

    def test_source_read_must_be_contiguous(self):
        self._register()
        tok = self.host.writer_acquire("curator-1", "demo")["token"]
        self.host.prepare(tok, "src-001")
        self.host.source_read(tok, "src-001", offset=0, limit=5)
        with self.assertRaises(HostError):
            self.host.source_read(tok, "src-001", offset=99, limit=5)

    def test_context_read_requires_declaration(self):
        self._register()
        tok = self.host.writer_acquire("curator-1", "demo")["token"]
        self.host.prepare(tok, "src-001")  # no old pages declared
        with self.assertRaises(HostError):
            self.host.context_read(tok, "99-System/知识库规则.md")

    def test_verify_detects_tampering(self):
        _, rel = self._register()
        tok = self.host.writer_acquire("curator-1", "demo")["token"]
        self.host.prepare(tok, "src-001")
        self._full_read(self.host, tok, "src-001")
        self.host.stage(tok)
        proposal = {"source_id": "src-001", "operation": "new",
                    "quotes": [{"path": rel, "text": "第二段包含关键数字 5000 元。"}],
                    "updates": [{"path": "20-Wiki/实体/新主题.md",
                                 "mode": "create",
                                 "content": "# 新主题\n"}]}
        res = self.host.commit(tok, proposal)
        # tamper after commit
        (self.vault / "20-Wiki" / "实体" / "新主题.md").write_text(
            "# 被篡改\n", encoding="utf-8")
        v = self.host.verify(res["commit_key"])
        self.assertFalse(v["verified"])

    def _proposal(self):
        _, rel = self._register()
        token = self.host.writer_acquire("curator-1")["token"]
        self.host.prepare(token, "src-001")
        self._full_read(self.host, token, "src-001")
        self.host.stage(token)
        return token, {"source_id": "src-001", "quotes": [{"path": rel, "text": "关键数字 5000 元"}],
                       "updates": [{"path": "20-Wiki/实体/新主题.md", "mode": "create", "content": "# 新主题\n"}]}

    def test_register_same_version_does_not_requeue_done_source(self):
        token, proposal = self._proposal()
        self.host.commit(token, proposal)
        result = self._register()[0]
        self.assertTrue(result["reused"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.host.pending(), [])

    def test_generated_material_and_empty_source_are_rejected(self):
        for paths in ([], ["20-Wiki/别名表.md"], ["99-System/知识库规则.md"]):
            with self.subTest(paths=paths), self.assertRaises(HostError):
                self.host.source_register("bad", "note", paths)

    def test_rule_change_and_source_mix_rejected(self):
        token, proposal = self._proposal()
        with self.assertRaises(HostError):
            self.host.source_read(token, "another-source")
        rules = self.vault / "99-System/知识库规则.md"
        rules.write_text("rule_version: changed\n", encoding="utf-8")
        with self.assertRaises(HostError):
            self.host.commit(token, proposal)
        self.assertFalse((self.vault / proposal["updates"][0]["path"]).exists())

    def test_commit_requires_explicit_stage_and_complete_source(self):
        self._register()
        token = self.host.writer_acquire("curator-1")["token"]
        self.host.prepare(token, "src-001")
        self.host.source_read(token, "src-001", limit=5)
        with self.assertRaises(HostError):
            self.host.commit(token, {"source_id": "src-001", "updates": []})
        self.host.stage(token)
        with self.assertRaises(HostError):
            self.host.commit(token, {"source_id": "src-001", "updates": []})

    def test_cannot_write_rules_originals_or_undeclared_old_pages(self):
        token, proposal = self._proposal()
        for target in ("99-System/知识库规则.md", "00-Inbox/随手放/note.md", "20-Wiki/别名表.md"):
            with self.subTest(target=target), self.assertRaises(HostError):
                self.host.commit(token, {**proposal, "updates": [{"path": target, "mode": "update", "content": "bad"}]})
        target = "20-Wiki/实体/旧页.md"
        (self.vault / target).write_text("old", encoding="utf-8")
        with self.assertRaises(HostError):
            self.host.commit(token, {**proposal, "updates": [{"path": target, "mode": "update", "content": "bad"}]})

    def test_quote_must_match_its_own_path(self):
        token, proposal = self._proposal()
        proposal["quotes"][0]["path"] = "00-Inbox/other.md"
        with self.assertRaises(HostError):
            self.host.commit(token, proposal)

    def test_multifile_source_quotes_and_plus_in_filename(self):
        paths = ["00-Inbox/随手放/a+b.md", "00-Inbox/随手放/c.md"]
        for path, body in zip(paths, ["第一份", "第二份"]):
            (self.vault / path).write_text(body, encoding="utf-8")
        self.host.source_register("multi", "note", paths)
        token = self.host.writer_acquire("curator-1")["token"]
        self.host.prepare(token, "multi")
        self._full_read(self.host, token, "multi")
        self.host.stage(token)
        result = self.host.commit(token, {"source_id": "multi", "quotes": [{"path": paths[1], "text": "第二份"}], "updates": []})
        self.assertTrue(self.host.verify(result["commit_key"])["verified"])

    def test_partial_write_recovers_after_reopening_same_state(self):
        token, proposal = self._proposal()
        proposal["updates"].append({"path": "20-Wiki/实体/第二页.md", "mode": "create", "content": "# 第二页\n"})
        import os
        real_link = os.link
        calls = []
        def fail_second(src, dst):
            calls.append(str(dst))
            if len(calls) == 2:
                raise OSError("injected second write failure")
            return real_link(src, dst)
        with patch("curation_host.os.link", side_effect=fail_second), self.assertRaises(OSError):
            self.host.commit(token, proposal)
        key = self.host.db.execute("SELECT commit_key FROM commits").fetchone()[0]
        self.assertFalse(self.host.verify(key)["verified"])
        self.host.db.close()
        self.host = CurationHost(self.vault, self.tmp / "state")
        result = self.host.commit(token, proposal)
        self.assertEqual(result["commit_key"], key)
        self.assertTrue(result["recovered"])
        self.assertTrue(self.host.verify(key)["verified"])

    def test_simultaneous_connections_only_grant_one_lease(self):
        import concurrent.futures
        import threading
        gate = threading.Barrier(2)
        def acquire(owner):
            host = CurationHost(self.vault, self.tmp / "state")
            try:
                gate.wait()
                return host.writer_acquire(owner)["status"]
            finally:
                host.db.close()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(acquire, ["one", "two"]))
        self.assertCountEqual(results, ["ACQUIRED", "BUSY"])

    def test_path_traversal_rejected(self):
        with self.assertRaises(HostError):
            self.host._safe("../evil.md")
        with self.assertRaises(HostError):
            self.host._safe("/abs/path.md")
        with self.assertRaises(HostError):
            self.host._safe("20-Wiki\\实体\\x.md")
        with self.assertRaises(HostError):
            self.host._safe("20-Wiki/实体/x.exe")

    def test_lint_save_and_list(self):
        p = self.vault / "20-Wiki" / "实体" / "主题.md"
        text = "# 主题\n\n" + "正文" * 100 + "\n"
        p.write_text(text, encoding="utf-8")
        rel = "20-Wiki/实体/主题.md"
        r = self.host.lint_save(rel, 0, 50, "normal",
                                evidence_paths=["00-Inbox/随手放/note.md"])
        self.assertEqual(r["status"], "SAVED")
        with self.assertRaises(HostError):
            self.host.lint_save(rel, 0, 50, "weird-kind", evidence_paths=[])
        with self.assertRaises(HostError):
            self.host.lint_save(rel, 0, len(text) + 999, "normal",
                                evidence_paths=[])
        reports = self.host.lint_reports(rel)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["coverage_kind"], "normal")


if __name__ == "__main__":
    unittest.main()
