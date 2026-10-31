"""Behavior checks for configured maintenance, isolated from any real service."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("maintenance", Path(__file__).resolve().parents[1] / "tools/maintenance.py")
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class MemoryMCP:
    def __init__(self, incomplete=False):
        self.notes = {"20-Wiki/实体/示例.md": "---\naliases: [示例简称]\n---\n# 示例\n",
                      "20-Wiki/别名表.md": "old view"}
        self.calls = []
        self.incomplete = incomplete

    def tool(self, name, **args):
        self.calls.append((name, args))
        if name == "curation_writer_acquire":
            return json.dumps({"status": "ACQUIRED", "token": "test-lease"})
        if name == "curation_writer_release":
            return "released"
        if name == "search_notes":
            if self.incomplete:
                return "may be incomplete"
            prefix = "- 2020-01-01 [示例](obsidian://open?vault=demo&file=20-Wiki%2F实体%2F示例.md)\n" if args["folder"] == "20-Wiki" else ""
            return prefix + '[SearchPage {"next_offset": null}]'
        if name == "read_note":
            body = self.notes.get(args["path"])
            return "Note not found:" if body is None else "[Open in Obsidian](obsidian://demo)\n\n---\n\n" + body
        if name == "write_note":
            self.notes[args["path"]] = args["content"]
            return "saved"
        raise AssertionError(name)


class MaintenanceTest(unittest.TestCase):
    def test_full_refresh_backups_readback_and_idempotence(self):
        client = MemoryMCP()
        source_before = client.notes["20-Wiki/实体/示例.md"]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self.assertEqual(maintenance.full_refresh(client, state)["status"], "READY")
            self.assertEqual(client.notes["20-Wiki/别名表.md"], "old view")
            result = maintenance.full_refresh(client, state, publish=True)
            self.assertEqual(result["status"], "PASS")
            self.assertIn("示例简称", client.notes["20-Wiki/别名表.md"])
            self.assertEqual(source_before, client.notes["20-Wiki/实体/示例.md"])
            self.assertEqual(len(list((state / "generated-view-backups").glob("*.json"))), 1)
            self.assertFalse(maintenance.full_refresh(client, state, publish=True)["changed"])
            self.assertEqual(client.calls[-1][0], "curation_writer_release")

    def test_incomplete_index_preserves_view_and_releases_writer(self):
        client = MemoryMCP(incomplete=True)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            maintenance.full_refresh(client, Path(tmp), publish=True)
        self.assertEqual(client.notes["20-Wiki/别名表.md"], "old view")
        self.assertEqual(client.calls[-1][0], "curation_writer_release")

    def test_incremental_checkpoint_is_not_publication_success(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "private.json"
            config.write_text(json.dumps({"mcp_url": "https://wiki.example.invalid/mcp", "mcp_token": "fictional", "state_dir": str(Path(tmp) / "state")}), encoding="utf-8")
            def load(name):
                if name == "vault-client":
                    return SimpleNamespace(MCP=lambda *args: object())
                return SimpleNamespace(listing=lambda client: {"fictional-source": "2020-01-01"},
                                       refresh=lambda *args, **kwargs: {"status": "CHECKPOINT", "remaining": 1, "changed": False})
            with patch.object(maintenance, "load", side_effect=load):
                self.assertEqual(maintenance.main(["--config", str(config), "aliases-incremental", "--publish"]), 2)
            self.assertFalse((Path(tmp) / "state/aliases.lock").exists())


if __name__ == "__main__":
    unittest.main()
