import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notifyhub import agents, bridge
from notifyhub.config import DEFAULT_CONFIG, deep_merge
from notifyhub.feishu_listener import bot_mentions_text, extract_text
from notifyhub import bridge_cli


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
        self.assertIn("占用", result.error)


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
    def make_event(self, sender_open_id="ou_allowed", text="hi", message_type="text"):
        class SenderId:
            open_id = sender_open_id

        class Sender:
            sender_id = SenderId()

        class Message:
            message_id = "om_1"
            chat_id = "chat-1"
            chat_type = "p2p"
            content = json.dumps({"text": text})

        # Assign after the class body: a class-scope name cannot reference the
        # enclosing function parameter of the same name.
        Message.message_type = message_type

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
