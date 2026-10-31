#!/usr/bin/env python3
"""Minimal platform-independent reference host for the curation_* contract.

Implements docs/04-tool-contract.md ("curation 扩展") with nothing but the
Python standard library: SQLite holds leases, the pending queue, the durable
journal and page versions; the vault itself is plain Markdown files on disk.

This host is the *deterministic half* of the system: permissions, versions,
transactions, the journal, the ledger and read-back. The *model half*
(understanding sources, proposing changes) is the Agent's job, driven by the
ingest skill. Pair this host with a scheduled Agent run (see cron.example)
and you have the "24h worker" without any vendor platform.

What this host is NOT (by design, see README.md):
  - no authentication / multi-user isolation: single trusted operator only.
    Production must enforce identity at the gateway, per the contract.
  - no MCP transport: the Python API below maps 1:1 to tool calls; the
    newcomer's MCP host adapts these functions.
  - no semantic search: reads are exact file/offset operations.

Stdlib only: sqlite3, hashlib, json, pathlib, secrets, time, argparse.
Python 3.11+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
  token      TEXT PRIMARY KEY,
  owner      TEXT NOT NULL,
  purpose    TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  source_id      TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,
  paths_json     TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  purpose        TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT 'pending',
  breakpoint_json TEXT,
  updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reads (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  token      TEXT NOT NULL,
  path       TEXT NOT NULL,
  kind       TEXT NOT NULL,
  range_start INTEGER NOT NULL,
  range_end   INTEGER NOT NULL,
  sha256     TEXT NOT NULL,
  read_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS commits (
  commit_key    TEXT PRIMARY KEY,
  token         TEXT NOT NULL,
  source_id     TEXT NOT NULL,
  operation     TEXT NOT NULL,
  params_sha256 TEXT NOT NULL,
  status        TEXT NOT NULL,
  journal_json  TEXT NOT NULL,
  created_at    REAL NOT NULL,
  UNIQUE(token, params_sha256)
);
CREATE TABLE IF NOT EXISTS page_versions (
  path       TEXT PRIMARY KEY,
  sha256     TEXT NOT NULL,
  commit_key TEXT,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lint_reports (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  path          TEXT NOT NULL,
  range_start   INTEGER NOT NULL,
  range_end     INTEGER NOT NULL,
  coverage_kind TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Sources may never be registered from these prefixes (contract: pending
# excludes logs and generated material).
EXCLUDED_SOURCE_PREFIXES = ("90-Logs/", "00-Test/")
ALLOWED_EXTENSIONS = {".md"}
DEFAULT_LEASE_TTL = 300  # seconds


class HostError(Exception):
    pass


def _utcnow() -> float:
    return time.time()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CurationHost:
    """Reference implementation of the curation_* tool contract."""

    def __init__(self, vault_root: str | Path, state_dir: str | Path):
        self.vault = Path(vault_root).resolve()
        self.state = Path(state_dir).resolve()
        if not self.vault.is_dir():
            raise HostError(f"vault root is not a directory: {self.vault}")
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "backups").mkdir(exist_ok=True)
        self.db_path = self.state / "curation.db"
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------------
    # path safety (mirrors the contract: relative only, no traversal)
    # ------------------------------------------------------------------
    def _safe(self, path: str) -> Path:
        if not isinstance(path, str) or not path:
            raise HostError("empty path")
        if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
            raise HostError(f"absolute path rejected: {path}")
        if "\\" in path or ".." in Path(path).parts:
            raise HostError(f"path traversal rejected: {path}")
        if ":" in path:  # Windows ADS / device names
            raise HostError(f"device/ADS path rejected: {path}")
        rel = Path(path).as_posix()
        if Path(rel).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HostError(f"unexpected extension rejected: {path}")
        full = (self.vault / rel).resolve()
        if not str(full).startswith(str(self.vault) + os.sep) and full != self.vault:
            raise HostError(f"path escapes vault: {path}")
        if full.is_symlink() or any(p.is_symlink() for p in full.parents
                                    if str(p).startswith(str(self.vault))):
            raise HostError(f"symlink rejected: {path}")
        return full

    def _read_text(self, path: str) -> str:
        full = self._safe(path)
        if not full.is_file():
            raise HostError(f"note not found: {path}")
        return full.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # leases: writer_acquire / writer_release / writer_renew
    # ------------------------------------------------------------------
    def _purge_expired_leases(self, now: float) -> None:
        self.db.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
        self.db.commit()

    def writer_acquire(self, owner: str, purpose: str = "",
                       ttl: int = DEFAULT_LEASE_TTL) -> dict:
        now = _utcnow()
        self._purge_expired_leases(now)
        row = self.db.execute(
            "SELECT token, owner, expires_at FROM leases LIMIT 1").fetchone()
        if row:
            return {"status": "BUSY", "owner": row["owner"],
                    "expires_at": row["expires_at"],
                    "hint": "single writer only; retry after expiry, never "
                            "acquire over a live lease"}
        token = "w_" + secrets.token_hex(16)
        self.db.execute(
            "INSERT INTO leases(token, owner, purpose, created_at, expires_at)"
            " VALUES(?,?,?,?,?)",
            (token, owner, purpose, now, now + ttl))
        self.db.commit()
        return {"status": "ACQUIRED", "token": token, "owner": owner,
                "expires_at": now + ttl}

    def _require_lease(self, token: str) -> sqlite3.Row:
        now = _utcnow()
        self._purge_expired_leases(now)
        row = self.db.execute(
            "SELECT * FROM leases WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HostError("no live lease for token; acquire first")
        return row

    def writer_release(self, token: str, owner: str) -> dict:
        row = self.db.execute(
            "SELECT owner FROM leases WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HostError("unknown token")
        if row["owner"] != owner:
            raise HostError("only the lease owner may release it")
        self.db.execute("DELETE FROM leases WHERE token = ?", (token,))
        self.db.commit()
        return {"status": "RELEASED"}

    def writer_renew(self, token: str, owner: str,
                     ttl: int = DEFAULT_LEASE_TTL) -> dict:
        row = self._require_lease(token)
        if row["owner"] != owner:
            raise HostError("only the lease owner may renew it")
        expires = _utcnow() + ttl
        self.db.execute("UPDATE leases SET expires_at = ? WHERE token = ?",
                        (expires, token))
        self.db.commit()
        return {"status": "RENEWED", "expires_at": expires}

    # ------------------------------------------------------------------
    # pending / status
    # ------------------------------------------------------------------
    def _source_hash(self, paths: list[str]) -> str:
        h = hashlib.sha256()
        for p in sorted(paths):
            h.update(_sha256_text(self._read_text(p)).encode())
            h.update(b"\x00")
        return h.hexdigest()

    def source_register(self, source_id: str, kind: str, paths: list[str],
                        purpose: str = "") -> dict:
        for p in paths:
            rel = Path(p).as_posix()
            if rel.startswith(EXCLUDED_SOURCE_PREFIXES):
                raise HostError(
                    f"source under excluded prefix rejected: {p} "
                    "(logs/generated material never enter the queue)")
            self._safe(p)  # validates + requires existence via _source_hash
        digest = self._source_hash(paths)
        now = _utcnow()
        self.db.execute(
            "INSERT OR REPLACE INTO sources(source_id, kind, paths_json,"
            " content_sha256, purpose, status, updated_at)"
            " VALUES(?,?,?,?,?,'pending',?)",
            (source_id, kind, json.dumps(paths, ensure_ascii=False),
             digest, purpose, now))
        self.db.commit()
        return {"source_id": source_id, "status": "pending",
                "content_sha256": digest, "paths": paths}

    def pending(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT source_id, kind, paths_json, content_sha256, purpose,"
            " status, breakpoint_json, updated_at FROM sources"
            " WHERE status IN ('pending','in_progress')"
            " ORDER BY updated_at").fetchall()
        return [dict(r) for r in rows]

    def source_status(self, source_id: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        if not row:
            raise HostError(f"unknown source_id: {source_id}")
        return dict(row)

    # ------------------------------------------------------------------
    # prepare / context_read / source_read / stage
    # ------------------------------------------------------------------
    def _rule_version(self) -> str:
        try:
            text = self._read_text("99-System/知识库规则.md")
        except HostError:
            return "unknown"
        m = re.search(r"(?m)^rule_version:\s*(\S+)", text)
        return m.group(1) if m else "unknown"

    def _slice(self, text: str, offset: int, limit: int) -> tuple[str, int | None]:
        # Unicode character offsets, per the contract.
        if offset < 0 or limit <= 0:
            raise HostError("offset must be >= 0 and limit > 0")
        total = len(text)
        if offset >= total:
            return "", None
        end = min(total, offset + limit)
        nxt = end if end < total else None
        return text[offset:end], nxt

    def _record_read(self, token: str, path: str, kind: str,
                     start: int, end: int, sha256: str) -> None:
        self.db.execute(
            "INSERT INTO reads(token, path, kind, range_start, range_end,"
            " sha256, read_at) VALUES(?,?,?,?,?,?,?)",
            (token, path, kind, start, end, sha256, _utcnow()))
        self.db.commit()

    def prepare(self, token: str, source_id: str,
                old_pages: list[str] | None = None) -> dict:
        self._require_lease(token)
        src = self.source_status(source_id)
        if src["status"] not in ("pending", "in_progress"):
            raise HostError(f"source not open for prepare: {src['status']}")
        paths = json.loads(src["paths_json"])
        # Snapshot: re-hash now; prepare binds this exact version.
        digest = self._source_hash(paths)
        if digest != src["content_sha256"]:
            raise HostError("source changed since registration; re-register")
        total = sum(len(self._read_text(p)) for p in paths)
        contexts = []
        for p in old_pages or []:
            text = self._read_text(p)
            contexts.append({"path": p, "sha256": _sha256_text(text),
                             "chars": len(text)})
        self.db.execute(
            "UPDATE sources SET status='in_progress', updated_at=? "
            "WHERE source_id=?", (_utcnow(), source_id))
        for c in contexts:
            self.db.execute(
                "INSERT INTO reads(token, path, kind, range_start, range_end,"
                " sha256, read_at)"
                " VALUES(?, ?, 'context-declared', 0, 0, ?, ?)",
                (token, c["path"], c["sha256"], _utcnow()))
        self.db.commit()
        return {"token": token,
                "source": {"source_id": source_id,
                           "content_sha256": digest, "paths": paths},
                "source_read_range": [0, total],
                "contexts": contexts,
                "rule_version": self._rule_version()}

    def source_read(self, token: str, source_id: str,
                    offset: int = 0, limit: int = 2500) -> dict:
        self._require_lease(token)
        src = self.source_status(source_id)
        paths = json.loads(src["paths_json"])
        # Concatenate declared source paths as one logical source.
        combined = "".join(self._read_text(p) for p in paths)
        # Enforce contiguous ranges: each read continues where the last ended.
        last = self.db.execute(
            "SELECT range_end FROM reads WHERE token=? AND kind='source'"
            " ORDER BY id DESC LIMIT 1", (token,)).fetchone()
        expected = last["range_end"] if last else 0
        if offset != expected:
            raise HostError(
                f"source reads must be contiguous: expected offset "
                f"{expected}, got {offset}")
        if self._source_hash(paths) != src["content_sha256"]:
            raise HostError("source snapshot changed mid-transaction")
        text, nxt = self._slice(combined, offset, limit)
        end = offset + len(text)
        self._record_read(token, paths[0] if len(paths) == 1 else "+".join(paths),
                          "source", offset, end, src["content_sha256"])
        return {"text": text, "range": [offset, end],
                "next_offset": nxt, "total_chars": len(combined)}

    def context_read(self, token: str, path: str,
                     offset: int = 0, limit: int = 2500) -> dict:
        self._require_lease(token)
        # Only pages declared in this transaction's prepare() may be read as
        # old-page context: never impersonate a source read.
        declared = self.db.execute(
            "SELECT 1 FROM reads WHERE token=? AND kind='context-declared'"
            " AND path=?", (token, path)).fetchone()
        if not declared:
            raise HostError(f"path not declared in prepare(): {path}")
        text = self._read_text(path)
        chunk, nxt = self._slice(text, offset, limit)
        end = offset + len(chunk)
        self._record_read(token, path, "old_page", offset, end,
                          _sha256_text(text))
        return {"text": chunk, "range": [offset, end],
                "next_offset": nxt, "sha256": _sha256_text(text)}

    def stage(self, token: str) -> dict:
        """Checkpoint accumulated reading evidence. Never counts as success."""
        self._require_lease(token)
        rows = self.db.execute(
            "SELECT path, kind, range_start, range_end FROM reads"
            " WHERE token=? AND kind IN ('source','old_page')"
            " ORDER BY id", (token,)).fetchall()
        # Validate contiguity per (path, kind).
        seen: dict[tuple[str, str], int] = {}
        for r in rows:
            key = (r["path"], r["kind"])
            expect = seen.get(key, 0)
            if r["range_start"] != expect:
                raise HostError(
                    f"non-contiguous staged range for {r['path']}: "
                    f"expected start {expect}")
            seen[key] = r["range_end"]
        return {"staged_ranges": [
            {"path": p, "kind": k, "read_chars": e}
            for (p, k), e in seen.items()],
            "counts_as_success": False}

    # ------------------------------------------------------------------
    # commit: pre-write verification, durable journal, idempotent recovery
    # ------------------------------------------------------------------
    def _staged_source_text(self, token: str) -> str:
        rows = self.db.execute(
            "SELECT path, range_start, range_end FROM reads"
            " WHERE token=? AND kind='source' ORDER BY id",
            (token,)).fetchall()
        # Re-read the exact staged ranges from disk (never trust memory).
        parts = []
        by_path: dict[str, list[tuple[int, int]]] = {}
        for r in rows:
            by_path.setdefault(r["path"], []).append(
                (r["range_start"], r["range_end"]))
        for path, ranges in by_path.items():
            if "+" in path:  # multi-file source marker; ranges are combined
                continue
            text = self._read_text(path)
            for s, e in ranges:
                parts.append(text[s:e])
        return "".join(parts)

    def commit(self, token: str, proposal: dict) -> dict:
        lease = self._require_lease(token)
        source_id = proposal.get("source_id")
        operation = proposal.get("operation", "new")
        if not source_id:
            raise HostError("proposal needs source_id")
        src = self.source_status(source_id)
        params_sha = _sha256_text(_canonical(proposal))

        # Idempotent recovery: same token + same params -> same result.
        existing = self.db.execute(
            "SELECT commit_key, status, journal_json FROM commits"
            " WHERE token=? AND params_sha256=?",
            (token, params_sha)).fetchone()
        if existing and existing["status"] == "committed":
            journal = json.loads(existing["journal_json"])
            return {"commit_key": existing["commit_key"],
                    "status": "committed",
                    "recovered": True,
                    "written": journal.get("written", [])}

        paths = json.loads(src["paths_json"])
        if self._source_hash(paths) != src["content_sha256"]:
            raise HostError("source snapshot changed mid-transaction; "
                            "re-prepare")

        # Old-page optimistic concurrency: versions must match prepare().
        declared = self.db.execute(
            "SELECT path, sha256 FROM reads WHERE token=?"
            " AND kind='context-declared'", (token,)).fetchall()
        for d in declared:
            current = _sha256_text(self._read_text(d["path"]))
            if current != d["sha256"]:
                raise HostError(
                    f"old page changed since prepare(): {d['path']}")

        # Literal evidence: every quote must appear in staged source text.
        staged = self._staged_source_text(token)
        for q in proposal.get("quotes", []):
            if q.get("text", "") not in staged:
                raise HostError(
                    "quote not found in staged source reads; stage the "
                    f"evidence first: {q.get('path')}")

        commit_key = ("cmt_" + time.strftime("%Y%m%dT%H%M%S", time.gmtime())
                      + "_" + secrets.token_hex(4))
        journal: dict = {
            "commit_key": commit_key,
            "owner": lease["owner"],
            "source_id": source_id,
            "operation": operation,
            "rule_version": self._rule_version(),
            "staged_ranges": self.stage(token)["staged_ranges"],
            "quotes": proposal.get("quotes", []),
            "overview_decision": proposal.get("overview_decision"),
            "plan": proposal.get("updates", []),
            "steps_completed": [],
            "written": [],
            "backups": [],
        }
        self.db.execute(
            "INSERT INTO commits(commit_key, token, source_id, operation,"
            " params_sha256, status, journal_json, created_at)"
            " VALUES(?,?,?,?,?,'in_progress',?,?)",
            (commit_key, token, source_id, operation, params_sha,
             json.dumps(journal, ensure_ascii=False), _utcnow()))
        self.db.commit()

        def save_journal(status: str) -> None:
            journal["status"] = status
            self.db.execute(
                "UPDATE commits SET status=?, journal_json=? WHERE commit_key=?",
                (status, json.dumps(journal, ensure_ascii=False), commit_key))
            self.db.commit()

        try:
            for upd in proposal.get("updates", []):
                path = upd["path"]
                mode = upd.get("mode", "create")
                full = self._safe(path)
                if mode == "create":
                    if full.exists():
                        raise HostError(f"create target exists: {path}")
                elif mode == "update":
                    if not full.exists():
                        raise HostError(f"update target missing: {path}")
                    # Backup before replace (contract: 写入计划/备份).
                    backup = (self.state / "backups" / commit_key /
                              (path.replace("/", "__")))
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(full.read_bytes())
                    journal["backups"].append(
                        {"path": path, "backup": str(backup)})
                else:
                    raise HostError(f"unknown update mode: {mode}")
                # Atomic single-file replace.
                tmp = full.parent / (full.name + ".tmp")
                tmp.write_text(upd["content"], encoding="utf-8")
                os.replace(tmp, full)
                digest = _sha256_text(upd["content"])
                journal["written"].append({"path": path, "sha256": digest})
                journal["steps_completed"].append(f"write:{path}")
                self.db.execute(
                    "INSERT OR REPLACE INTO page_versions(path, sha256,"
                    " commit_key, updated_at) VALUES(?,?,?,?)",
                    (path, digest, commit_key, _utcnow()))
                save_journal("in_progress")
        except Exception:
            save_journal("failed")
            raise

        self.db.execute(
            "UPDATE sources SET status='done', updated_at=? WHERE source_id=?",
            (_utcnow(), source_id))
        self.db.commit()
        save_journal("committed")

        # Read-back: re-read every written file from disk.
        read_back = []
        for w in journal["written"]:
            actual = _sha256_text(self._read_text(w["path"]))
            read_back.append({"path": w["path"],
                              "match": actual == w["sha256"]})
        return {"commit_key": commit_key, "status": "committed",
                "written": journal["written"], "read_back": read_back}

    # ------------------------------------------------------------------
    # verify: never trust self-reported outputs, re-read from disk
    # ------------------------------------------------------------------
    def verify(self, commit_key: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM commits WHERE commit_key = ?",
            (commit_key,)).fetchone()
        if not row:
            raise HostError(f"unknown commit_key: {commit_key}")
        journal = json.loads(row["journal_json"])
        checks = []
        ok = True
        for w in journal.get("written", []):
            try:
                actual = _sha256_text(self._read_text(w["path"]))
                match = actual == w["sha256"]
            except HostError:
                match = False
            checks.append({"check": "file_sha", "path": w["path"],
                           "pass": match})
            ok = ok and match
        src = self.db.execute(
            "SELECT status FROM sources WHERE source_id=?",
            (journal.get("source_id"),)).fetchone()
        src_done = bool(src) and src["status"] == "done"
        checks.append({"check": "source_done", "pass": src_done})
        ok = ok and src_done
        journal_ok = row["status"] == "committed"
        checks.append({"check": "journal_committed", "pass": journal_ok})
        ok = ok and journal_ok
        return {"commit_key": commit_key, "verified": ok, "checks": checks}

    # ------------------------------------------------------------------
    # lint_prepare / lint_save
    # ------------------------------------------------------------------
    def lint_save(self, path: str, range_start: int, range_end: int,
                  coverage_kind: str,
                  evidence_paths: list[str]) -> dict:
        if coverage_kind not in ("normal", "temporal"):
            raise HostError("coverage_kind must be normal|temporal")
        text = self._read_text(path)  # validates path
        if not (0 <= range_start < range_end <= len(text)):
            raise HostError("lint range outside actual page text")
        for e in evidence_paths:
            self._safe(e)
        self.db.execute(
            "INSERT INTO lint_reports(path, range_start, range_end,"
            " coverage_kind, evidence_json, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (path, range_start, range_end, coverage_kind,
             json.dumps(evidence_paths, ensure_ascii=False), _utcnow()))
        self.db.commit()
        return {"status": "SAVED", "path": path,
                "range": [range_start, range_end],
                "coverage_kind": coverage_kind}

    def lint_reports(self, path: str | None = None) -> list[dict]:
        q = "SELECT * FROM lint_reports ORDER BY id"
        args: tuple = ()
        if path:
            q = "SELECT * FROM lint_reports WHERE path=? ORDER BY id"
            args = (path,)
        return [dict(r) for r in self.db.execute(q, args).fetchall()]


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _host(args) -> CurationHost:
    return CurationHost(args.vault, args.state)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Minimal curation_* reference host")
    ap.add_argument("--vault", required=True, help="vault root directory")
    ap.add_argument("--state", required=True, help="host state directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("acquire"); p.add_argument("--owner", required=True)
    p.add_argument("--purpose", default=""); p.add_argument("--ttl", type=int, default=DEFAULT_LEASE_TTL)
    p = sub.add_parser("release"); p.add_argument("--token", required=True); p.add_argument("--owner", required=True)
    p = sub.add_parser("renew"); p.add_argument("--token", required=True); p.add_argument("--owner", required=True)
    p = sub.add_parser("register-source"); p.add_argument("--id", required=True); p.add_argument("--kind", default="note")
    p.add_argument("--paths", nargs="+", required=True); p.add_argument("--purpose", default="")
    p = sub.add_parser("pending")
    p = sub.add_parser("prepare"); p.add_argument("--token", required=True); p.add_argument("--id", required=True)
    p.add_argument("--old-pages", nargs="*", default=[])
    p = sub.add_parser("source-read"); p.add_argument("--token", required=True); p.add_argument("--id", required=True)
    p.add_argument("--offset", type=int, default=0); p.add_argument("--limit", type=int, default=2500)
    p = sub.add_parser("context-read"); p.add_argument("--token", required=True); p.add_argument("--path", required=True)
    p.add_argument("--offset", type=int, default=0); p.add_argument("--limit", type=int, default=2500)
    p = sub.add_parser("stage"); p.add_argument("--token", required=True)
    p = sub.add_parser("commit"); p.add_argument("--token", required=True); p.add_argument("--proposal", required=True,
        help="JSON file with source_id/operation/quotes/updates/overview_decision")
    p = sub.add_parser("verify"); p.add_argument("--commit-key", required=True)
    p = sub.add_parser("lint-save"); p.add_argument("--path", required=True)
    p.add_argument("--start", type=int, required=True); p.add_argument("--end", type=int, required=True)
    p.add_argument("--kind", default="normal"); p.add_argument("--evidence", nargs="*", default=[])

    args = ap.parse_args(argv)
    host = _host(args)
    try:
        if args.cmd == "acquire":
            out = host.writer_acquire(args.owner, args.purpose, args.ttl)
        elif args.cmd == "release":
            out = host.writer_release(args.token, args.owner)
        elif args.cmd == "renew":
            out = host.writer_renew(args.token, args.owner)
        elif args.cmd == "register-source":
            out = host.source_register(args.id, args.kind, args.paths, args.purpose)
        elif args.cmd == "pending":
            out = {"pending": host.pending()}
        elif args.cmd == "prepare":
            out = host.prepare(args.token, args.id, args.old_pages)
        elif args.cmd == "source-read":
            out = host.source_read(args.token, args.id, args.offset, args.limit)
        elif args.cmd == "context-read":
            out = host.context_read(args.token, args.path, args.offset, args.limit)
        elif args.cmd == "stage":
            out = host.stage(args.token)
        elif args.cmd == "commit":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            out = host.commit(args.token, proposal)
        elif args.cmd == "verify":
            out = host.verify(args.commit_key)
        elif args.cmd == "lint-save":
            out = host.lint_save(args.path, args.start, args.end, args.kind, args.evidence)
        else:
            raise HostError("unknown command")
    except HostError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
