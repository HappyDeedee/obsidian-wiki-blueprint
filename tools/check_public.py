"""Conservative public-package check; not a complete secret or privacy audit."""
from pathlib import Path
import hashlib
import json
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", "__pycache__"}
TOP = {"README.md", "AGENTS.md", "LICENSE", "SECURITY.md", "THIRD_PARTY.md",
       ".gitignore", ".gitattributes", ".github", "docs", "skills", "config",
       "prompts", "vault-template", "tools", "tests", "release", "system", "manifest.json"}
PATTERNS = {
    "github_token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "cloud_access_key": re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "model_key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),
    "bearer_value": re.compile(r"Bearer\s+[A-Za-z0-9_.-]{24,}"),
    "non_loopback_ipv4": re.compile(r"(?<![\w.])(?!127\.0\.0\.1(?:[^\d.]|$))(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
    "platform_record_id": re.compile(r"\bcm[v-z][a-z0-9]{20,}\b"),
    "private_absolute_path": re.compile(r"(?i)(?:[a-z]:[\\/](?:Users|myproject|obsidian)[\\/]|/(?:home)/[^/\s]+/)"),
    "url_credentials": re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
}


def files(root=ROOT):
    return sorted(p for p in root.rglob("*") if p.is_file() and not any(x in IGNORED for x in p.relative_to(root).parts))


def inspect(root=ROOT):
    issues = []
    entries = files(root)
    for path in entries:
        rel = path.relative_to(root)
        if rel.parts[0] not in TOP:
            issues.append({"path": rel.as_posix(), "issue": "not_in_public_allowlist"})
        if path.is_symlink() or path.suffix.lower() in {".pem", ".key", ".db", ".sqlite", ".zip", ".gz", ".mp3", ".wav"}:
            issues.append({"path": rel.as_posix(), "issue": "private_or_binary_artifact"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            issues.append({"path": rel.as_posix(), "issue": "not_utf8"})
            continue
        if "\ufffd" in text:
            issues.append({"path": rel.as_posix(), "issue": "replacement_character"})
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                issues.append({"path": rel.as_posix(), "issue": label})
        if path.suffix == ".md":
            # Markdown code blocks are excluded from link checking; code examples aren't links.
            prose = re.sub(r"```.*?```", "", text, flags=re.S)
            for link in re.findall(r"\]\(([^)]+)\)", prose):
                if re.match(r"(?:https?://|mailto:|#)", link):
                    continue
                target = (path.parent / unquote(link.split("#", 1)[0])).resolve()
                if not target.is_relative_to(root.resolve()) or not target.exists():
                    issues.append({"path": rel.as_posix(), "issue": "broken_repository_link", "link": link})
    for skill in (root / "skills").glob("*/SKILL.md"):
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---\n") or not re.search(r"(?m)^name: [a-z0-9-]+$", text) or not re.search(r"(?m)^description: .+", text):
            issues.append({"path": skill.relative_to(root).as_posix(), "issue": "invalid_skill_frontmatter"})
    manifest_file = root / "manifest.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        actual = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in entries if p.name != "manifest.json"}
        if manifest.get("files") != actual:
            issues.append({"path": "manifest.json", "issue": "manifest_file_set_or_hash_mismatch"})
    return {"status": "PASS" if not issues else "FAIL", "checked_files": len(entries), "issues": issues,
            "limitations": "Pattern and link checks only; human privacy review and history scanning still required"}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = inspect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
