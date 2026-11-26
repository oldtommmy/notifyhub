import json
import os
import tempfile
import unittest
from pathlib import Path

from notifyhub import gateway_install
from notifyhub.config import ensure_config, load_config, save_config
from notifyhub.gui import (
    save_config_payload,
    status_payload,
    validate_config,
)
from notifyhub.installer import install_codex, uninstall_codex


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.config_file = self.home / "config.json"
        ensure_config(self.config_file)
        self.outbox_path = self.home / "outbox.json"
        save_config_payload(self.config_file, {"outbox": {"path": str(self.outbox_path)}})
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

    def test_stage_app_credentials_with_text_format_and_notifications_off(self):
        save_config_payload(self.config_file, {"channels": {
            "feishu": {"enabled": True, "webhook_url": "https://example.test/hook"},
            "feishu_app": {"message_type": "text"},
        }})
        saved = save_config_payload(self.config_file, {"channels": {"feishu_app": {
            "app_id": "cli_test", "app_secret": "test-secret", "receive_id": "ou_owner", "enabled": False,
        }}})
        self.assertEqual(saved["channels"]["feishu_app"]["message_type"], "text")
        status = status_payload(self.config_file)["channels"]
        self.assertTrue(status["feishu"]["configured"])
        self.assertTrue(status["feishu_app"]["credentials_ready"])
        self.assertFalse(status["feishu_app"]["configured"])

    def test_status_covers_outbox_without_exposing_its_contents(self):
        self.outbox_path.write_text(json.dumps({"entries": [
            {"id": "retry", "notification": {"last_message": "private"}},
            {"id": "dead", "dead": True},
        ]}))
        status = status_payload(self.config_file)
        self.assertTrue(status["state_path"])
        self.assertEqual(status["outbox"]["count"], 2)
        self.assertEqual(status["outbox"]["due"], 1)
        self.assertEqual(status["outbox"]["dead"], 1)
        self.assertNotIn("private", json.dumps(status))

    def test_reports_installed_codex_hook_even_after_events_change(self):
        home = self.home / "codex"
        install_codex(home, ["Interrupt"], "python notifyhub.py hook codex")
        self.assertTrue(status_payload(self.config_file)["codex"]["hooks_installed"])
        self.assertEqual(uninstall_codex(home)["removed"], 1)
        self.assertFalse(status_payload(self.config_file)["codex"]["hooks_installed"])

    def test_gateway_button_switch_preserves_custom_bot_and_other_settings(self):
        save_config_payload(self.config_file, {"channels": {"feishu_app": {
            "app_id": "cli_notifications", "app_secret": "notification-fixture",
        }}})
        gateway = self.home / "gateway.json"
        config = load_config(self.config_file)
        config["bridge"]["max_message_chars"] = 1800
        config["channels"]["feishu_app"].update(app_id="cli_continuation", app_secret="gateway-fixture")
        save_config(config, gateway)
        gateway_install.save_options(self.config_file, {
            "enabled": True, "dsh_enabled": False, "codex_enabled": True, "antigravity_enabled": True,
            "allow_open_ids": ["ou_user"], "card_actions": True,
        })
        saved = load_config(gateway)
        self.assertEqual(saved["channels"]["feishu_app"]["app_id"], "cli_continuation")
        self.assertEqual(saved["bridge"]["max_message_chars"], 1800)
        self.assertTrue(saved["bridge"]["card_actions"])
        self.assertEqual(status_payload(self.config_file)["continuation"]["selected_engines"], ["codex", "antigravity"])

    def test_cannot_enable_gateway_without_a_verified_allowlist(self):
        with self.assertRaisesRegex(ValueError, "白名单"):
            gateway_install.save_options(self.config_file, {
                "enabled": True, "codex_enabled": True, "allow_open_ids": [], "card_actions": True,
            })
        self.assertFalse((self.home / "gateway.json").exists())

    def test_gateway_card_actions_default_on_and_preserve_opt_out(self):
        options = {"allow_open_ids": []}
        self.assertTrue(status_payload(self.config_file)["continuation"]["card_actions"])
        gateway = gateway_install.save_options(self.config_file, options)
        self.assertTrue(load_config(gateway)["bridge"]["card_actions"])

        gateway_install.save_options(self.config_file, {**options, "card_actions": False})
        gateway_install.save_options(self.config_file, options)
        self.assertFalse(status_payload(self.config_file)["continuation"]["card_actions"])

    def test_status_payload(self):
        data = status_payload(self.config_file)
        self.assertTrue(data["ok"])
        self.assertIn("feishu", data["channels"])
        self.assertIn("feishu_app", data["channels"])
        self.assertIn("hooks_installed", data["codex"])


if __name__ == "__main__":
    unittest.main()
