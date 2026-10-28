"""Trusted-local reference only: no network, model, MCP or remote authentication.

All public mutations cooperate through one directory lock. Sync engines and
external editors do not honor it: keep the reference vault isolated.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import unicodedata
import uuid

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = [
    "00-总入口", "00-Inbox/随手放", "00-Inbox/日记", "00-Inbox/会议/待处理",
    "00-Inbox/已提取正文", "00-Inbox/问答投稿", "00-Inbox/待确认答复",
    "10-Sources/用户自述", "20-Wiki/实体", "20-Wiki/人物", "20-Wiki/概念",
    "20-Wiki/来源摘要", "20-Wiki/综合", "30-Projects", "40-输出", "50-写作范本",
    "90-Logs/待确认/questions", "90-Logs/提交回执", "99-System", "00-Test",
]
SOURCE_ROOTS = ("00-Inbox/随手放/", "00-Inbox/日记/", "00-Inbox/会议/",
                "00-Inbox/已提取正文/", "00-Inbox/待确认答复/", "10-Sources/")
RULE = "99-System/知识库规则.md"
INDEX = "20-Wiki/知识索引.md"
NAV = {INDEX, "20-Wiki/总览.md"}


class WikiError(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path, data):
    """Atomic single-file replacement, not a multi-file ACID transaction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with tmp.open("xb") as stream:
            stream.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def save_json(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def safe(root, name):
    """Reject Windows and POSIX path escape/alias forms before resolving."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise WikiError("Unsafe relative path")
    parts = name.split("/")
    if PurePosixPath(name).is_absolute() or any(p in {"", ".", ".."} for p in parts):
        raise WikiError("Unsafe path components")
    for part in parts:
        if part.endswith((" ", ".")) or re.search(r'[<>"|?*\x00-\x1f]', part):
            raise WikiError("Unsafe filename")
        if re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])", part.split(".")[0]):
            raise WikiError("Reserved filename")
    result = root
    for part in parts:
        result = result / part
        if result.is_symlink() or (hasattr(result, "is_junction") and result.is_junction()):
            raise WikiError("Symlink/junction paths are not supported")
    if not result.resolve().is_relative_to(root.resolve()):
        raise WikiError("Path escaped vault")
    return result


def note(root, name):
    result = safe(root, name)
    if not name.endswith(".md") or any(p.startswith(".") for p in name.split("/")):
        raise WikiError("Only non-hidden Markdown notes are allowed")
    return result


def file_hash(path):
    return digest(path.read_bytes()) if path.exists() else None


def init(vault):
    root = Path(vault).absolute()
    if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
        raise WikiError("init needs a new or empty directory; existing work is never replaced")
    root.mkdir(parents=True, exist_ok=True)
    # Reject symlinked parent chains too; the reference is intentionally conservative.
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise WikiError("Symlinked vault roots are not supported")
    for folder in FOLDERS:
        safe(root, folder).mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "vault-template").rglob("*"):
        if source.is_file():
            target = safe(root, source.relative_to(ROOT / "vault-template").as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(source.read_bytes())
    state = root / ".wiki-state"
    state.mkdir()
    for folder in ("transactions", "receipts", "questions", "answers", "rollback-archive"):
        (state / folder).mkdir()
    save_json(state / "config.json", {"reference_only": True, "version": 1, "created_at": now()})
    return {"status": "INITIALIZED", "vault": str(root), "reference_only": True}


class Wiki:
    def __init__(self, vault, role="reader"):
        self.root = Path(vault).absolute()
        if self.root.is_symlink() or any(p.is_symlink() for p in self.root.parents):
            raise WikiError("Symlinked vault root")
        self.state = safe(self.root, ".wiki-state")
        if not (self.state / "config.json").is_file():
            raise WikiError("Not a reference vault; use init on a NEW directory")
        if read_json(self.state / "config.json").get("reference_only") is not True:
            raise WikiError("Unexpected vault marker")
        if role not in {"reader", "proposer", "curator", "owner"}:
            raise WikiError("Unknown local role")
        self.role = role

    def require(self, *roles):
        if self.role not in roles:
            raise WikiError("ROLE_DENIED (local guard, not OS authentication)")

    @contextmanager
    def locked(self):
        lock = self.state / "writer.lock"
        try:
            lock.mkdir()
        except FileExistsError:
            raise WikiError("BUSY: do not steal lock; verify stopped writers before manual recovery")
        try:
            yield
        finally:
            lock.rmdir()

    def read(self, path, offset=0, limit=2500):
        if offset < 0 or limit < 1 or limit > 10000:
            raise WikiError("Invalid read range")
        body = note(self.root, path).read_text(encoding="utf-8")
        end = min(len(body), offset + limit)
        if offset > len(body):
            raise WikiError("Offset past END")
        return {"path": path, "sha256": digest(body), "text": body[offset:end],
                "read_range": [offset, end], "total_chars": len(body),
                "next_offset": end if end < len(body) else None}

    def _receipts(self):
        return [read_json(p) for p in sorted((self.state / "receipts").glob("*.json"))]

    def prepare(self, source, targets, operation="new"):
        self.require("curator", "owner")
        if operation not in {"new", "refresh", "topic_repair"}:
            raise WikiError("Unsupported operation")
        if not source.startswith(SOURCE_ROOTS):
            raise WikiError("Generated outputs and logs cannot be sources")
        source_file = note(self.root, source)
        if not source_file.is_file():
            raise WikiError("Source missing")
        if not targets or len(set(targets)) != len(targets):
            raise WikiError("Need unique targets")
        for target in targets:
            note(self.root, target)
            if not target.startswith(("20-Wiki/", "30-Projects/")) or target in NAV:
                raise WikiError("Target outside formal knowledge scope")
        with self.locked():
            if source.startswith("00-Inbox/待确认答复/"):
                answers = [read_json(p) for p in (self.state / "answers").glob("*.json")]
                match = next((a for a in answers if a["path"] == source), None)
                if match is None or operation != "topic_repair":
                    raise WikiError("Answer requires recorded identity and topic_repair")
                q = self._question(match["question_id"])
                if match["question_version"] != q["question_version"]:
                    raise WikiError("STALE_QUESTION")
                if set(targets) != set(q["target_versions"]):
                    raise WikiError("Answer cannot repair unrelated targets")
                if any(file_hash(note(self.root, p)) != h for p, h in q["target_versions"].items()):
                    raise WikiError("Question target changed; review question version first")
            token = uuid.uuid4().hex
            source_hash = file_hash(source_file)
            source_id = "source-" + digest(source)[:20]
            receipts = [r for r in self._receipts() if not r.get("rolled_back")]
            same = next((r for r in receipts if r["source_id"] == source_id and
                         r["source_sha256"] == source_hash and r["operation"] != "topic_repair"), None)
            if operation != "topic_repair" and same:
                return {**same, "reused": True, "historical_receipt": True}
            existing = any(r["source_id"] == source_id and r["operation"] != "topic_repair" for r in receipts)
            actual_op = "topic_repair" if operation == "topic_repair" else "refresh" if existing else "new"
            prepared = {"token": token, "created_at": now(), "source": source,
                        "source_id": source_id, "source_sha256": source_hash,
                        "operation": actual_op, "rule_sha256": file_hash(note(self.root, RULE)),
                        "index_sha256": file_hash(note(self.root, INDEX)),
                        "targets": {p: file_hash(note(self.root, p)) for p in targets}, "status": "PREPARED"}
            save_json(self.state / "transactions" / (token + ".json"), prepared)
            return prepared

    def _transaction(self, token):
        if not re.fullmatch("[a-f0-9]{32}", token):
            raise WikiError("Invalid token")
        path = self.state / "transactions" / (token + ".json")
        if not path.is_file():
            raise WikiError("Unknown transaction")
        return path, read_json(path)

    def _index(self, targets):
        paths = set(targets)
        for base in ("20-Wiki", "30-Projects"):
            for p in (self.root / base).rglob("*.md"):
                relative = p.relative_to(self.root).as_posix()
                note(self.root, relative)
                if relative not in NAV:
                    paths.add(relative)
        return "# 知识索引\n\n" + "".join(f"- [[{p}]]\n" for p in sorted(paths))

    def commit(self, token, plan, fail_after=None):
        self.require("curator", "owner")
        with self.locked():
            txpath, tx = self._transaction(token)
            plan_hash = digest(canonical(plan))
            if tx.get("plan_sha256") and tx["plan_sha256"] != plan_hash:
                raise WikiError("DURABLE_PLAN_CHANGED: retry same token and identical plan")
            receipt_path = self.state / "receipts" / (token + ".json")
            if receipt_path.exists():
                receipt = read_json(receipt_path)
                if receipt.get("rolled_back"):
                    raise WikiError("Transaction was rolled back")
                return {**receipt, "reused": True, "historical_receipt": True}
            if file_hash(note(self.root, tx["source"])) != tx["source_sha256"]:
                raise WikiError("SOURCE_CHANGED")
            if file_hash(note(self.root, RULE)) != tx["rule_sha256"]:
                raise WikiError("RULE_CHANGED")
            if tx["status"] == "PREPARED":
                updates = plan.get("updates", [])
                if not updates or len(updates) != len(tx["targets"]):
                    raise WikiError("Need exactly the prepared targets")
                if len({u["path"] for u in updates}) != len(updates):
                    raise WikiError("Duplicate target")
                if {u["path"] for u in updates} != set(tx["targets"]):
                    raise WikiError("Unprepared target")
                source_text = note(self.root, tx["source"]).read_text(encoding="utf-8")
                quotes = plan.get("quotes", [])
                if not quotes or any(not isinstance(q, str) or not q.strip() or q not in source_text for q in quotes):
                    raise WikiError("QUOTE_NOT_LITERAL")
                if any(not isinstance(u["content"], str) or not u["content"].strip() for u in updates):
                    raise WikiError("Empty output")
                if file_hash(note(self.root, INDEX)) != tx["index_sha256"]:
                    raise WikiError("INDEX_CHANGED")
                changes = []
                for u in updates:
                    p = note(self.root, u["path"])
                    if file_hash(p) != tx["targets"][u["path"]]:
                        raise WikiError("TARGET_CHANGED")
                    changes.append({"path": u["path"], "before": p.read_bytes().decode("utf-8") if p.exists() else None,
                                    "after": u["content"], "before_sha256": file_hash(p)})
                index_file = note(self.root, INDEX)
                changes.append({"path": INDEX, "before": index_file.read_bytes().decode("utf-8"),
                                "after": self._index(tx["targets"]), "before_sha256": file_hash(index_file)})
                log_path = "90-Logs/提交回执/" + token + ".md"
                changes.append({"path": log_path, "before": None, "before_sha256": None,
                                "after": f"# 提交记录\n\ncommit_key: {token}\nsource: [[{tx['source']}]]\noperation: {tx['operation']}\n\n"
                                         + "\n".join(f"- [[{u['path']}]]" for u in updates)
                                         + "\n\n最终状态以私有机器回执为准；本页存在不代表完成。\n"})
                # Full before-text is a private backup and is durable before ANY note write.
                tx.update(status="DURABLE", plan_sha256=plan_hash, changes=changes, plan=plan)
                save_json(txpath, tx)
            elif tx["status"] != "DURABLE":
                raise WikiError("Unexpected transaction state")
            for index, change in enumerate(tx["changes"]):
                p = note(self.root, change["path"])
                desired = digest(change["after"])
                current = file_hash(p)
                if current != desired:
                    if current != change["before_sha256"]:
                        raise WikiError("RECOVERY_CONFLICT: preserve external edits")
                    atomic(p, change["after"])
                if file_hash(p) != desired:
                    raise WikiError("READBACK_FAILED")
                if fail_after is not None and index + 1 == fail_after:
                    raise WikiError("INJECTED_CRASH: retry same token and plan")
            receipt = {"commit_key": token, "source_id": tx["source_id"], "source": tx["source"],
                       "source_sha256": tx["source_sha256"], "operation": tx["operation"],
                       "plan_sha256": plan_hash, "completed_at": now(), "verification": "PASS",
                       "maintenance_finalized": True, "reused": False,
                       "outputs": [{"path": u["path"], "sha256": digest(u["content"])} for u in plan["updates"]]}
            save_json(receipt_path, receipt)
            tx["status"] = "COMPLETE"
            save_json(txpath, tx)
            return receipt

    @staticmethod
    def _id(value):
        if not re.fullmatch("[a-z0-9][a-z0-9-]{0,63}", value):
            raise WikiError("Use a lowercase stable ID of at most 64 characters")
        return value

    def _question(self, question_id):
        p = self.state / "questions" / (self._id(question_id) + ".json")
        if not p.exists():
            raise WikiError("Unknown question")
        return read_json(p)

    def _write_question(self, q):
        save_json(self.state / "questions" / (q["question_id"] + ".json"), q)
        text = "# 待确认\n\n" + "\n".join(f"{k}: {q[k]}" for k in ("question_id", "question_version", "status", "ask"))
        text += "\n\n" + "\n".join(f"- [[{p}]]" for p in q["target_versions"])
        if q.get("commit_key"):
            text += f"\n\n最终回执：[[90-Logs/提交回执/{q['commit_key']}.md]]"
        atomic(note(self.root, f"90-Logs/待确认/questions/{q['question_id']}.md"), text + "\n")

    def question(self, question_id, target, ask):
        self.require("curator", "owner")
        self._id(question_id)
        if not ask.strip() or not target.startswith(("20-Wiki/", "30-Projects/")) or target in NAV:
            raise WikiError("Question needs a formal existing target and actual question")
        target_file = note(self.root, target)
        if not target_file.is_file():
            raise WikiError("Question target missing")
        with self.locked():
            if (self.state / "questions" / (question_id + ".json")).exists():
                raise WikiError("Question exists; reference CLI does not silently revise it")
            q = {"question_id": question_id, "question_version": 1, "ask": ask, "status": "pending",
                 "target_versions": {target: file_hash(target_file)}, "created_at": now()}
            self._write_question(q)
            return q

    def answer(self, question_id, version, text, source):
        self.require("proposer", "owner")
        if not text.strip() or not source.strip():
            raise WikiError("Need actual user words and provenance; draft is not an answer")
        with self.locked():
            q = self._question(question_id)
            if q["question_version"] != version:
                raise WikiError("STALE_QUESTION")
            normalized = " ".join(unicodedata.normalize("NFKC", text).split())
            answer_id = digest(canonical([question_id, version, normalized]))[:32]
            state_path = self.state / "answers" / (answer_id + ".json")
            if state_path.exists():
                return {**read_json(state_path), "reused": True}
            if q["status"] in {"adopted", "closed_unknown", "received"}:
                raise WikiError("Question closed or has an answer; conflicting/superseding answers need review")
            path = f"00-Inbox/待确认答复/{answer_id}.md"
            answer = {"kind": "confirmation-response", "answer_id": answer_id, "question_id": question_id,
                      "question_version": version, "text": text, "answer_source": source, "received_at": now(),
                      "path": path, "status": "received", "reused": False}
            body = "---\n" + "\n".join(f"{k}: {json.dumps(answer[k], ensure_ascii=False)}" for k in
                                       ("kind", "answer_id", "question_id", "question_version", "answer_source", "received_at"))
            body += "\n---\n# 主人答复（待核）\n\n" + text + "\n"
            # Exclusive creation; an unrelated existing proposal is never overwritten.
            with note(self.root, path).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(body)
            save_json(state_path, answer)
            q["status"] = "received"
            self._write_question(q)
            return answer

    def resolve(self, question_id, answer_id, commit_key):
        self.require("curator", "owner")
        if not re.fullmatch("[a-f0-9]{32}", answer_id):
            raise WikiError("Invalid answer ID")
        with self.locked():
            q = self._question(question_id)
            a = read_json(self.state / "answers" / (answer_id + ".json"))
            _, tx = self._transaction(commit_key)
            receipt_file = self.state / "receipts" / (commit_key + ".json")
            if not receipt_file.exists():
                raise WikiError("NO_FINAL_RECEIPT")
            receipt = read_json(receipt_file)
            if (a["question_id"] != question_id or a["question_version"] != q["question_version"]
                or receipt.get("rolled_back") or receipt["verification"] != "PASS"
                or not receipt["maintenance_finalized"] or receipt["operation"] != "topic_repair"
                or receipt["source"] != a["path"] or file_hash(note(self.root, a["path"])) != receipt["source_sha256"]
                or set(tx["targets"]) != set(q["target_versions"])
                or tx["targets"] != q["target_versions"]):
                raise WikiError("ANSWER_RECEIPT_MISMATCH")
            if any(file_hash(note(self.root, o["path"])) != o["sha256"] for o in receipt["outputs"]):
                raise WikiError("Final output changed; review before resolving")
            q.update(status="adopted", answer_id=answer_id, commit_key=commit_key)
            a.update(status="adopted", commit_key=commit_key)
            save_json(self.state / "answers" / (answer_id + ".json"), a)
            self._write_question(q)
            return q

    def rollback(self, token):
        self.require("owner")
        with self.locked():
            _, tx = self._transaction(token)
            receipts = sorted((r for r in self._receipts() if not r.get("rolled_back")), key=lambda r: r["completed_at"])
            if not receipts or receipts[-1]["commit_key"] != token:
                raise WikiError("Only the latest completed transaction can be rolled back")
            if any(file_hash(note(self.root, c["path"])) != digest(c["after"]) for c in tx["changes"]):
                raise WikiError("ROLLBACK_CONFLICT: current files differ; preserve edits")
            for c in reversed(tx["changes"]):
                p = note(self.root, c["path"])
                archived = safe(self.state / "rollback-archive", token + "/" + c["path"])
                archived.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, archived)
                if c["before"] is None:
                    # A newly generated file is archived above, not an original source.
                    p.unlink()
                else:
                    atomic(p, c["before"])
            r = receipts[-1]
            r.update(rolled_back=True, rollback_at=now())
            save_json(self.state / "receipts" / (token + ".json"), r)
            for p in (self.state / "questions").glob("*.json"):
                q = read_json(p)
                if q.get("commit_key") == token:
                    q["status"] = "needs_review"
                    self._write_question(q)
            return {"status": "ROLLED_BACK", "commit_key": token,
                    "warning": "Reference rollback is not crash-atomic; restore private journal backup if interrupted"}

    def query(self, text, offset=0, limit=20):
        if not text.strip() or offset < 0 or not 1 <= limit <= 50:
            raise WikiError("Invalid query")
        matches, scanned = [], 0
        for base in ("20-Wiki", "30-Projects", "10-Sources", "00-Inbox/会议"):
            for p in sorted((self.root / base).rglob("*.md")):
                path = p.relative_to(self.root).as_posix()
                body = note(self.root, path).read_text(encoding="utf-8")
                scanned += 1
                pos = body.find(text)
                if pos >= 0:
                    matches.append({"path": path, "sha256": digest(body), "offset": pos,
                                    "preview": body[max(0, pos - 40):pos + 160]})
        return {"matches": matches[offset:offset+limit], "matched_total": len(matches),
                "next_offset": offset + limit if offset + limit < len(matches) else None,
                "scanned_files": scanned, "semantic_answer": False,
                "coverage": "Selected directories only; search misses do not prove absence"}

    def health(self):
        candidates, formal_count = [], 0
        for base in ("20-Wiki", "30-Projects"):
            for p in sorted((self.root / base).rglob("*.md")):
                path = p.relative_to(self.root).as_posix()
                body = note(self.root, path).read_text(encoding="utf-8")
                if path not in NAV:
                    formal_count += 1
                    frontmatter = body.split("---", 2)[1] if body.startswith("---\n") and body.count("---") >= 2 else ""
                    missing = [k for k in ("title", "domain", "type", "status", "created", "updated", "sources")
                               if not re.search(r"(?m)^" + k + r":", frontmatter)]
                    if missing:
                        candidates.append({"path": path, "candidate": "missing_metadata", "fields": missing})
                cleaned = re.sub(r"```.*?```", "", body, flags=re.S)
                for target in re.findall(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]", cleaned):
                    link = target if target.endswith(".md") else target + ".md"
                    try:
                        exists = note(self.root, link).is_file()
                    except WikiError:
                        exists = False
                    if not exists:
                        candidates.append({"path": path, "candidate": "unresolved_link_not_confirmed_broken", "link": target})
        receipts = [r for r in self._receipts() if not r.get("rolled_back")]
        sources = {r["source_id"] for r in receipts if r["operation"] != "topic_repair"}
        return {"at": now(), "formal_pages": formal_count, "unique_curated_sources": len(sources),
                "completed_commits": len(receipts), "candidates": candidates,
                "semantic_review": "NOT_PERFORMED", "whole_vault_accepted": False,
                "scope": "Limited metadata/link candidate scan of 20-Wiki and 30-Projects"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "read", "query", "health", "prepare", "commit", "question", "answer", "resolve", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True)
        if name == "read":
            p.add_argument("--path", required=True)
            p.add_argument("--offset", type=int, default=0)
            p.add_argument("--limit", type=int, default=2500)
        if name == "query":
            p.add_argument("--text", required=True)
        if name == "prepare":
            p.add_argument("--source", required=True)
            p.add_argument("--target", action="append", required=True)
            p.add_argument("--operation", choices=["new", "refresh", "topic_repair"], default="new")
        if name in {"commit", "rollback", "resolve"}:
            p.add_argument("--token", required=True)
        if name == "commit":
            p.add_argument("--plan", required=True, help="Local JSON with updates[{path,content}] and quotes[str]")
        if name in {"question", "answer", "resolve"}:
            p.add_argument("--id", required=True)
        if name == "question":
            p.add_argument("--target", required=True)
            p.add_argument("--ask", required=True)
        if name == "answer":
            p.add_argument("--version", type=int, required=True)
            p.add_argument("--text", required=True)
            p.add_argument("--source", required=True)
        if name == "resolve":
            p.add_argument("--answer-id", required=True)
    args = parser.parse_args()
    c = args.command
    if c == "init":
        result = init(args.vault)
    else:
        role = "owner" if c == "rollback" else "proposer" if c == "answer" else "curator" if c in {
            "prepare", "commit", "question", "resolve"} else "reader"
        wiki = Wiki(args.vault, role)
        if c == "read": result = wiki.read(args.path, args.offset, args.limit)
        elif c == "query": result = wiki.query(args.text)
        elif c == "health": result = wiki.health()
        elif c == "prepare": result = wiki.prepare(args.source, args.target, args.operation)
        elif c == "commit": result = wiki.commit(args.token, read_json(Path(args.plan)))
        elif c == "question": result = wiki.question(args.id, args.target, args.ask)
        elif c == "answer": result = wiki.answer(args.id, args.version, args.text, args.source)
        elif c == "resolve": result = wiki.resolve(args.id, args.answer_id, args.token)
        elif c == "rollback": result = wiki.rollback(args.token)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (WikiError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
