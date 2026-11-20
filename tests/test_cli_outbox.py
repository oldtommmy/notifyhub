import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from notifyhub.cli import main


class DoctorTests(unittest.TestCase):
    """cmd_doctor had no coverage and shipped a NameError; keep it executing."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, extra=None):
        config = {
            "state_path": str(self.root / "state.json"),
            "log_path": str(self.root / "notifyhub.log"),
            "outbox": {"enabled": True, "path": str(self.root / "outbox.json")},
            "channels": {
                "feishu": {"enabled": False, "webhook_url": ""},
                "feishu_app": {"enabled": False},
                "imessage": {"enabled": False},
            },
        }
        if extra:
            config.update(extra)
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def run_doctor(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--config", str(self.config_path), "doctor"])
        return code, output.getvalue()

    def test_doctor_runs_with_default_channels_disabled(self):
        self.write_config()
        code, output = self.run_doctor()
        self.assertIn("发件箱", output)
        self.assertIn("飞书群机器人", output)
        # No notification channel configured at all is a critical failure.
        self.assertEqual(code, 1)

    def test_doctor_reports_empty_outbox(self):
        self.write_config()
        _, output = self.run_doctor()
        self.assertIn("发件箱: 空", output)

    def test_doctor_flags_outbox_backlog(self):
        self.write_config()
        outbox = {
            "entries": [
                {
                    "id": "1",
                    "created_at": 9_999_999_999,
                    "attempts": 1,
                    "next_attempt_at": 0,
                    "dead": False,
                    "last_error": "request failed: Connection refused",
                    "notification": {"dedupe_key": "k"},
                }
            ]
        }
        (self.root / "outbox.json").write_text(json.dumps(outbox), encoding="utf-8")
        _, output = self.run_doctor()
        self.assertIn("积压 1 条", output)

    def test_doctor_does_not_warn_about_disabled_webhook(self):
        self.write_config()
        _, output = self.run_doctor()
        self.assertIn("○ 飞书群机器人: 已关闭", output)

    def test_doctor_flags_enabled_but_unconfigured_webhook(self):
        self.write_config(
            {"channels": {"feishu": {"enabled": True, "webhook_url": ""}}}
        )
        _, output = self.run_doctor()
        self.assertIn("✗ 飞书群机器人: 缺少 webhook_url", output)


class FlushCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "state_path": str(self.root / "state.json"),
                    "log_path": str(self.root / "notifyhub.log"),
                    "outbox": {"enabled": True, "path": str(self.root / "outbox.json")},
                    "channels": {
                        "feishu": {"enabled": False},
                        "feishu_app": {"enabled": False},
                        "imessage": {"enabled": False},
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_flush(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--config", str(self.config_path), "flush", *args])
        return code, output.getvalue()

    def test_status_on_empty_outbox(self):
        code, output = self.run_flush("--status")
        self.assertEqual(code, 0)
        self.assertIn("积压: 0 条", output)

    def test_clear(self):
        (self.root / "outbox.json").write_text(
            json.dumps({"entries": [{"id": "1", "notification": {}}]}), encoding="utf-8"
        )
        code, output = self.run_flush("--clear")
        self.assertEqual(code, 0)
        self.assertIn("已清空发件箱（1 条）", output)

    def test_flush_with_nothing_pending_succeeds(self):
        code, output = self.run_flush()
        self.assertEqual(code, 0)
        self.assertIn("成功 0", output)


if __name__ == "__main__":
    unittest.main()