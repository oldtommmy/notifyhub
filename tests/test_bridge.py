import fcntl
import json
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from notifyhub import agents, bridge
from notifyhub.config import DEFAULT_CONFIG, deep_merge
from notifyhub.events import Notification
from notifyhub.feishu_listener import bot_mentions_text, extract_text
from notifyhub import bridge_cli, transcript


def config_with(**bridge_settings):
    base = {
        "enabled": True,
        "allow_open_ids": ["ou_allowed"],
        "default_agent": "dsh",
        "workspace": "~",
        "timeout_seconds": 30,
    }
    base.update(bridge_settings)
    return deep_merge(DEFAULT_CONFIG, {"bridge": base})


# --------------------------------------------------------------- 白名单安全


class AllowlistTests(unittest.TestCase):
    def test_empty_allowlist_denies_everyone(self):
        # Fail closed: a half-configured bridge must not be an open shell.
        config = config_with(allow_open_ids=[])
        self.assertFalse(bridge.is_allowed("ou_anyone", config))
        self.assertFalse(bridge.is_allowed("", config))

    def test_listed_sender_is_allowed(self):
        config = config_with(allow_open_ids=["ou_allowed"])
        self.assertTrue(bridge.is_allowed("ou_allowed", config))

    def test_unlisted_sender_is_denied(self):
        config = config_with(allow_open_ids=["ou_allowed"])
        self.assertFalse(bridge.is_allowed("ou_other", config))

    def test_whitespace_is_normalised(self):
        config = config_with(allow_open_ids=["  ou_allowed  "])
        self.assertTrue(bridge.is_allowed("ou_allowed", config))

    def test_missing_sender_is_denied(self):
        config = config_with()
        self.assertFalse(bridge.is_allowed(None, config))


# ----------------------------------------------------------------- 命令解析


class ParseCommandTests(unittest.TestCase):
    def test_bare_message_uses_default_agent(self):
        kind, payload = bridge.parse_command("帮我跑个测试", config_with(default_agent="dsh"))
        self.assertEqual(kind, "agent")
        self.assertEqual(payload, ("dsh", "帮我跑个测试"))

    def test_default_agent_can_be_codex(self):
        _, payload = bridge.parse_command("hi", config_with(default_agent="codex"))
        self.assertEqual(payload[0], "codex")

    def test_at_prefix_switches_agent(self):
        _, payload = bridge.parse_command("@codex 检查一下", config_with())
        self.assertEqual(payload, ("codex", "检查一下"))

    def test_configured_prefix_switches_agent(self):
        config = config_with(prefixes={"dsh": "!", "codex": "@codex "})
        _, payload = bridge.parse_command("!继续", config)
        self.assertEqual(payload, ("dsh", "继续"))

    def test_local_commands(self):
        self.assertEqual(bridge.parse_command("/new", config_with())[0], "new")
        self.assertEqual(bridge.parse_command("/status", config_with())[0], "status")
        self.assertEqual(bridge.parse_command("/help", config_with())[0], "help")
        self.assertEqual(bridge.parse_command("/handoff", config_with()), ("handoff", ""))
        self.assertEqual(bridge.parse_command("/handoff 3", config_with()), ("handoff", "3"))
        self.assertEqual(bridge.parse_command("/takeover", config_with()), ("handoff", ""))
        self.assertEqual(bridge.parse_command("/接续", config_with())[0], "handoff")

    def test_empty_message_is_rejected(self):
        with self.assertRaises(bridge.BridgeDenied):
            bridge.parse_command("   ", config_with())

    def test_prefix_without_prompt_is_rejected(self):
        with self.assertRaises(bridge.BridgeDenied):
            bridge.parse_command("@codex   ", config_with())

    def test_invalid_default_agent_is_rejected(self):
        with self.assertRaises(bridge.BridgeDenied):
            bridge.parse_command("hi", config_with(default_agent="rm -rf"))


# ------------------------------------------------------------------ 内容解析


class ExtractTextTests(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(extract_text('{"text": "你好"}', "text"), "你好")

    def test_post_rich_text(self):
        content = json.dumps(
            {"content": [[{"tag": "text", "text": "第一行"}, {"tag": "text", "text": "第二行"}]]}
        )
        self.assertEqual(extract_text(content, "post"), "第一行\n第二行")

    def test_unknown_type_returns_empty(self):
        self.assertEqual(extract_text('{"image_key": "x"}', "image"), "")

    def test_malformed_json_does_not_crash(self):
        self.assertEqual(extract_text("not json", "text"), "not json")

    def test_bot_mention_token_is_stripped(self):
        self.assertEqual(bot_mentions_text("@_user_1 帮我看看"), "帮我看看")


# --------------------------------------------------------------- 会话映射表


class SessionMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "sessions.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_set_and_get_persists(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("chat-1", "dsh", "session-abc")
        self.assertEqual(sessions.get("chat-1", "dsh"), "session-abc")
        # Re-read from disk.
        self.assertEqual(bridge.SessionMap(self.path).get("chat-1", "dsh"), "session-abc")

    def test_agents_are_independent(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("chat-1", "dsh", "session-d")
        sessions.set("chat-1", "codex", "session-c")
        self.assertEqual(sessions.get("chat-1", "dsh"), "session-d")
        self.assertEqual(sessions.get("chat-1", "codex"), "session-c")

    def test_chats_are_isolated(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("chat-1", "dsh", "session-a")
        sessions.set("chat-2", "dsh", "session-b")
        self.assertEqual(sessions.get("chat-1", "dsh"), "session-a")
        self.assertEqual(sessions.get("chat-2", "dsh"), "session-b")

    def test_reset_clears_one_chat(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("chat-1", "dsh", "session-a")
        sessions.set("chat-2", "dsh", "session-b")
        self.assertTrue(sessions.reset("chat-1"))
        self.assertEqual(sessions.get("chat-1", "dsh"), "")
        self.assertEqual(sessions.get("chat-2", "dsh"), "session-b")

    def test_empty_session_id_is_ignored(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("chat-1", "dsh", "")
        self.assertEqual(sessions.get("chat-1", "dsh"), "")

    def test_all_sessions_returns_copy(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("chat-1", "dsh", "session-a")
        snapshot = sessions.all_sessions()
        snapshot["chat-1"]["dsh"] = "mutated"
        self.assertEqual(sessions.get("chat-1", "dsh"), "session-a")

    def test_reset_during_run_does_not_resurrect_session(self):
        # /new while a task is running must win: the finishing run cannot write
        # its old session back.
        sessions = bridge.SessionMap(self.path)
        sessions.set("chat-1", "dsh", "session-old")
        generation = sessions.generation("chat-1")
        sessions.reset("chat-1")
        sessions.set("chat-1", "dsh", "session-from-finished-run", generation=generation)
        self.assertEqual(sessions.get("chat-1", "dsh"), "")

    def test_matching_generation_is_saved(self):
        sessions = bridge.SessionMap(self.path)
        generation = sessions.generation("chat-1")
        sessions.set("chat-1", "dsh", "session-fine", generation=generation)
        self.assertEqual(sessions.get("chat-1", "dsh"), "session-fine")


# -------------------------------------------------------------------- 串行保护


class RunGuardTests(unittest.TestCase):
    def test_same_key_cannot_run_twice(self):
        guard = bridge.RunGuard()
        self.assertTrue(guard.acquire(("chat", "dsh")))
        self.assertFalse(guard.acquire(("chat", "dsh")))

    def test_different_agents_can_run_concurrently(self):
        guard = bridge.RunGuard()
        self.assertTrue(guard.acquire(("chat", "dsh")))
        self.assertTrue(guard.acquire(("chat", "codex")))

    def test_release_allows_reacquire(self):
        guard = bridge.RunGuard()
        guard.acquire(("chat", "dsh"))
        guard.release(("chat", "dsh"))
        self.assertTrue(guard.acquire(("chat", "dsh")))

    def test_busy_reports_state(self):
        guard = bridge.RunGuard()
        self.assertFalse(guard.busy(("chat", "dsh")))
        guard.acquire(("chat", "dsh"))
        self.assertTrue(guard.busy(("chat", "dsh")))


# ------------------------------------------------------------------ dsh 解析


class DshRunnerTests(unittest.TestCase):
    """Parse the NDJSON that `dsh headless --json` actually emits."""

    def output(self, lines):
        class Done:
            returncode = 0
            stdout = "\n".join(json.dumps(line) for line in lines)
            stderr = ""

        return Done()

    def test_success_with_session_and_text(self):
        done = self.output(
            [
                {"type": "session", "sessionId": "session-1", "cwd": "/tmp"},
                {"type": "status", "phase": "turn_start", "turn": 1},
                {
                    "type": "final",
                    "sessionId": "session-1",
                    "reason": {"kind": "completed"},
                    "text": "完成",
                },
            ]
        )
        with patch.object(agents, "_binary", return_value="/usr/bin/dsh"), patch.object(
            agents, "_run", return_value=done
        ):
            result = agents.dsh_runner("", "hi", "/tmp", 10)

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "完成")
        self.assertEqual(result.session_id, "session-1")

    def test_error_reason_is_reported(self):
        done = self.output(
            [
                {"type": "session", "sessionId": "session-1"},
                {
                    "type": "final",
                    "sessionId": "session-1",
                    "reason": {"kind": "error", "error": {"message": "boom"}},
                    "text": "",
                },
            ]
        )
        with patch.object(agents, "_binary", return_value="/usr/bin/dsh"), patch.object(
            agents, "_run", return_value=done
        ):
            result = agents.dsh_runner("", "hi", "/tmp", 10)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "boom")

    def test_startup_failure_without_events_is_reported(self):
        # e.g. a broken profile: dsh dies before emitting any event.
        class Done:
            returncode = 1
            stdout = ""
            stderr = "dsh: cannot initialise profile"

        with patch.object(agents, "_binary", return_value="/usr/bin/dsh"), patch.object(
            agents, "_run", return_value=Done()
        ):
            result = agents.dsh_runner("", "hi", "/tmp", 10)
        self.assertFalse(result.ok)
        self.assertIn("cannot initialise", result.error)

    def test_missing_credential_with_session_event_still_raises(self):
        # Real dsh emits a session event first, then fails on credentials.
        # This is the case observed against a freshly created headless profile.
        done = self.output(
            [
                {"type": "session", "sessionId": "session-c", "cwd": "/tmp"},
                {"type": "status", "phase": "turn_start", "turn": 1},
                {
                    "type": "final",
                    "sessionId": "session-c",
                    "reason": {
                        "kind": "error",
                        "error": {
                            "message": 'llm-deepseek: no API key for provider route "deepseek-official"',
                            "code": "MISSING_CREDENTIAL",
                        },
                    },
                    "text": "",
                },
            ]
        )
        with patch.object(agents, "_binary", return_value="/usr/bin/dsh"), patch.object(
            agents, "_run", return_value=done
        ):
            with self.assertRaises(agents.RunnerError) as ctx:
                agents.dsh_runner("", "hi", "/tmp", 10)
        message = str(ctx.exception)
        self.assertIn("MISSING_CREDENTIAL", message)
        self.assertIn("DEEPSEEK_API_KEY", message)

    def test_resume_passes_session_id(self):
        captured = {}

        class Done:
            returncode = 0
            stdout = json.dumps({"type": "final", "reason": {"kind": "completed"}, "text": "ok"})
            stderr = ""

        def fake_run(command, prompt, cwd, timeout, env=None):
            captured["command"] = command
            return Done()

        with patch.object(agents, "_binary", return_value="/usr/bin/dsh"), patch.object(
            agents, "_run", side_effect=fake_run
        ):
            agents.dsh_runner("session-9", "continue", "/tmp", 10, approval_policy="never")

        self.assertIn("--session-id", captured["command"])
        self.assertIn("session-9", captured["command"])
        self.assertIn("--approval-policy", captured["command"])
        self.assertIn("never", captured["command"])

    def test_no_session_id_means_no_resume_flag(self):
        captured = {}

        class Done:
            returncode = 0
            stdout = json.dumps({"type": "final", "reason": {"kind": "completed"}, "text": "ok"})
            stderr = ""

        def fake_run(command, prompt, cwd, timeout, env=None):
            captured["command"] = command
            return Done()

        with patch.object(agents, "_binary", return_value="/usr/bin/dsh"), patch.object(
            agents, "_run", side_effect=fake_run
        ):
            agents.dsh_runner("", "first", "/tmp", 10)

        self.assertNotIn("--session-id", captured["command"])


# ---------------------------------------------------------------- codex 解析


class CodexRunnerTests(unittest.TestCase):
    def output(self, lines, code=0):
        class Done:
            returncode = code
            stdout = "\n".join(json.dumps(line) for line in lines)
            stderr = ""

        return Done()

    def test_agent_message_and_session(self):
        done = self.output(
            [
                {"type": "session_configured", "session_id": "abc-123"},
                {"type": "agent_message", "message": "done"},
            ]
        )
        with patch.object(agents, "_binary", return_value="/usr/bin/codex"), patch.object(
            agents, "_run", return_value=done
        ):
            result = agents.codex_runner("", "hi", "/tmp", 10)

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "done")
        self.assertEqual(result.session_id, "abc-123")

    def test_nonzero_exit_is_failure(self):
        done = self.output([{"type": "agent_message", "message": "partial"}], code=1)
        with patch.object(agents, "_binary", return_value="/usr/bin/codex"), patch.object(
            agents, "_run", return_value=done
        ):
            result = agents.codex_runner("", "hi", "/tmp", 10)
        self.assertFalse(result.ok)

    def test_resume_included_in_command(self):
        captured = {}

        class Done:
            returncode = 0
            stdout = json.dumps({"type": "agent_message", "message": "ok"})
            stderr = ""

        def fake_run(command, prompt, cwd, timeout, env=None):
            captured["command"] = command
            return Done()

        with patch.object(agents, "_binary", return_value="/usr/bin/codex"), patch.object(
            agents, "_run", side_effect=fake_run
        ):
            agents.codex_runner("sess-1", "go", "/tmp", 10)

        self.assertIn("resume", captured["command"])
        self.assertIn("sess-1", captured["command"])

    def test_missing_binary_raises(self):
        with patch.object(agents, "_binary", return_value=""):
            with self.assertRaises(agents.RunnerError):
                agents.codex_runner("", "hi", "/tmp", 10)


class RunnerSelectionTests(unittest.TestCase):
    def test_known_runners(self):
        self.assertIs(agents.get_runner("dsh"), agents.dsh_runner)
        self.assertIs(agents.get_runner("codex"), agents.codex_runner)

    def test_unknown_runner_rejected(self):
        with self.assertRaises(agents.RunnerError):
            agents.get_runner("bash")


# ------------------------------------------------------------------ run_task


class RunTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "sessions.json")
        self.sessions = bridge.SessionMap(self.path)
        self.task = bridge.Task(
            message_id="m1", chat_id="chat-1", sender_id="ou_allowed", prompt="hi", agent="dsh"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_session_is_saved_after_run(self):
        def runner(session_id, prompt, cwd, timeout, **kwargs):
            return agents.RunResult(ok=True, text="ok", session_id="session-new")

        result = bridge.run_task(self.task, config_with(), self.sessions, runner=runner)
        self.assertTrue(result.ok)
        self.assertEqual(self.sessions.get("chat-1", "dsh"), "session-new")

    def test_existing_session_is_resumed(self):
        self.sessions.set("chat-1", "dsh", "session-old")
        seen = {}

        def runner(session_id, prompt, cwd, timeout, **kwargs):
            seen["session_id"] = session_id
            return agents.RunResult(ok=True, text="ok", session_id=session_id)

        bridge.run_task(self.task, config_with(), self.sessions, runner=runner)
        self.assertEqual(seen["session_id"], "session-old")

    def test_runner_error_becomes_failed_result(self):
        def runner(*args, **kwargs):
            raise agents.RunnerError("找不到 dsh")

        result = bridge.run_task(self.task, config_with(), self.sessions, runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("找不到 dsh", result.error)

    def test_unexpected_exception_is_contained(self):
        def runner(*args, **kwargs):
            raise ValueError("surprise")

        result = bridge.run_task(self.task, config_with(), self.sessions, runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("surprise", result.error)

    def test_locked_dsh_session_is_refused(self):
        self.sessions.set("chat-1", "dsh", "session-busy")
        with patch.object(agents, "session_in_use", return_value=True):
            result = bridge.run_task(self.task, config_with(), self.sessions)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, bridge.LOCKED_HINT)

    def test_handoff_context_reaches_the_runner(self):
        task = bridge.Task(
            message_id="m",
            chat_id="chat-1",
            sender_id="ou",
            prompt="接着说",
            agent="dsh",
            topic="main",
            context="【历史】之前聊了 A",
        )
        seen = {}

        def runner(*args, **kwargs):
            seen.update(kwargs)
            return agents.RunResult(ok=True, exit_code=0, text="好", session_id="session-1")

        bridge.run_task(task, config_with(), self.sessions, runner=runner)
        self.assertEqual(seen.get("context"), "【历史】之前聊了 A")

    def test_no_context_means_no_context_kwarg(self):
        seen = {}

        def runner(*args, **kwargs):
            seen.update(kwargs)
            return agents.RunResult(ok=True, exit_code=0, text="好", session_id="session-1")

        bridge.run_task(self.task, config_with(), self.sessions, runner=runner)
        self.assertNotIn("context", seen)

    def test_conflict_mode_is_passed_from_config(self):
        # The probe takes no tuning knobs any more: only a held flock blocks.
        self.sessions.set("chat-1", "dsh", "session-busy")
        seen = {}

        def fake(agent, session_id):
            seen["agent"] = agent
            seen["session"] = session_id
            return True

        with patch.object(agents, "session_in_use", side_effect=fake):
            bridge.run_task(self.task, config_with(), self.sessions)
        self.assertEqual(seen["agent"], "dsh")
        self.assertEqual(seen["session"], "session-busy")

    def test_owned_error_becomes_a_lock_hint(self):
        # The desktop app can grab the write handle between the probe and
        # dsh's own write open; that race must read as "try again", not as a
        # mysterious dsh crash.
        self.sessions.set("chat-1", "dsh", "session-busy")

        def runner(*args, **kwargs):
            return agents.RunResult(
                ok=False,
                exit_code=1,
                error='session "session-busy" is already owned by an active write handle',
                session_id="session-busy",
            )

        result = bridge.run_task(self.task, config_with(), self.sessions, runner=runner)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, bridge.LOCKED_HINT)


# ------------------------------------------------------------- 结果卡片


class ResultNotificationTests(unittest.TestCase):
    def test_success_card(self):
        task = bridge.Task(
            message_id="m", chat_id="c", sender_id="ou", prompt="run", agent="dsh"
        )
        result = bridge.TaskResult(
            task=task, ok=True, text="全部通过", session_id="session-1", duration_seconds=65
        )
        notification = bridge_cli.build_result_notification(task, result, config_with())
        self.assertIn("完成", notification.status_text)
        self.assertEqual(notification.color, "green")
        self.assertEqual(notification.last_message, "全部通过")
        self.assertIn("1m05s", " ".join(notification.extra_lines))

    def test_failure_card_shows_error(self):
        task = bridge.Task(message_id="m", chat_id="c", sender_id="ou", prompt="run", agent="codex")
        result = bridge.TaskResult(task=task, ok=False, error="超时（>30s）")
        notification = bridge_cli.build_result_notification(task, result, config_with())
        self.assertIn("失败", notification.status_text)
        self.assertEqual(notification.color, "red")
        self.assertIn("超时", notification.last_message)


# ------------------------------------------------------------ 监听器校验


class ListenerTests(unittest.TestCase):
    def make_event(self, sender_open_id="ou_allowed", text="hi", message_type="text", message_id="om_1"):
        class SenderId:
            open_id = sender_open_id

        class Sender:
            sender_id = SenderId()

        class Message:
            chat_id = "chat-1"
            chat_type = "p2p"
            content = json.dumps({"text": text})

        # Assign after the class body: a class-scope name cannot reference the
        # enclosing function parameter of the same name.
        Message.message_type = message_type
        Message.message_id = message_id

        class Data:
            sender = Sender()
            message = Message()

        class Event:
            event = Data()

        return Event()

    def test_allowed_message_reaches_handler(self):
        from notifyhub.feishu_listener import FeishuListener

        received = []
        listener = FeishuListener(config_with(), lambda **kw: received.append(kw))
        listener.handle_event(self.make_event())
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["payload"], ("dsh", "hi"))

    def test_denied_sender_never_reaches_handler(self):
        from notifyhub.feishu_listener import FeishuListener

        received = []
        replies = []
        listener = FeishuListener(
            config_with(),
            lambda **kw: received.append(kw),
            reply=lambda message_id, text: replies.append(text),
        )
        listener.handle_event(self.make_event(sender_open_id="ou_attacker"))
        self.assertEqual(received, [])
        self.assertTrue(any("未授权" in r for r in replies))

    def test_non_text_message_type_ignored(self):
        from notifyhub.feishu_listener import FeishuListener

        received = []
        listener = FeishuListener(config_with(), lambda **kw: received.append(kw))
        listener.handle_event(self.make_event(message_type="image"))
        self.assertEqual(received, [])

    def test_redelivered_message_runs_once(self):
        # Feishu re-pushes an event when its ack is slow; a second run would
        # burn tokens and replay tools.
        from notifyhub.feishu_listener import FeishuListener

        received = []
        listener = FeishuListener(config_with(), lambda **kw: received.append(kw))
        listener.handle_event(self.make_event())
        listener.handle_event(self.make_event())
        self.assertEqual(len(received), 1)

    def test_distinct_message_ids_both_run(self):
        from notifyhub.feishu_listener import FeishuListener

        received = []
        listener = FeishuListener(config_with(), lambda **kw: received.append(kw))
        listener.handle_event(self.make_event())
        listener.handle_event(self.make_event(message_id="om_2"))
        self.assertEqual(len(received), 2)

    def test_seen_window_is_bounded(self):
        from notifyhub.feishu_listener import SeenMessages

        seen = SeenMessages(capacity=3)
        for i in range(5):
            self.assertTrue(seen.check_and_add("om_%d" % i))
        self.assertFalse(seen.check_and_add("om_4"))
        # The oldest id has been evicted, so it is treated as new again.
        self.assertTrue(seen.check_and_add("om_0"))

    def test_missing_message_id_is_not_dropped(self):
        from notifyhub.feishu_listener import SeenMessages

        seen = SeenMessages()
        self.assertTrue(seen.check_and_add(""))
        self.assertTrue(seen.check_and_add(""))


if __name__ == "__main__":
    unittest.main()

class _Args:
    config = None


class CredentialCheckTests(unittest.TestCase):
    """The headless profile reads apiKeyEnv from the process environment; a key
    stored in .credentials.yaml is not exported automatically."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.profile = self.home / "profiles" / "headless"
        self.profile.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_patch(self, env_name):
        (self.profile / "cordis.patch.yml").write_text(
            "providers:\n  qwen:\n    apiKeyEnv: %s\n" % env_name, encoding="utf-8"
        )

    def write_credentials(self, name):
        (self.home / ".credentials.yaml").write_text(
            "refs:\n  %s: sk-test-value\n" % name, encoding="utf-8"
        )

    def test_reads_api_key_env_name(self):
        self.write_patch("QWEN_API_KEY")
        with patch.dict("os.environ", {"DSH_HOME": str(self.home)}):
            self.assertEqual(bridge_cli._headless_api_key_env(), "QWEN_API_KEY")

    def test_no_patch_returns_none(self):
        with patch.dict("os.environ", {"DSH_HOME": str(self.home)}):
            self.assertIsNone(bridge_cli._headless_api_key_env())

    def test_stored_credential_detected(self):
        self.write_credentials("QWEN_API_KEY")
        with patch.dict("os.environ", {"DSH_HOME": str(self.home)}):
            self.assertTrue(bridge_cli._stored_credential("QWEN_API_KEY"))

    def test_missing_credential_is_false(self):
        self.write_credentials("OTHER_KEY")
        with patch.dict("os.environ", {"DSH_HOME": str(self.home)}):
            self.assertFalse(bridge_cli._stored_credential("QWEN_API_KEY"))

    def test_check_flags_stored_but_not_exported_key(self):
        self.write_patch("QWEN_API_KEY")
        self.write_credentials("QWEN_API_KEY")
        config = config_with()
        env = {k: v for k, v in __import__("os").environ.items() if k != "QWEN_API_KEY"}
        env["DSH_HOME"] = str(self.home)
        with patch.dict("os.environ", env, clear=True), patch.object(
            bridge_cli, "load_config", return_value=config
        ), patch.object(bridge_cli, "resolve_path", side_effect=lambda v: self.home):
            buffer = __import__("io").StringIO()
            with __import__("contextlib").redirect_stdout(buffer):
                code = bridge_cli.cmd_bridge_check(_Args())
        output = buffer.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("未注入环境", output)

    def test_check_passes_when_key_exported(self):
        self.write_patch("QWEN_API_KEY")
        self.write_credentials("QWEN_API_KEY")
        config = config_with()
        env = dict(__import__("os").environ)
        env["DSH_HOME"] = str(self.home)
        env["QWEN_API_KEY"] = "sk-exported"
        with patch.dict("os.environ", env, clear=True), patch.object(
            bridge_cli, "load_config", return_value=config
        ), patch.object(bridge_cli, "resolve_path", side_effect=lambda v: self.home):
            buffer = __import__("io").StringIO()
            with __import__("contextlib").redirect_stdout(buffer):
                bridge_cli.cmd_bridge_check(_Args())
        self.assertIn("已注入环境", buffer.getvalue())


class ResultSendTargetTests(unittest.TestCase):
    """The result card must go to the chat that asked, not to receive_id."""

    def test_result_is_sent_to_task_chat(self):
        from unittest.mock import MagicMock

        sent = {}

        def fake_send(notification, settings, logger=None):
            sent.update(settings)

        config = config_with()
        service = bridge_cli.BridgeService.__new__(bridge_cli.BridgeService)
        service.config = config
        service.logger = logging.getLogger("t")
        with patch.object(bridge_cli.feishu_app, "send", side_effect=fake_send):
            notification = Notification(source="dsh", event="BridgeResult", status_text="x")
            service._send(notification, "oc_the_chat")

        self.assertEqual(sent.get("receive_id"), "oc_the_chat")
        self.assertEqual(sent.get("receive_id_type"), "chat_id")

    def test_send_failure_does_not_raise(self):
        config = config_with()
        service = bridge_cli.BridgeService.__new__(bridge_cli.BridgeService)
        service.config = config
        service.logger = logging.getLogger("t")
        with patch.object(
            bridge_cli.feishu_app, "send", side_effect=RuntimeError("boom")
        ), patch.object(bridge_cli.time, "sleep"), patch.object(
            bridge_cli, "outbox_for"
        ) as outbox_for:
            outbox = outbox_for.return_value
            result = service._send(
                Notification(source="dsh", event="x", status_text="x"), "oc_1"
            )
        # All attempts failed, so the card is queued rather than dropped.
        self.assertFalse(result)
        outbox.add.assert_called_once()

    def test_retries_then_succeeds(self):
        config = config_with()
        service = bridge_cli.BridgeService.__new__(bridge_cli.BridgeService)
        service.config = config
        service.logger = logging.getLogger("t")
        calls = {"n": 0}

        def flaky(notification, settings, logger=None):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("ssl eof")

        with patch.object(bridge_cli.feishu_app, "send", side_effect=flaky), patch.object(
            bridge_cli.time, "sleep"
        ):
            result = service._send(
                Notification(source="dsh", event="x", status_text="x"), "oc_1"
            )

        self.assertTrue(result)
        self.assertEqual(calls["n"], 2)

    def test_reply_retries_once(self):
        config = config_with()
        service = bridge_cli.BridgeService.__new__(bridge_cli.BridgeService)
        service.config = config
        service.logger = logging.getLogger("t")
        calls = {"n": 0}

        def flaky(message_id, text, settings):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("handshake timeout")

        with patch.object(bridge_cli.feishu_app, "reply_text", side_effect=flaky), patch.object(
            bridge_cli.time, "sleep"
        ):
            self.assertTrue(service._reply("om_1", "hi"))
        self.assertEqual(calls["n"], 2)


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "sessions.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_default_topic_is_main(self):
        sessions = bridge.SessionMap(self.path)
        self.assertEqual(sessions.active_topic("c1"), bridge.DEFAULT_TOPIC)

    def test_topics_are_isolated(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("c1", "dsh", "session-main", topic="main")
        sessions.set("c1", "dsh", "session-front", topic="前端")
        self.assertEqual(sessions.get("c1", "dsh", "main"), "session-main")
        self.assertEqual(sessions.get("c1", "dsh", "前端"), "session-front")

    def test_switch_changes_which_session_is_used(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("c1", "dsh", "session-a", topic="A")
        sessions.set("c1", "dsh", "session-b", topic="B")
        sessions.set_active_topic("c1", "A")
        self.assertEqual(sessions.get("c1", "dsh"), "session-a")
        sessions.set_active_topic("c1", "B")
        self.assertEqual(sessions.get("c1", "dsh"), "session-b")

    def test_topics_are_persisted(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("c1", "dsh", "session-x", topic="X")
        sessions.set_active_topic("c1", "X")
        reloaded = bridge.SessionMap(self.path)
        self.assertEqual(reloaded.get("c1", "dsh"), "session-x")
        self.assertEqual(reloaded.active_topic("c1"), "X")

    def test_topic_list_is_default_first(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("c1", "dsh", "s", topic="zeta")
        sessions.set("c1", "dsh", "s2", topic="alpha")
        self.assertEqual(sessions.topics("c1"), ["main", "alpha", "zeta"])

    def test_reset_only_clears_one_topic(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("c1", "dsh", "s-main", topic="main")
        sessions.set("c1", "dsh", "s-front", topic="前端")
        sessions.set_active_topic("c1", "前端")
        self.assertTrue(sessions.reset("c1"))
        self.assertEqual(sessions.get("c1", "dsh", "前端"), "")
        self.assertEqual(sessions.get("c1", "dsh", "main"), "s-main")

    def test_drop_topic_falls_back_to_main(self):
        sessions = bridge.SessionMap(self.path)
        sessions.set("c1", "dsh", "s-front", topic="前端")
        sessions.set_active_topic("c1", "前端")
        self.assertTrue(sessions.drop_topic("c1", "前端"))
        self.assertEqual(sessions.active_topic("c1"), bridge.DEFAULT_TOPIC)
        self.assertEqual(sessions.get("c1", "dsh", "前端"), "")

    def test_legacy_file_loads_as_main(self):
        Path(self.path).write_text(
            json.dumps({"chat-1": {"dsh": "session-old"}}), encoding="utf-8"
        )
        sessions = bridge.SessionMap(self.path)
        self.assertEqual(sessions.get("chat-1", "dsh"), "session-old")

    def test_generation_is_per_topic(self):
        sessions = bridge.SessionMap(self.path)
        gen_main = sessions.generation("c1", "main")
        sessions.set("c1", "dsh", "s-front", topic="前端")
        sessions.reset("c1", "前端")
        # Resetting another topic must not invalidate this one.
        sessions.set("c1", "dsh", "s-new", generation=gen_main, topic="main")
        self.assertEqual(sessions.get("c1", "dsh", "main"), "s-new")


class TopicCommandTests(unittest.TestCase):
    def test_topic_switch_parsed(self):
        kind, name = bridge.parse_command("/topic 前端", config_with())
        self.assertEqual(kind, "switch")
        self.assertEqual(name, "前端")

    def test_use_is_alias(self):
        self.assertEqual(bridge.parse_command("/use 前端", config_with())[0], "switch")

    def test_topics_listing(self):
        self.assertEqual(bridge.parse_command("/topics", config_with())[0], "topics")

    def test_delete_topic(self):
        kind, name = bridge.parse_command("/del 前端", config_with())
        self.assertEqual(kind, "drop")
        self.assertEqual(name, "前端")

    def test_bare_topic_lists_instead_of_switching(self):
        # "/topic" with no argument means "show me the topics", which is the
        # least surprising reading of an accidental empty message.
        self.assertEqual(bridge.parse_command("/topic", config_with())[0], "topics")
        self.assertEqual(bridge.parse_command("/topic   ", config_with())[0], "topics")

    def test_topic_name_is_sanitised(self):
        self.assertEqual(bridge.normalize_topic("  #前端  "), "前端")
        self.assertEqual(bridge.normalize_topic("a   b"), "a b")
        self.assertEqual(bridge.normalize_topic(".."), "")
        self.assertEqual(len(bridge.normalize_topic("x" * 50)), 32)


class TopicRunIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sessions = bridge.SessionMap(str(Path(self.temp.name) / "s.json"))

    def tearDown(self):
        self.temp.cleanup()

    def run_in_topic(self, topic, prompt):
        task = bridge.Task(
            message_id="m", chat_id="c1", sender_id="ou", prompt=prompt,
            agent="dsh", topic=topic,
        )
        seen = {}

        def runner(session_id, prompt, cwd, timeout, **kwargs):
            seen["session"] = session_id
            return agents.RunResult(ok=True, text="ok", session_id=session_id or f"new-{topic}")

        bridge.run_task(task, config_with(), self.sessions, runner=runner)
        return seen["session"]

    def test_topics_do_not_share_sessions(self):
        self.assertEqual(self.run_in_topic("main", "a"), "")
        self.assertEqual(self.run_in_topic("前端", "b"), "")
        self.assertEqual(self.run_in_topic("main", "c"), "new-main")
        self.assertEqual(self.run_in_topic("前端", "d"), "new-前端")

    def test_guard_key_matches_between_submit_and_release(self):
        # A mismatch here leaks the run lock and makes the topic permanently
        # busy, which is exactly what happened before this test existed.
        from unittest.mock import MagicMock

        config = config_with()
        service = bridge_cli.BridgeService.__new__(bridge_cli.BridgeService)
        service.config = config
        service.logger = logging.getLogger("t")
        service.session_map = bridge.SessionMap(
            str(Path(tempfile.mkdtemp()) / "s.json")
        )
        service.guard = bridge.RunGuard()
        service._send = MagicMock()
        service._reply = MagicMock()

        task = bridge.Task(
            message_id="m", chat_id="c1", sender_id="ou", prompt="hi",
            agent="dsh", topic="前端",
        )
        self.assertTrue(service.guard.acquire(("c1", "前端", "dsh")))
        with patch.object(
            bridge, "run_task",
            return_value=bridge.TaskResult(task=task, ok=True, text="ok"),
        ):
            service._handle(task)
        self.assertEqual(service.guard.active(), {})


class InstanceLockTests(unittest.TestCase):
    """Feishu delivers each event to only one of an app's long connections, so a
    second bridge silently steals messages. Starting twice must fail loudly."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = {
            "sessions_path": str(Path(self.temp.name) / "bridge_sessions.json")
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_second_instance_is_refused(self):
        first = bridge_cli._acquire_instance_lock(self.settings)
        try:
            with self.assertRaises(SystemExit) as ctx:
                bridge_cli._acquire_instance_lock(self.settings)
            self.assertIn("已经有一个 bridge 在运行", str(ctx.exception))
        finally:
            first.close()

    def test_lock_is_released_on_close(self):
        first = bridge_cli._acquire_instance_lock(self.settings)
        first.close()
        second = bridge_cli._acquire_instance_lock(self.settings)
        second.close()

    def test_lock_records_pid(self):
        import os

        handle = bridge_cli._acquire_instance_lock(self.settings)
        try:
            lock_path = Path(self.settings["sessions_path"]).with_suffix(".lock")
            self.assertEqual(lock_path.read_text().strip(), str(os.getpid()))
        finally:
            handle.close()


class SessionIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_dsh_session(self, cwd_slug, session_id, title=""):
        d = self.home / "sessions" / cwd_slug / session_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.v4.jsonl.zstd").write_bytes(b"")
        return d

    def test_lists_dsh_sessions(self):
        from notifyhub import sessions_index as si

        self.make_dsh_session("--Users-x-proj--", "session-aaa")
        with patch.dict("os.environ", {"DSH_HOME": str(self.home)}):
            items = si.list_dsh_sessions()
        self.assertEqual([item.session_id for item in items], ["session-aaa"])
        self.assertEqual(items[0].agent, "dsh")

    def test_cwd_slug_is_decoded(self):
        from notifyhub import sessions_index as si

        self.assertEqual(si._dsh_cwd_from_slug("--Users-x-proj--"), "/Users/x/proj")
        self.assertEqual(si._dsh_cwd_from_slug("bad"), "")

    def test_codex_rollout_is_parsed(self):
        from notifyhub import sessions_index as si

        day = self.home / "sessions" / "2026" / "11" / "20"
        day.mkdir(parents=True)
        rollout = day / "rollout-2026-11-20T10-00-00-abc.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "payload": {"id": "sid-1", "cwd": "/w"}}),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "<environment_context>x"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "修一下这个 bug"}],
                    },
                }
            ),
        ]
        rollout.write_text("\n".join(lines), encoding="utf-8")

        with patch.dict("os.environ", {"CODEX_HOME": str(self.home)}):
            items = si.list_codex_sessions()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].session_id, "sid-1")
        self.assertEqual(items[0].cwd, "/w")
        # Injected environment context must not become the title.
        self.assertEqual(items[0].title, "修一下这个 bug")

    def test_missing_homes_return_empty(self):
        from notifyhub import sessions_index as si

        with patch.dict("os.environ", {"DSH_HOME": str(self.home), "CODEX_HOME": str(self.home)}):
            self.assertEqual(si.list_sessions(), [])

    def test_codex_skips_all_injected_system_blocks(self):
        from notifyhub import sessions_index as si

        day = self.home / "sessions" / "2026" / "11" / "20"
        day.mkdir(parents=True)
        rollout = day / "rollout-x.jsonl"

        def user(text):
            return json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    },
                }
            )

        lines = [
            json.dumps({"type": "session_meta", "payload": {"id": "sid-2", "cwd": "/w"}}),
            user("<recommended_plugins>\nHere is a list of plugins"),
            user("<environment_context>\n  <cwd>/w</cwd>"),
            user("帮我改这个函数"),
        ]
        rollout.write_text("\n".join(lines), encoding="utf-8")

        with patch.dict("os.environ", {"CODEX_HOME": str(self.home)}):
            items = si.list_codex_sessions()
        self.assertEqual(items[0].title, "帮我改这个函数")


class AttachCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sessions = bridge.SessionMap(str(Path(self.temp.name) / "s.json"))
        config = config_with()
        service = bridge_cli.BridgeService.__new__(bridge_cli.BridgeService)
        service.config = config
        service.logger = logging.getLogger("t")
        service.session_map = self.sessions
        service._attach_cache = {}
        self.replies = []
        service._reply = lambda message_id, text: self.replies.append(text)
        self.service = service

    def tearDown(self):
        self.temp.cleanup()

    def fake_list(self, *items):
        from notifyhub import sessions_index as si

        rows = [
            si.ExistingSession(
                agent=agent, session_id=session_id, title=title, updated_at=time.time()
            )
            for agent, session_id, title in items
        ]
        return patch.object(bridge_cli.sessions_index, "list_sessions", return_value=rows)

    def test_attach_by_index(self):
        with self.fake_list(("dsh", "session-1", "甲"), ("codex", "cx-2", "乙")):
            self.service._on_message(
                kind="attach_list", payload=None, message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
            self.service._on_message(
                kind="attach", payload="2", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertEqual(self.sessions.get("c1", "codex", "main"), "cx-2")
        self.assertIn("乙", self.replies[-1])

    def test_attach_by_raw_id_infers_agent(self):
        with self.fake_list():
            self.service._on_message(
                kind="attach", payload="session-raw-id", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertEqual(self.sessions.get("c1", "dsh", "main"), "session-raw-id")

    def test_attach_warns_when_the_desktop_app_holds_the_session(self):
        with self.fake_list(("dsh", "session-live-0001", "甲")), patch.object(
            bridge_cli.agents, "session_in_use", return_value=True
        ):
            self.service._on_message(
                kind="attach", payload="session-live-0001", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertIn("正在写它", self.replies[-1])
        self.assertIn("/handoff", self.replies[-1])

    def test_attach_is_quiet_when_the_session_is_free(self):
        with self.fake_list(("dsh", "session-free-0002", "乙")), patch.object(
            bridge_cli.agents, "session_in_use", return_value=False
        ):
            self.service._on_message(
                kind="attach", payload="session-free-0002", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertNotIn("正开着它", self.replies[-1])
        self.assertIn("接着那个对话说", self.replies[-1])

    def test_attach_list_marks_locked_sessions(self):
        with self.fake_list(
            ("dsh", "session-live-0001", "占用的"), ("dsh", "session-free-0002", "空闲的")
        ), patch.object(
            bridge_cli.agents,
            "session_in_use",
            side_effect=lambda agent, sid, **kw: sid == "session-live-0001",
        ):
            self.service._on_message(
                kind="attach_list", payload=None, message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        text = self.replies[-1]
        self.assertIn("占用的 🔒", text)
        self.assertNotIn("空闲的 🔒", text)
        self.assertIn("/handoff", text)

    def test_attach_binds_to_active_topic_only(self):
        self.sessions.set_active_topic("c1", "前端")
        with self.fake_list(("dsh", "session-x", "x")):
            self.service._on_message(
                kind="attach_list", payload=None, message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
            self.service._on_message(
                kind="attach", payload="1", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertEqual(self.sessions.get("c1", "dsh", "前端"), "session-x")
        self.assertEqual(self.sessions.get("c1", "dsh", "main"), "")

    def test_out_of_range_index_is_rejected(self):
        with self.fake_list(("dsh", "session-1", "甲")):
            self.service._on_message(
                kind="attach_list", payload=None, message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
            self.service._on_message(
                kind="attach", payload="9", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertIn("序号要在", self.replies[-1])
        self.assertEqual(self.sessions.get("c1", "dsh", "main"), "")

    def test_attach_without_listing_explains(self):
        self.service._on_message(
            kind="attach", payload="1", message_id="m", chat_id="c1",
            sender_id="ou", chat_type="p2p",
        )
        self.assertIn("/attach", self.replies[-1])

    def test_empty_list_message(self):
        with self.fake_list():
            self.service._on_message(
                kind="attach_list", payload=None, message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertIn("没有找到", self.replies[-1])


    def test_angle_bracket_index_is_accepted(self):
        # Feishu clients can render "<2>" or a markdown-quoted "`2`".
        with self.fake_list(("dsh", "session-1", "甲"), ("dsh", "session-2", "乙")):
            self.service._on_message(
                kind="attach_list", payload=None, message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
            for form in ("<2>", "`2`", "'2'"):
                self.sessions.set("c1", "dsh", "")
                self.service._on_message(
                    kind="attach", payload=form, message_id="m", chat_id="c1",
                    sender_id="ou", chat_type="p2p",
                )
                self.assertEqual(
                    self.sessions.get("c1", "dsh", "main"), "session-2", form
                )

    def test_malformed_argument_is_rejected_not_stored(self):
        # Previously "<2>" or any junk was stored as a codex session id.
        with self.fake_list(("dsh", "session-1", "甲")):
            self.service._on_message(
                kind="attach_list", payload=None, message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
            self.service._on_message(
                kind="attach", payload="随便什么", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertIn("没看懂", self.replies[-1])
        self.assertEqual(self.sessions.get("c1", "codex", "main"), "")
        self.assertEqual(self.sessions.get("c1", "dsh", "main"), "")

    def test_codex_uuid_is_recognised(self):
        with self.fake_list():
            self.service._on_message(
                kind="attach",
                payload="01a0624a-f543-7713-91b1-2bb6f9e315bd",
                message_id="m", chat_id="c1", sender_id="ou", chat_type="p2p",
            )
        self.assertEqual(
            self.sessions.get("c1", "codex", "main"),
            "01a0624a-f543-7713-91b1-2bb6f9e315bd",
        )


class DuplicateSuppressionTests(unittest.TestCase):
    """A send whose outcome is unknown must not be repeated."""

    def test_an_ambiguous_card_send_is_not_retried(self):
        service = _service()
        ambiguous = bridge_cli.feishu_app.AmbiguousSendError("timed out")
        with patch.object(bridge_cli.feishu_app, "send", side_effect=ambiguous) as send:
            with patch.object(bridge_cli.time, "sleep") as sleep:
                ok = service._send(_notification(), "oc_chat")
        self.assertFalse(ok)
        self.assertEqual(send.call_count, 1)
        sleep.assert_not_called()

    def test_a_definite_card_failure_is_still_retried(self):
        service = _service()
        with patch.object(
            bridge_cli.feishu_app, "send", side_effect=RuntimeError("HTTP 500")
        ) as send, patch.object(bridge_cli.time, "sleep"), patch.object(
            bridge_cli, "outbox_for"
        ) as outbox:
            outbox.return_value = MagicMock()
            ok = service._send(_notification(), "oc_chat")
        self.assertFalse(ok)
        self.assertEqual(send.call_count, 3)

    def test_an_ambiguous_reply_is_not_retried(self):
        service = _service()
        ambiguous = bridge_cli.feishu_app.AmbiguousSendError("eof")
        with patch.object(
            bridge_cli.feishu_app, "reply_text", side_effect=ambiguous
        ) as reply, patch.object(bridge_cli.time, "sleep") as sleep:
            ok = service._reply("om_1", "hi")
        self.assertFalse(ok)
        self.assertEqual(reply.call_count, 1)
        sleep.assert_not_called()


def _notification():
    from notifyhub.events import Notification

    return Notification(
        source="dsh", event="BridgeResult", status_text="✅", color="green",
        last_message="hi",
    )


def _service():
    logger = MagicMock()
    config = {"channels": {"feishu_app": {"app_id": "a", "app_secret": "b"}}, "bridge": {}}
    return bridge_cli.BridgeService(config, logger)


class SessionProbeTests(unittest.TestCase):
    """A session is busy only while another process holds its flock. dsh never
    deletes session.lock on release, so the file's presence must not block."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.session = self.home / "sessions" / "--w--" / "session-abc123"
        self.session.mkdir(parents=True)
        self.lock = self.session / "session.lock"
        self.lock.write_bytes(b"")
        self.data = self.session / "session.v4.jsonl.zstd"
        self.data.write_bytes(b"x")

    def tearDown(self):
        self.temp.cleanup()

    def hold_lock(self):
        handle = open(self.lock, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle

    def probe(self):
        return agents.session_in_use("dsh", "session-abc123", dsh_home=self.home)

    def test_a_bare_lock_file_is_not_busy(self):
        # The common case: the app released the lock but the file stays behind.
        self.assertFalse(self.probe())

    def test_a_recently_written_transcript_is_not_busy_on_its_own(self):
        # mtime says nothing about who owns the session; only the flock does.
        os.utime(self.data, (time.time(), time.time()))
        self.assertFalse(self.probe())

    def test_a_held_lock_is_busy(self):
        self.hold_lock()
        self.assertTrue(self.probe())

    def test_releasing_the_lock_clears_busy_without_deleting_the_file(self):
        handle = self.hold_lock()
        self.assertTrue(self.probe())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.assertTrue(self.lock.exists())
        self.assertFalse(self.probe())

    def test_unknown_agent_is_never_busy(self):
        self.hold_lock()
        self.assertFalse(
            agents.session_in_use("codex", "session-abc123", dsh_home=self.home)
        )

    def test_missing_session_dir_is_never_busy(self):
        self.assertFalse(
            agents.session_in_use("dsh", "session-nope", dsh_home=self.home)
        )

    def test_owned_error_is_recognised(self):
        self.assertTrue(
            agents.is_already_owned(
                'session "session-abc123" is already owned by an active write handle'
            )
        )
        self.assertFalse(agents.is_already_owned("超时（>30s）"))
        self.assertFalse(agents.is_already_owned(None))


class HandoffCommandTests(unittest.TestCase):
    """\u002fhandoff seeds the next message with a desktop conversation digest."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sessions = bridge.SessionMap(str(Path(self.temp.name) / "s.json"))
        service = bridge_cli.BridgeService.__new__(bridge_cli.BridgeService)
        service.config = config_with()
        service.logger = logging.getLogger("t")
        service.session_map = self.sessions
        service._attach_cache = {}
        service._handoff_pending = {}
        self.replies = []
        service._reply = lambda message_id, text: self.replies.append(text)
        self.service = service

    def tearDown(self):
        self.temp.cleanup()

    def fake_digest(self, empty=False, **kwargs):
        digest = transcript.Digest(
            session_id="session-desk",
            title="桌面的事",
            total_turns=9 if not empty else 0,
            exchanges=[] if empty else [transcript.Exchange(user="旧问题", assistant="旧回答")],
            truncated=not empty,
        )
        return patch.object(bridge_cli.transcript, "build_digest", return_value=digest)

    def test_handoff_stores_a_preamble_for_the_next_message(self):
        with self.fake_digest():
            self.service._on_message(
                kind="handoff", payload="", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertIn("已接上", self.replies[-1])
        preamble = self.service._handoff_pending.get("c1", "")
        self.assertIn("旧问题", preamble)

    def test_handoff_resets_the_topic_session(self):
        self.sessions.set("c1", "dsh", "session-old", topic="main")
        with self.fake_digest():
            self.service._on_message(
                kind="handoff", payload="", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        # A reused session would mix two unrelated histories together.
        self.assertEqual(self.sessions.get("c1", "dsh", "main"), "")

    def test_handoff_by_index_uses_the_attach_list(self):
        from notifyhub import sessions_index as si

        rows = [si.ExistingSession(agent="dsh", session_id="session-pick", title="挑中的")]
        self.service._attach_cache["c1"] = rows
        captured = {}

        def fake(session_id, **kwargs):
            captured["session_id"] = session_id
            return transcript.Digest(session_id=session_id, exchanges=[])

        with patch.object(bridge_cli.transcript, "build_digest", side_effect=fake):
            self.service._on_message(
                kind="handoff", payload="1", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertEqual(captured["session_id"], "session-pick")

    def test_handoff_without_records_says_so(self):
        with patch.object(
            bridge_cli.sessions_index,
            "list_sessions",
            return_value=[
                __import__("notifyhub.sessions_index", fromlist=["x"]).ExistingSession(
                    agent="dsh", session_id="session-empty"
                )
            ],
        ), self.fake_digest(empty=True):
            self.service._on_message(
                kind="handoff", payload="", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertIn("没有可用的对话记录", self.replies[-1])
        self.assertNotIn("c1", self.service._handoff_pending)

    def test_preamble_is_consumed_once(self):
        with self.fake_digest():
            self.service._on_message(
                kind="handoff", payload="", message_id="m", chat_id="c1",
                sender_id="ou", chat_type="p2p",
            )
        self.assertIn("c1", self.service._handoff_pending)
        # Popping it for one message is what stops it being re-sent forever.
        self.service._handoff_pending.pop("c1")
        self.assertNotIn("c1", self.service._handoff_pending)
