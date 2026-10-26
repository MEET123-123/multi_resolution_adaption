from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "studio_reverse" / "web_theme_pipeline"))
from browser_runtime_render import asset


class BrowserAssetPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root / "assets" / "unlock" / "lockscreen" / "background.jpg"
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b"fake-jpeg-content")

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_session_relative_path(self):
        result = asset("assets/unlock/lockscreen/background.jpg?v=abc", self.root)
        self.assertTrue(result.startswith("data:image/jpeg;base64,"))
        self.assertIn("ZmFrZS1qcGVnLWNvbnRlbnQ=", result)

    def test_http_rooted_asset_path(self):
        result = asset("/assets/unlock/lockscreen/background.jpg?v=abc", self.root)
        self.assertTrue(result.startswith("data:image/jpeg;base64,"))

    def test_encoded_traversal_is_rejected(self):
        self.assertIsNone(asset("/assets/%2e%2e/secret.png", self.root))
        self.assertIsNone(asset("/assets/%2e%2e%2fsecret.png", self.root))

    def test_backslash_traversal_is_rejected(self):
        self.assertIsNone(asset(r"assets\..\secret.png", self.root))
        self.assertIsNone(asset("/assets/%5c..%5csecret.png", self.root))

    def test_external_and_data_urls_are_rejected(self):
        self.assertIsNone(asset("https://example.com/assets/a.png", self.root))
        self.assertIsNone(asset("data:image/png;base64,AAAA", self.root))
        self.assertIsNone(asset("//example.com/assets/a.png", self.root))

    def test_unrelated_and_missing_paths_are_rejected(self):
        self.assertIsNone(asset("/other/a.png", self.root))
        self.assertIsNone(asset("assets/missing.png", self.root))
        self.assertIsNone(asset("assets/unlock/lockscreen/background.exe", self.root))

    def test_control_characters_are_rejected(self):
        self.assertIsNone(asset("assets/unlock/lockscreen/%00background.jpg", self.root))


if __name__ == "__main__":
    unittest.main()
