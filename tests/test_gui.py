import json
import os
import tempfile
import unittest
from pathlib import Path

from notifyhub.config import ensure_config, save_config
from notifyhub.gui import (
    preview_payload,
    save_config_payload,
    status_payload,
    validate_config,
)


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.config_file = self.home / "config.json"
        ensure_config(self.config_file)
        self.old_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.home / "codex")

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.old_home
        self.temp.cleanup()

    def test_validate_accepts_valid_config(self):
        self.assertEqual(validate_config(json.loads(self.config_file.read_text())), [])

    def test_validate_rejects_bad_webhook(self):
        config = json.loads(self.config_file.read_text())
        config["channels"]["feishu"]["webhook_url"] = "ftp://example"
        self.assertTrue(validate_config(config))

    def test_validate_rejects_bad_event(self):
        config = json.loads(self.config_file.read_text())
        config["codex"]["events"] = ["Unknown"]
        self.assertTrue(validate_config(config))

    def test_validate_allows_env_placeholder(self):
        config = json.loads(self.config_file.read_text())
        config["channels"]["feishu"]["webhook_url"] = "${FEISHU_WEBHOOK}"
        self.assertEqual(validate_config(config), [])

    def test_save_preserves_placeholder(self):
        save_config({"channels": {"feishu": {"webhook_url": "${DEMO_WEBHOOK}"}}}, self.config_file)
        os.environ["DEMO_WEBHOOK"] = "https://example.test/hook"
        try:
            saved = save_config_payload(self.config_file, {"channels": {"feishu": {"at_all": True}}})
        finally:
            os.environ.pop("DEMO_WEBHOOK")
        self.assertEqual(saved["channels"]["feishu"]["webhook_url"], "${DEMO_WEBHOOK}")
        self.assertTrue(saved["channels"]["feishu"]["at_all"])
        self.assertIn("${DEMO_WEBHOOK}", self.config_file.read_text(encoding="utf-8"))

    def test_preview_payload(self):
        data = preview_payload(self.config_file, "complete")
        self.assertIn("Goal 完成", data["text"])
        self.assertEqual(data["card"]["header"]["template"], "green")

    def test_status_payload(self):
        data = status_payload(self.config_file)
        self.assertTrue(data["ok"])
        self.assertIn("feishu", data["channels"])
        self.assertIn("feishu_app", data["channels"])
        self.assertIn("hooks_installed", data["codex"])


if __name__ == "__main__":
    unittest.main()
