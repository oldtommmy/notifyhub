import json
import tempfile
import unittest
from pathlib import Path

from notifyhub.adapters import claude
from notifyhub.config import DEFAULT_CONFIG, deep_merge


def write_transcript(directory, events):
    path = Path(directory) / "transcript.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )
    return str(path)


class ClaudeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = deep_merge(DEFAULT_CONFIG, {"claude": {"events": ["Stop"]}})

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, path, **overrides):
        data = {
            "session_id": "s1",
            "transcript_path": path,
            "cwd": "/tmp/work/demo",
            "hook_event_name": "Stop",
        }
        data.update(overrides)
        return data

    def test_stop_uses_text_only_and_ai_title(self):
        path = write_transcript(
            self.temp.name,
            [
                {"type": "ai-title", "aiTitle": "修复登录", "sessionId": "s1"},
                {
                    "type": "user",
                    "uuid": "u1",
                    "sessionId": "s1",
                    "message": {"role": "user", "content": "帮我修登录"},
                },
                {
                    "type": "user",
                    "uuid": "u-synthetic",
                    "sessionId": "s1",
                    "message": {"role": "user", "content": "<system-reminder>ignore me</system-reminder>"},
                },
                {
                    "type": "assistant",
                    "uuid": "a1",
                    "sessionId": "s1",
                    "message": {
                        "role": "assistant",
                        "model": "deepseek-flash",
                        "content": [
                            {"type": "thinking", "thinking": "SECRET"},
                            {"type": "text", "text": "已经修好了。"},
                        ],
                    },
                },
                {
                    "type": "assistant",
                    "uuid": "side",
                    "sessionId": "s1",
                    "isSidechain": True,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "SIDE CHAIN"}],
                    },
                },
                {"type": "last-prompt", "lastPrompt": "帮我修登录", "sessionId": "s1"},
            ],
        )

        notification = claude.parse(self.payload(path), self.config)

        self.assertEqual(notification.status_text, "✅ 本轮完成")
        self.assertEqual(notification.title, "修复登录")
        self.assertEqual(notification.recent_prompt, "帮我修登录")
        self.assertEqual(notification.last_message, "已经修好了。")
        self.assertNotIn("SECRET", notification.last_message)
        self.assertNotIn("SIDE CHAIN", notification.last_message)
        self.assertEqual(notification.model, "deepseek-flash")
        self.assertEqual(notification.project, "demo")
        self.assertEqual(notification.dedupe_key, "claude:Stop:s1:a1")

    def test_missing_transcript_falls_back_to_payload(self):
        notification = claude.parse(
            {
                "session_id": "s2",
                "cwd": "/tmp/work/fallback",
                "hook_event_name": "Stop",
                "prompt": "帮我看看",
                "last_assistant_message": "看过了。",
                "model": "claude-test",
            },
            self.config,
        )

        self.assertEqual(notification.title, "fallback")
        self.assertEqual(notification.recent_prompt, "帮我看看")
        self.assertEqual(notification.last_message, "看过了。")
        self.assertEqual(notification.model, "claude-test")

    def test_event_not_enabled_and_disabled_setting(self):
        path = write_transcript(self.temp.name, [])
        self.assertIsNone(claude.parse(self.payload(path, hook_event_name="SessionEnd"), self.config))

        config = deep_merge(self.config, {"claude": {"enabled": False}})
        self.assertIsNone(claude.parse(self.payload(path), config))

    def test_truncation(self):
        config = deep_merge(
            self.config,
            {"claude": {"max_message_chars": 5, "max_prompt_chars": 5}},
        )
        path = write_transcript(
            self.temp.name,
            [
                {
                    "type": "assistant",
                    "uuid": "a1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "abcdefghij"}],
                    },
                },
                {"type": "last-prompt", "lastPrompt": "123456789"},
            ],
        )
        notification = claude.parse(self.payload(path), config)
        self.assertEqual(len(notification.last_message), 5)
        self.assertEqual(len(notification.recent_prompt), 5)
        self.assertTrue(notification.last_message.endswith("…"))
        self.assertTrue(notification.recent_prompt.endswith("…"))


if __name__ == "__main__":
    unittest.main()