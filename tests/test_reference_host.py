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
