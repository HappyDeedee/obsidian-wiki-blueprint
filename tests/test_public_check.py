from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from check_public import inspect


class PublicCheckTests(unittest.TestCase):
    def test_detects_synthetic_secret_without_echoing_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("gh" + "p_" + "X" * 36, encoding="utf-8")
            result = inspect(root)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("github_token", [i["issue"] for i in result["issues"]])
            self.assertNotIn("X" * 36, str(result))

    def test_missing_link_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("[entry](missing.md)", encoding="utf-8")
            self.assertIn("broken_repository_link", [i["issue"] for i in inspect(root)["issues"]])

    def test_non_allowlisted_file_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "credentials.json").write_text("{}", encoding="utf-8")
            self.assertIn("not_in_public_allowlist", [i["issue"] for i in inspect(root)["issues"]])

    def test_arbitrary_binary_is_rejected_even_under_fixture_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'benchmark/fixtures').mkdir(parents=True)
            (root/'benchmark/fixtures/secret.wav').write_bytes(b'not a public fixture')
            result=inspect(root)
            self.assertIn('private_or_binary_artifact',[i['issue'] for i in result['issues']])

    def test_dependency_directory_is_not_scanned_or_manifested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'host/node_modules/package').mkdir(parents=True)
            (root/'host/node_modules/package/private.key').write_text('generated dependency',encoding='utf-8')
            self.assertEqual(inspect(root)['checked_files'],0)


if __name__ == "__main__":
    unittest.main()
