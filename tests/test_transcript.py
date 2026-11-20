"""Tests for transcript digests: reading a dsh session without a write handle."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from notifyhub import transcript


def _event(kind, data):
    return json.dumps({"type": kind, "data": data}, ensure_ascii=False)


def _user(text):
    return _event("user/message", {"content": [{"type": "text", "text": text}]})


def _assistant(text):
    return _event(
        "assistant/message", {"message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    )


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="notifyhub-transcript-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.session_id = "session-abc12345"
        self.session_dir = self.home / "sessions" / "--tmp-work--" / self.session_id
        self.session_dir.mkdir(parents=True)

    def write(self, lines):
        # Plain jsonl, not zstd: build_digest reads either.
        (self.session_dir / "session.v4.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    # ---------------------------------------------------------- locating

    def test_finds_session_with_prefix(self):
        found = transcript.find_session_dir(self.session_id, self.home)
        self.assertEqual(found, self.session_dir)

    def test_finds_session_without_prefix(self):
        found = transcript.find_session_dir("abc12345", self.home)
        self.assertEqual(found, self.session_dir)

    def test_missing_session_returns_none(self):
        self.assertIsNone(transcript.find_session_dir("session-nope", self.home))
        self.assertIsNone(transcript.find_session_dir("", self.home))

    # --------------------------------------------------------- digesting

    def test_pairs_turns(self):
        self.write(
            [
                _user("第一个问题"),
                _assistant("第一个回答"),
                _user("第二个问题"),
                _assistant("第二个回答"),
            ]
        )
        digest = transcript.build_digest(self.session_id, self.home)
        self.assertEqual(len(digest.exchanges), 2)
        self.assertEqual(digest.exchanges[0].user, "第一个问题")
        self.assertEqual(digest.exchanges[0].assistant, "第一个回答")
        self.assertEqual(digest.exchanges[1].user, "第二个问题")
        self.assertEqual(digest.total_turns, 2)
        self.assertFalse(digest.truncated)

    def test_keeps_only_recent_turns(self):
        lines = []
        for index in range(10):
            lines += [_user(f"问题{index}"), _assistant(f"回答{index}")]
        self.write(lines)
        digest = transcript.build_digest(self.session_id, self.home, turns=3)
        self.assertEqual(len(digest.exchanges), 3)
        self.assertEqual(digest.total_turns, 10)
        self.assertEqual(digest.exchanges[-1].user, "问题9")
        self.assertTrue(digest.truncated)

    def test_reports_the_title(self):
        self.write(
            [
                _event("session/title", {"title": "调研飞书开放平台"}),
                _user("问题"),
                _assistant("回答"),
            ]
        )
        digest = transcript.build_digest(self.session_id, self.home)
        self.assertEqual(digest.title, "调研飞书开放平台")

    def test_ignores_synthetic_user_messages(self):
        # Tool results and reminders are injected as user/message with a
        # source kind; they are not turns the user typed.
        self.write(
            [
                _user("真正的问题"),
                _assistant("真正的回答"),
                _event(
                    "user/message",
                    {
                        "content": [{"type": "text", "text": "工具结果"}],
                        "source": {"kind": "tool"},
                    },
                ),
            ]
        )
        digest = transcript.build_digest(self.session_id, self.home)
        self.assertEqual(digest.total_turns, 1)
        self.assertEqual(digest.exchanges[0].user, "真正的问题")

    def test_tool_calls_are_summarised_not_dumped(self):
        self.write(
            [
                _user("跑一下"),
                _event(
                    "assistant/message",
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool-call",
                                    "id": "call_1",
                                    "name": "bash",
                                    "arguments": '{"command": "ls"}',
                                },
                                {"type": "text", "text": "跑好了"},
                            ],
                        }
                    },
                ),
            ]
        )
        digest = transcript.build_digest(self.session_id, self.home)
        reply = digest.exchanges[0].assistant
        self.assertIn("[调用 bash]", reply)
        self.assertIn("跑好了", reply)
        self.assertNotIn("command", reply)

    def test_character_budget_drops_oldest_first(self):
        lines = []
        for index in range(6):
            lines += [_user("长" * 500 + str(index)), _assistant("答" * 500)]
        self.write(lines)
        digest = transcript.build_digest(
            self.session_id, self.home, turns=6, max_chars=1200
        )
        self.assertLessEqual(len(digest.exchanges), 2)
        self.assertTrue(digest.truncated)
        # The newest turn must survive the trimming.
        self.assertIn("5", digest.exchanges[-1].user)

    def test_truncated_tail_is_tolerated(self):
        # A session being written right now can end mid-line.
        path = self.session_dir / "session.v4.jsonl"
        path.write_text(
            _user("完整问题") + "\n" + _assistant("完整回答") + "\n" + '{"type":"step/st',
            encoding="utf-8",
        )
        digest = transcript.build_digest(self.session_id, self.home)
        self.assertEqual(len(digest.exchanges), 1)

    def test_empty_session_yields_empty_digest(self):
        self.write([_event("session/title", {"title": "空"})])
        digest = transcript.build_digest(self.session_id, self.home)
        self.assertTrue(digest.empty)
        self.assertEqual(transcript.render_digest(digest), "")

    # --------------------------------------------------------- rendering

    def test_render_marks_history_as_read_only(self):
        self.write([_user("问题"), _assistant("回答")])
        digest = transcript.build_digest(self.session_id, self.home)
        text = transcript.render_digest(digest)
        self.assertIn("只读", text)
        self.assertIn("用户：问题", text)
        self.assertIn("你：回答", text)
        self.assertIn("以上是历史", text)

    def test_render_states_the_total_when_truncated(self):
        lines = []
        for index in range(5):
            lines += [_user(f"问题{index}"), _assistant(f"回答{index}")]
        self.write(lines)
        digest = transcript.build_digest(self.session_id, self.home, turns=2)
        text = transcript.render_digest(digest)
        self.assertIn("共 5 轮", text)


if __name__ == "__main__":
    unittest.main()