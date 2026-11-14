import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notifyhub.config import deep_merge, DEFAULT_CONFIG
from notifyhub.events import Notification
from notifyhub.router import dispatch


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = str(Path(self.temp.name) / "state.json")
        self.config = deep_merge(
            DEFAULT_CONFIG,
            {
                "state_path": self.state_path,
                "channels": {"feishu": {"enabled": True, "webhook_url": "https://example.test"}},
            },
        )
        self.logger = logging.getLogger("notifyhub-test")
        self.logger.addHandler(logging.NullHandler())
        self.sent = []

    def tearDown(self):
        self.temp.cleanup()

    def notification(self, key="key-1", cwd="/tmp/work/demo"):
        return Notification(
            source="codex",
            event="Stop",
            status_text="✅ 完成",
            project="demo",
            cwd=cwd,
            dedupe_key=key,
        )

    def test_delivers_and_deduplicates(self):
        def fake_sender(notification, config, logger):
            self.sent.append(notification)

        with patch("notifyhub.router.get_sender", return_value=fake_sender):
            first = dispatch(self.notification(), self.config, self.logger)
            second = dispatch(self.notification(), self.config, self.logger)

        self.assertEqual(first, [("feishu", True, "")])
        self.assertEqual(second, [])
        self.assertEqual(len(self.sent), 1)

    def test_force_bypasses_dedupe(self):
        def fake_sender(notification, config, logger):
            self.sent.append(notification)

        with patch("notifyhub.router.get_sender", return_value=fake_sender):
            dispatch(self.notification(), self.config, self.logger)
            dispatch(self.notification(), self.config, self.logger, force=True)

        self.assertEqual(len(self.sent), 2)

    def test_project_deny(self):
        config = deep_merge(self.config, {"filters": {"projects_deny": ["demo"]}})
        with patch("notifyhub.router.get_sender") as sender:
            results = dispatch(self.notification(), config, self.logger)
        self.assertEqual(results, [])
        sender.assert_not_called()

    def test_quiet_hours(self):
        config = deep_merge(
            self.config,
            {"filters": {"quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59"}}},
        )
        with patch("notifyhub.router.get_sender") as sender:
            results = dispatch(self.notification(), config, self.logger)
        self.assertEqual(results, [])
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
