import os
import tempfile
import unittest
from pathlib import Path

from notifyhub.config import deep_merge, ensure_config, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_deep_merge_keeps_nested_defaults(self):
        merged = deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}})
        self.assertEqual(merged["a"], {"b": 9, "c": 2})

    def test_env_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config({"channels": {"feishu": {"webhook_url": "${TEST_WEBHOOK}", "secret": ""}}}, path)
            os.environ["TEST_WEBHOOK"] = "https://example.test/hook"
            try:
                config = load_config(path)
            finally:
                os.environ.pop("TEST_WEBHOOK")
        self.assertEqual(config["channels"]["feishu"]["webhook_url"], "https://example.test/hook")

    def test_ensure_config_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"version": 9}', encoding="utf-8")
            target, created = ensure_config(path)
        self.assertFalse(created)
        self.assertEqual(target, path)

    def test_ensure_config_creates_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            target, created = ensure_config(path)
            self.assertTrue(created)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
