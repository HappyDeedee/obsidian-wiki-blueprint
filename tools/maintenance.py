"""Configured aliases maintenance against an already integrated MCP host.

This does not install that host. Credentials and mutable state stay outside
the checkout. CHECKPOINT/BUSY are not successful publication receipts.
"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "system/production-reference/maintenance"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), MODULES / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("mcp_url", "mcp_token", "state_dir"):
        config[key] = os.environ.get("WIKI_" + key.upper(), config.get(key, ""))
        if not isinstance(config[key], str) or not config[key]:
            raise ValueError("missing configuration field: " + key)
    state = Path(config["state_dir"])
    if not state.is_absolute() or state.resolve().is_relative_to(ROOT):
        raise ValueError("state_dir must be an absolute directory outside the checkout")
    config["state_dir"] = state
    return config


def full_refresh(client, state, publish=False):
    views = load("wiki-generated-views")
    # Hold the same server writer lease for the full snapshot and publication.
    lease = json.loads(client.tool("curation_writer_acquire", owner="public-aliases-full"))
    if lease.get("status") != "ACQUIRED":
        return {"status": "BUSY", "changed": False}
    try:
        paths = views.changed_alias_sources(client)
        if not paths:
            raise ValueError("no alias candidates; keep the previous view")
        notes = {path: views.read(client, path) for path in paths}
        if any(text is None for text in notes.values()):
            raise ValueError("source disappeared; keep the previous view")
        generated = views.generate(notes, "aliases")
        if generated["errors"]:
            raise ValueError("invalid source frontmatter; keep the previous view")
        if paths != views.changed_alias_sources(client):
            raise ValueError("source listing changed; retry")
        for path, text in notes.items():
            if views.read(client, path) != text:
                raise ValueError("source changed; retry")
        if not publish:
            return {"status": "READY", "count": generated["count"], "changed": False}
        target = "20-Wiki/别名表.md"
        old = views.read(client, target)
        changed = old != generated["body"]
        if changed:
            backups = state / "generated-view-backups"
            backups.mkdir(mode=0o700, parents=True, exist_ok=True)
            name = datetime.datetime.now(datetime.timezone.utc).strftime("aliases-full-%Y%m%dT%H%M%S%fZ.json")
            backup = backups / name
            backup.write_text(json.dumps({"path": target, "body": old}, ensure_ascii=False), encoding="utf-8")
            backup.chmod(0o600)
            response = client.tool("write_note", path=target, content=generated["body"])
            if "saved" not in response.lower():
                raise ValueError("publication was not acknowledged")
        if views.read(client, target) != generated["body"]:
            raise ValueError("publication readback mismatch; inspect backup before retry")
        return {"status": "PASS", "target": target, "changed": changed, "count": generated["count"]}
    finally:
        client.tool("curation_writer_release", token=lease["token"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("action", choices=["aliases-full", "aliases-incremental"])
    parser.add_argument("--publish", action="store_true", help="otherwise validate/checkpoint without publishing")
    parser.add_argument("--batch-size", type=int, default=40)
    args = parser.parse_args(argv)
    try:
        if args.batch_size < 1:
            raise ValueError("batch-size must be positive")
        config = configuration(args.config)
        state = config["state_dir"]
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Exclusive local run guard also protects the incremental checkpoint.
        lock = state / "aliases.lock"
        try:
            lock.mkdir()
        except FileExistsError:
            print(json.dumps({"status": "BUSY", "changed": False}))
            return 2
        try:
            client = load("vault-client").MCP(config["mcp_url"], config["mcp_token"])
            if args.action == "aliases-full":
                result = full_refresh(client, state, args.publish)
            else:
                incremental = load("aliases-incremental")
                if not incremental.listing(client):
                    raise ValueError("no alias candidates; keep the previous view")
                result = incremental.refresh(
                    client, MODULES, state, publish=args.publish, batch_size=args.batch_size)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] in {"PASS", "READY"} else 2
        finally:
            lock.rmdir()
    except Exception as exc:
        # Do not print server responses, URLs, tokens or source bodies.
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__,
                          "reason": "check configuration, host compatibility and private service logs"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
