import unittest

from notifyhub.adapters import dsh
from notifyhub.config import DEFAULT_CONFIG, deep_merge


def payload(**overrides):
    data = {
        "hook_event_name": "SessionComplete",
        "source": "dsh",
        "session_id": "session-1",
        "cwd": "/tmp/demo",
        "title": "帮我跑测试",
        "model": "deepseek-chat",
        "recent_prompt": "再检查一下",
        "last_assistant_message": "**已检查**",
        "status": "completed",
        "completed_at": 123,
        "seq": 5,
        "origin": "main",
    }
    data.update(overrides)
    return data


class DshAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config = deep_merge(DEFAULT_CONFIG, {})

    def test_completed_session(self):
        notification = dsh.parse(payload(), self.config)
        self.assertEqual(notification.status_text, "✅ DSH 会话完成")
        self.assertEqual(notification.project, "demo")
        self.assertEqual(notification.title, "帮我跑测试")
        self.assertEqual(notification.recent_prompt, "再检查一下")
        self.assertEqual(notification.last_message, "**已检查**")
        self.assertEqual(notification.dedupe_key, "dsh:session-1:5:completed")

    def test_failed_session(self):
        notification = dsh.parse(payload(status="error"), self.config)
        self.assertEqual(notification.status_text, "❌ DSH 会话失败")
        self.assertEqual(notification.color, "red")

    def test_subagent_is_skipped_by_default(self):
        self.assertIsNone(dsh.parse(payload(origin="subagent"), self.config))

    def test_subagent_can_be_included(self):
        config = deep_merge(self.config, {"dsh": {"include_subagents": True}})
        self.assertIsNotNone(dsh.parse(payload(origin="subagent"), config))

    def test_prompt_and_message_can_be_hidden(self):
        config = deep_merge(
            self.config,
            {"dsh": {"include_recent_prompt": False, "include_last_message": False}},
        )
        notification = dsh.parse(payload(), config)
        self.assertEqual(notification.recent_prompt, "")
        self.assertEqual(notification.last_message, "")

    def test_truncation(self):
        config = deep_merge(
            self.config,
            {"dsh": {"max_prompt_chars": 5, "max_message_chars": 5}},
        )
        notification = dsh.parse(payload(recent_prompt="123456789", last_assistant_message="abcdefghij"), config)
        self.assertEqual(len(notification.recent_prompt), 5)
        self.assertEqual(len(notification.last_message), 5)
        self.assertTrue(notification.recent_prompt.endswith("…"))
        self.assertTrue(notification.last_message.endswith("…"))

    def test_file_uri_cwd(self):
        notification = dsh.parse(payload(cwd="file:///tmp/my%20project"), self.config)
        self.assertEqual(notification.cwd, "/tmp/my project")
        self.assertEqual(notification.project, "my project")


if __name__ == "__main__":
    unittest.main()
