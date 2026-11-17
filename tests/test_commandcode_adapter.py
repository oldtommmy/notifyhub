import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notifyhub.adapters import commandcode
from notifyhub.config import DEFAULT_CONFIG, deep_merge


def write_transcript(directory, events):
    path = Path(directory) / "transcript.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )
    return str(path)


class CommandCodeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = deep_merge(DEFAULT_CONFIG, {"commandcode": {"events": ["Stop"]}})

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, path, **overrides):
        data = {
            "session_id": "c1",
            "transcript_path": path,
            "cwd": "/tmp/work/cc",
            "hook_event_name": "Stop",
        }
        data.update(overrides)
        return data

    def test_stop_uses_text_only_and_last_real_prompt(self):
        path = write_transcript(
            self.temp.name,
            [
                {"type": "session", "version": 3, "id": "c1", "cwd": "/tmp/work/cc"},
                {"type": "model_change", "model": "deepseek/deepseek-v4.1-flash"},
                {
                    "type": "message",
                    "id": "u1",
                    "message": {"role": "user", "content": [{"type": "text", "text": "跑一下测试"}]},
                },
                {
                    "type": "message",
                    "id": "a1",
                    "model": "deepseek/deepseek-v4.1-flash",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "SECRET"},
                            {"type": "text", "text": "测试通过"},
                        ],
                    },
                },
                {
                    "type": "message",
                    "id": "u2",
                    "message": {"role": "user", "content": [{"type": "text", "text": "再跑一次"}]},
                },
                {
                    "type": "message",
                    "id": "a2",
                    "model": "deepseek/deepseek-v4.1-flash",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "SECRET2"},
                            {"type": "text", "text": "第二次通过"},
                        ],
                    },
                },
            ],
        )

        notification = commandcode.parse(self.payload(path), self.config)

        self.assertEqual(notification.status_text, "✅ 本轮完成")
        self.assertEqual(notification.title, "跑一下测试")
        self.assertEqual(notification.recent_prompt, "再跑一次")
        self.assertEqual(notification.last_message, "第二次通过")
        self.assertNotIn("SECRET", notification.last_message)
        self.assertEqual(notification.model, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(notification.project, "cc")
        self.assertEqual(notification.dedupe_key, "commandcode:Stop:c1:a2")

    def test_model_falls_back_to_config(self):
        path = write_transcript(
            self.temp.name,
            [
                {
                    "type": "message",
                    "id": "a1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                    },
                }
            ],
        )
        with patch("notifyhub.adapters.commandcode._config_model", return_value="config-model"):
            notification = commandcode.parse(self.payload(path), self.config)
        self.assertEqual(notification.model, "config-model")

    def test_disabled_and_event_filter(self):
        path = write_transcript(self.temp.name, [])
        self.assertIsNone(commandcode.parse(self.payload(path, hook_event_name="SessionEnd"), self.config))

        config = deep_merge(self.config, {"commandcode": {"enabled": False}})
        self.assertIsNone(commandcode.parse(self.payload(path), config))


if __name__ == "__main__":
    unittest.main()