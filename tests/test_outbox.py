import logging
import tempfile
import unittest
from pathlib import Path

from notifyhub.config import deep_merge, DEFAULT_CONFIG
from notifyhub.events import Notification
from notifyhub.outbox import (
    Outbox,
    backoff_seconds,
    is_permanent_error,
    notification_from_dict,
    notification_to_dict,
)
from notifyhub.router import dispatch, replay_outbox


def quiet_logger():
    logger = logging.getLogger("notifyhub-outbox-test")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "outbox.json")
        self.logger = quiet_logger()

    def tearDown(self):
        self.temp.cleanup()

    def outbox(self, **settings):
        base = {"enabled": True, "path": self.path}
        base.update(settings)
        return Outbox(base, self.logger)

    def notification(self, key="key-1"):
        return Notification(
            source="codex",
            event="Stop",
            status_text="✅ 完成",
            project="demo",
            dedupe_key=key,
        )

    def test_roundtrip_keeps_notification_fields(self):
        original = self.notification()
        restored = notification_from_dict(notification_to_dict(original))
        self.assertEqual(restored.status_text, original.status_text)
        self.assertEqual(restored.dedupe_key, "key-1")
        self.assertEqual(restored.extra_lines, [])

    def test_add_persists_to_disk(self):
        outbox = self.outbox()
        entry = outbox.add(self.notification(), [("feishu_app", False, "connection refused")])
        self.assertIsNotNone(entry)
        self.assertTrue(Path(self.path).exists())
        self.assertEqual(len(self.outbox()), 1)
        self.assertEqual(self.outbox().entries()[0]["notification"]["status_text"], "✅ 完成")

    def test_disabled_outbox_does_not_write(self):
        outbox = self.outbox(enabled=False)
        self.assertIsNone(outbox.add(self.notification(), [("feishu_app", False, "x")]))
        self.assertFalse(Path(self.path).exists())

    def test_permanent_error_goes_dead_and_is_not_due(self):
        outbox = self.outbox()
        entry = outbox.add(
            self.notification(),
            [("feishu_app", False, "code=99991672 msg=no permission")],
        )
        self.assertTrue(entry["dead"])
        self.assertEqual(outbox.due_count(), 0)

    def test_transient_error_is_due_and_backs_off(self):
        outbox = self.outbox()
        entry = outbox.add(
            self.notification(),
            [("feishu_app", False, "request failed: Connection refused")],
        )
        self.assertFalse(entry["dead"])
        self.assertEqual(outbox.due_count(), 1)

        updated = outbox.record_failure(entry["id"], "request failed: Connection refused", now=1000.0)
        self.assertEqual(updated["attempts"], 1)
        self.assertFalse(updated["dead"])
        # Not due until the backoff window elapses.
        self.assertEqual(outbox.due_count(now=1000.0), 0)
        self.assertEqual(outbox.due_count(now=1000.0 + backoff_seconds(1)), 1)

    def test_permanent_error_on_replay_kills_entry(self):
        outbox = self.outbox()
        entry = outbox.add(self.notification(), [("feishu_app", False, "timeout")])
        updated = outbox.record_failure(entry["id"], "code=230001 msg=invalid receive_id")
        self.assertTrue(updated["dead"])

    def test_max_attempts_marks_dead(self):
        outbox = self.outbox(max_attempts=2)
        entry = outbox.add(self.notification(), [("feishu_app", False, "timeout")])
        outbox.record_failure(entry["id"], "timeout")
        updated = outbox.record_failure(entry["id"], "timeout")
        self.assertTrue(updated["dead"])

    def test_dedupe_key_replaces_existing_entry(self):
        outbox = self.outbox()
        outbox.add(self.notification("dup"), [("feishu_app", False, "a")])
        outbox.add(self.notification("dup"), [("feishu_app", False, "b")])
        self.assertEqual(len(outbox), 1)
        self.assertIn("b", outbox.entries()[0]["last_error"])

    def test_prunes_expired_entries(self):
        outbox = self.outbox(max_age_hours=1)
        outbox._entries = [
            {"id": "old", "created_at": 0, "notification": {"dedupe_key": "old"}},
        ]
        outbox.add(self.notification("new"), [("feishu_app", False, "x")], now=10_000)
        ids = [entry["id"] for entry in outbox.entries()]
        self.assertNotIn("old", ids)
        self.assertEqual(len(ids), 1)

    def test_revive_dead_requeues(self):
        outbox = self.outbox()
        outbox.add(self.notification(), [("feishu_app", False, "code=99991672 msg=x")])
        self.assertEqual(outbox.due_count(), 0)
        self.assertEqual(outbox.revive_dead(), 1)
        self.assertEqual(outbox.due_count(), 1)

    def test_remove_and_clear(self):
        outbox = self.outbox()
        entry = outbox.add(self.notification(), [("feishu_app", False, "x")])
        self.assertTrue(outbox.remove(entry["id"]))
        self.assertEqual(len(outbox), 0)
        outbox.add(self.notification("b"), [("feishu_app", False, "y")])
        self.assertEqual(outbox.clear(), 1)
        self.assertEqual(len(outbox), 0)


class OutboxErrorTests(unittest.TestCase):
    def test_permanent_codes(self):
        for code in ("99991663", "99991672", "99991661", "230001", "230002", "230006"):
            self.assertTrue(is_permanent_error(f"code={code} msg=x"), code)

    def test_transient_errors_are_repeatable(self):
        for message in (
            "request failed: Connection refused",
            "<urlopen error _ssl.c:993: handshake timed out>",
            "HTTP 502: bad gateway",
        ):
            self.assertFalse(is_permanent_error(message), message)

    def test_configuration_errors_are_permanent(self):
        self.assertTrue(is_permanent_error("feishu.webhook_url is not configured"))

    def test_backoff_is_capped(self):
        self.assertEqual(backoff_seconds(1), 60)
        self.assertEqual(backoff_seconds(20), 3600)


class DispatchOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = deep_merge(
            DEFAULT_CONFIG,
            {
                "state_path": str(Path(self.temp.name) / "state.json"),
                "outbox": {"enabled": True, "path": str(Path(self.temp.name) / "outbox.json")},
                "channels": {"feishu": {"enabled": True, "webhook_url": "https://example.test"}},
            },
        )
        self.logger = quiet_logger()

    def tearDown(self):
        self.temp.cleanup()

    def notification(self, key="key-1"):
        return Notification(source="codex", event="Stop", status_text="✅ 完成", dedupe_key=key)

    def test_failed_delivery_is_queued(self):
        from unittest.mock import patch

        with patch("notifyhub.router.get_sender") as get_sender:
            get_sender.return_value = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            results = dispatch(self.notification(), self.config, self.logger)

        self.assertFalse(results[0][1])
        outbox = Outbox(self.config["outbox"], self.logger)
        self.assertEqual(len(outbox), 1)

    def test_successful_delivery_does_not_queue(self):
        from unittest.mock import patch

        with patch("notifyhub.router.get_sender") as get_sender:
            get_sender.return_value = lambda *a, **k: None
            results = dispatch(self.notification(), self.config, self.logger)

        self.assertTrue(results[0][1])
        self.assertEqual(len(Outbox(self.config["outbox"], self.logger)), 0)

    def test_replay_delivers_then_clears(self):
        from unittest.mock import patch

        with patch("notifyhub.router.get_sender") as get_sender:
            get_sender.return_value = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            dispatch(self.notification(), self.config, self.logger)
        self.assertEqual(len(Outbox(self.config["outbox"], self.logger)), 1)

        with patch("notifyhub.router.get_sender") as get_sender:
            get_sender.return_value = lambda *a, **k: None
            delivered, failed, remaining = replay_outbox(self.config, self.logger, limit=10)

        self.assertEqual((delivered, failed, remaining), (1, 0, 0))
        self.assertEqual(len(Outbox(self.config["outbox"], self.logger)), 0)

    def test_replay_is_idempotent(self):
        from unittest.mock import patch

        with patch("notifyhub.router.get_sender") as get_sender:
            get_sender.return_value = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            dispatch(self.notification(), self.config, self.logger)

        with patch("notifyhub.router.get_sender") as get_sender:
            get_sender.return_value = lambda *a, **k: None
            replay_outbox(self.config, self.logger)
            delivered, _, remaining = replay_outbox(self.config, self.logger)

        self.assertEqual(delivered, 0)
        self.assertEqual(remaining, 0)

    def test_replay_records_failure_with_backoff(self):
        from unittest.mock import patch

        with patch("notifyhub.router.get_sender") as get_sender:
            get_sender.return_value = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            dispatch(self.notification(), self.config, self.logger)
            delivered, failed, remaining = replay_outbox(self.config, self.logger)

        self.assertEqual((delivered, failed, remaining), (0, 1, 1))
        entry = Outbox(self.config["outbox"], self.logger).entries()[0]
        self.assertEqual(entry["attempts"], 1)
        self.assertFalse(entry["dead"])

    def test_no_enabled_channel_does_not_queue(self):
        config = deep_merge(
            self.config,
            {"channels": {"feishu": {"enabled": False}, "feishu_app": {"enabled": False}}},
        )
        results = dispatch(self.notification(), config, self.logger)
        self.assertEqual(results, [])
        self.assertEqual(len(Outbox(config["outbox"], self.logger)), 0)

    def test_enabled_but_unconfigured_channel_is_skipped_not_queued(self):
        config = deep_merge(
            self.config,
            {"channels": {"feishu": {"enabled": True, "webhook_url": ""}}},
        )
        with self.assertLogs(self.logger, level="WARNING") as captured:
            results = dispatch(self.notification(), config, self.logger)

        self.assertEqual(results, [])
        self.assertTrue(
            any("not configured" in line for line in captured.output),
            captured.output,
        )
        # A config mistake is not a delivery failure: nothing to replay.
        self.assertEqual(len(Outbox(config["outbox"], self.logger)), 0)


if __name__ == "__main__":
    unittest.main()