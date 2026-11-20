"""`notifyhub bridge` — run the Feishu long-connection bridge.

Flow:
    Feishu app -> (outbound websocket) -> FeishuListener
      -> allowlist check -> Worker queue -> agent runner -> reply card
"""

from __future__ import annotations

import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from . import agents, bridge
from .channels import feishu_app
from .config import load_config, resolve_path
from .events import Notification
from .feishu_listener import FeishuListener
from .log import get_logger
from .outbox import outbox_for
from .render import shorten_markdown


def _bridge_settings(config):
    return config.get("bridge") or {}


def _fmt_seconds(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def build_result_notification(task, result, config):
    """Render an agent run as a normal notifyhub card."""
    settings = _bridge_settings(config)
    if result.ok:
        status, color = f"✅ {result.task.agent} 完成", "green"
    else:
        status, color = f"❌ {result.task.agent} 失败", "red"

    limit = int(settings.get("max_message_chars", 3000))
    body = shorten_markdown(result.text or result.error, limit)
    topic = result.task.topic or bridge.DEFAULT_TOPIC
    topic_label = "main" if topic == bridge.DEFAULT_TOPIC else topic

    return Notification(
        source=result.task.agent,
        event="BridgeResult",
        status_text=status,
        color=color,
        title=f"飞书远程续聊 · {topic_label}",
        project="",
        session_id=result.session_id,
        model="",
        recent_prompt=result.task.prompt[:160],
        last_message=body,
        include_last_message=True,
        include_session_id=False,
        show_quota=False,
        extra_lines=[f"耗时 {_fmt_seconds(result.duration_seconds)}"]
        + ([f"会话 {result.session_id}"] if result.session_id else []),
    )


class BridgeService:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        settings = _bridge_settings(config)
        self.session_map = bridge.SessionMap(
            settings.get("sessions_path") or "~/.local/state/notifyhub/bridge_sessions.json",
            logger,
        )
        self.guard = bridge.RunGuard()
        self.worker = bridge.Worker(self._handle, logger, name="bridge-runner")
        self.listener = FeishuListener(
            config,
            self._on_message,
            logger,
            reply=self._reply,
        )
        self._stop = threading.Event()

    # ------------------------------------------------------------- outbound

    def _app_settings(self):
        return (self.config.get("channels") or {}).get("feishu_app") or {}

    def _reply(self, message_id, text):
        # A momentary TLS failure here used to drop the ack silently. Retry
        # once, but never block the receive loop for long.
        for attempt, delay in enumerate((0, 2), start=1):
            if delay:
                time.sleep(delay)
            try:
                feishu_app.reply_text(message_id, text, self._app_settings())
                return True
            except Exception as error:
                self.logger.warning(
                    "bridge: reply attempt %d/2 failed: %s", attempt, str(error)[:160]
                )
        return False

    def _send(self, notification, chat_id):
        """Send the result card back into the originating chat.

        The result must go to the chat that asked for it, not to the configured
        receive_id (which is usually a personal open_id of a different type).

        Feishu calls fail intermittently behind a flaky proxy, so retry briefly
        and fall back to the outbox rather than losing the result.
        """
        settings = dict(self._app_settings())
        settings["receive_id_type"] = "chat_id"
        settings["receive_id"] = chat_id

        delays = (0, 2, 5)
        last_error = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                time.sleep(delay)
            try:
                feishu_app.send(notification, settings, self.logger)
                if attempt > 1:
                    self.logger.info("bridge: card delivered on attempt %d", attempt)
                return True
            except Exception as error:
                last_error = error
                self.logger.warning(
                    "bridge: card send attempt %d/%d failed: %s",
                    attempt,
                    len(delays),
                    str(error)[:160],
                )

        # Every attempt failed: queue it so `notifyhub flush` retries later.
        try:
            outbox = outbox_for(self.config, self.logger)
            outbox.add(notification, [("feishu_app", False, str(last_error))])
            self.logger.error(
                "bridge: card queued to outbox after %d failures: %s",
                len(delays),
                str(last_error)[:160],
            )
        except Exception as error:
            self.logger.error("bridge: could not queue card: %s", error)
        return False

    # -------------------------------------------------------------- inbound

    def _on_message(self, kind, payload, message_id, chat_id, sender_id, chat_type):
        """Runs on the websocket loop: enqueue only, never block."""
        settings = _bridge_settings(self.config)
        replies = settings.get("reply") or {}

        if kind == "help":
            self._reply(message_id, bridge.help_text(self.config))
            return
        if kind == "status":
            self._reply(message_id, self._status_text(chat_id))
            return
        if kind == "topics":
            self._reply(message_id, self._topics_text(chat_id))
            return
        if kind == "switch":
            return self._switch_topic(message_id, chat_id, payload)
        if kind == "drop":
            return self._drop_topic(message_id, chat_id, payload)
        if kind == "new":
            topic = self.session_map.active_topic(chat_id)
            had = self.session_map.reset(chat_id, topic)
            label = self._topic_label(topic)
            self._reply(
                message_id,
                f"已清空话题 {label} 的上下文，下次发消息会开新会话。"
                if had
                else f"话题 {label} 本来就没有进行中的会话。",
            )
            return

        agent, prompt = payload
        if not agents.available_agents():
            self._reply(message_id, "本机没有可用的 agent（dsh / codex）。")
            return
        if agent not in agents.available_agents():
            self._reply(message_id, f"本机找不到 {agent}。")
            return

        topic = self.session_map.active_topic(chat_id)
        task = bridge.Task(
            message_id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            prompt=prompt,
            agent=agent,
            topic=topic,
        )

        key = (chat_id, topic, agent)
        if settings.get("reject_when_busy", True) and self.guard.busy(key):
            self._reply(message_id, f"{agent} 还在处理这个话题的上一条消息，请等它结束。")
            return
        if settings.get("reject_when_busy", True) and not self.guard.acquire(key):
            self._reply(message_id, f"{agent} 还在处理这个话题的上一条消息，请稍后。")
            return

        if replies.get("ack", True):
            position = self.worker.pending() + 1
            suffix = "" if position <= 1 else f"（前面还有 {position - 1} 条）"
            self._reply(
                message_id,
                f"收到（话题 {self._topic_label(topic)}），已交给 {agent} 处理{suffix}。",
            )

        self.worker.submit(task)

    # --------------------------------------------------------------- topics

    @staticmethod
    def _topic_label(topic):
        return "main" if topic == bridge.DEFAULT_TOPIC else topic

    def _switch_topic(self, message_id, chat_id, name):
        """Switch to a topic, creating it implicitly when it is new."""
        if name == bridge.DEFAULT_TOPIC:
            self.session_map.set_active_topic(chat_id, bridge.DEFAULT_TOPIC)
            self._reply(message_id, "已切回 main 话题。")
            return

        existed = name in self.session_map.topics(chat_id)
        self.session_map.set_active_topic(chat_id, name)
        if existed:
            self._reply(message_id, f"已切到话题 {name}。")
        else:
            self._reply(message_id, f"已新建并切到话题 {name}，直接发消息开始吧。")

    def _drop_topic(self, message_id, chat_id, name):
        if name == bridge.DEFAULT_TOPIC:
            self._reply(message_id, "main 话题不能删除；可以用 /new 清空它的上下文。")
            return
        if name not in self.session_map.topics(chat_id):
            self._reply(message_id, f"没有话题 {name}。")
            return
        self.session_map.drop_topic(chat_id, name)
        active = self.session_map.active_topic(chat_id)
        self._reply(
            message_id,
            f"已删除话题 {name}，当前话题：{self._topic_label(active)}。",
        )

    def _topics_text(self, chat_id):
        active = self.session_map.active_topic(chat_id)
        lines = ["话题列表："]
        for name in self.session_map.topics(chat_id):
            label = self._topic_label(name)
            marker = "→" if name == active else " "
            sessions = []
            for agent in ("dsh", "codex"):
                if self.session_map.get(chat_id, agent, name):
                    sessions.append(agent)
            detail = "（" + ", ".join(sessions) + "）" if sessions else "（空）"
            lines.append(f"{marker} {label} {detail}")
        lines.append("")
        lines.append("/topic <名字> 切换或新建")
        return "\n".join(lines)

    def _status_text(self, chat_id):
        topic = self.session_map.active_topic(chat_id)
        lines = [f"当前话题：{self._topic_label(topic)}", "", "会话："]
        found = False
        for agent in ("dsh", "codex"):
            session_id = self.session_map.get(chat_id, agent, topic)
            if session_id:
                found = True
                lines.append(f"- {agent}: {session_id}")
        if not found:
            lines.append("- 还没有会话，直接发消息就会新建")

        active = self.guard.active()
        if active:
            lines.append("")
            lines.append("运行中：")
            for (chat, run_topic, agent) in active:
                lines.append(f"- {agent}（话题 {self._topic_label(run_topic)}）")
        lines.append("")
        lines.append(f"排队中：{self.worker.pending()} 条")
        lines.append(f"话题数：{len(self.session_map.topics(chat_id))}（/topics 查看）")
        return "\n".join(lines)

    # -------------------------------------------------------------- worker

    def _handle(self, task):
        # Must match the key used in _on_message, or release() never clears it
        # and the topic stays permanently "busy".
        topic = task.topic or bridge.DEFAULT_TOPIC
        key = (task.chat_id, topic, task.agent)
        self.logger.info(
            "bridge: running agent=%s topic=%s chat=%s len=%d",
            task.agent,
            topic,
            task.chat_id,
            len(task.prompt),
        )
        try:
            result = bridge.run_task(task, self.config, self.session_map, self.logger)
            replies = _bridge_settings(self.config).get("reply") or {}
            if replies.get("on_finish", True):
                self._send(build_result_notification(task, result, self.config), task.chat_id)
            self.logger.info(
                "bridge: finished agent=%s ok=%s duration=%.1fs",
                task.agent,
                result.ok,
                result.duration_seconds,
            )
        except Exception as error:
            self.logger.exception("bridge: task crashed: %s", error)
            self._reply(task.message_id, f"处理失败：{error}")
        finally:
            self.guard.release(key)

    # ----------------------------------------------------------------- run

    def start(self):
        settings = _bridge_settings(self.config)
        allowed = settings.get("allow_open_ids") or []
        if not allowed:
            raise SystemExit(
                "bridge.allow_open_ids 为空，桥接会拒绝所有人。\n"
                "先填上你自己的 open_id（feishu_probe.py --owner 可以查到）再启动。"
            )

        # Feishu allows several long connections for one app and delivers each
        # event to only one of them, so a second instance silently steals
        # messages. Refuse to start instead of splitting traffic.
        self._lock_handle = _acquire_instance_lock(settings)

        self.worker.start()
        self.listener.start()
        self.logger.info(
            "bridge: listening; agents=%s allow=%d workspace=%s",
            ", ".join(agents.available_agents()) or "none",
            len(allowed),
            settings.get("workspace"),
        )

    def stop(self):
        self._stop.set()
        self.worker.stop()

    def wait(self):
        def handle_signal(signum, frame):
            raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGTERM, handle_signal)
        except (ValueError, OSError):
            pass
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self.logger.info("bridge: stopped")


def _acquire_instance_lock(settings):
    """Refuse to run two bridges at once.

    Feishu delivers an event to exactly one of an app's long connections, so a
    second instance silently swallows messages the first one should have
    handled. Fail loudly instead.
    """
    sessions_path = settings.get("sessions_path") or "~/.local/state/notifyhub/bridge_sessions.json"
    lock_path = resolve_path(str(sessions_path)).with_suffix(".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+")
    except OSError as error:
        raise SystemExit(f"无法创建实例锁 {lock_path}: {error}")

    try:
        import fcntl
    except ImportError:  # Windows: no flock, skip the guard
        return handle

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit(
            f"已经有一个 bridge 在运行了（锁文件 {lock_path}）。\n"
            "飞书会把每条消息只投递给其中一个连接，多开会互相抢消息。\n"
            "先停掉旧的：pkill -f 'notifyhub.py.*bridge start'"
        )
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass
    return handle


def cmd_bridge_start(args):
    config = load_config(args.config)
    if not _bridge_settings(config).get("enabled") and not args.force:
        print("bridge.enabled 是 false。确认知道风险后加 --force，或在配置里改成 true。")
        return 1

    logger = get_logger(config.get("log_path", ""), args.verbose)
    service = BridgeService(config, logger)
    service.start()
    service.wait()
    return 0


def cmd_bridge_status(args):
    config = load_config(args.config)
    settings = _bridge_settings(config)
    sessions = bridge.SessionMap(
        settings.get("sessions_path") or "~/.local/state/notifyhub/bridge_sessions.json"
    )

    print(f"bridge.enabled : {settings.get('enabled')}")
    print(f"allow_open_ids : {len(settings.get('allow_open_ids') or [])} 个")
    print(f"default_agent  : {settings.get('default_agent')}")
    print(f"workspace      : {resolve_path(str(settings.get('workspace') or '~'))}")
    print(f"可用 agent     : {', '.join(agents.available_agents()) or '（无）'}")
    print(f"会话文件       : {sessions.path}")

    data = sessions.all_sessions()
    if data:
        print("已记录的会话：")
        for chat_id, per_agent in data.items():
            for agent, session_id in (per_agent or {}).items():
                print(f"  {agent}: {session_id}  (chat {chat_id})")
    else:
        print("还没有任何远程会话。")
    return 0


def cmd_bridge_check(args):
    """Preflight: what the bridge needs before it can work."""
    config = load_config(args.config)
    settings = _bridge_settings(config)
    app = (config.get("channels") or {}).get("feishu_app") or {}
    ok = True

    def report(label, passed, detail):
        nonlocal ok
        print(f"{'✓' if passed else '✗'} {label}: {detail}")
        if not passed:
            ok = False

    report("app_id / app_secret", bool(app.get("app_id") and app.get("app_secret")), "已配置" if app.get("app_id") else "缺少")
    try:
        from .feishu_listener import import_sdk

        import_sdk()
        report("lark-oapi", True, "已安装")
    except Exception as error:
        report("lark-oapi", False, str(error).splitlines()[0])

    allowed = settings.get("allow_open_ids") or []
    report(
        "allow_open_ids",
        bool(allowed),
        f"{len(allowed)} 个" if allowed else "为空，会拒绝所有人",
    )
    found = agents.available_agents()
    report("agent 可执行文件", bool(found), ", ".join(found) or "没有找到 dsh / codex")
    report("bridge.enabled", bool(settings.get("enabled")), "已开启" if settings.get("enabled") else "未开启")

    workspace = resolve_path(str(settings.get("workspace") or "~"))
    report("workspace", workspace.is_dir(), str(workspace))

    # The headless profile authenticates through apiKeyEnv, which reads the
    # process environment, not the account login the desktop app uses. A key
    # sitting in ~/.dsh/.credentials.yaml is not automatically exported.
    env_key = _headless_api_key_env()
    if env_key is None:
        report("模型凭据", True, "headless profile 未声明 apiKeyEnv（用账号登录）")
    else:
        imported = bool(os.environ.get(env_key))
        stored = _stored_credential(env_key)
        if imported:
            detail = f"{env_key} 已注入环境"
        elif stored:
            detail = f"{env_key} 在 ~/.dsh/.credentials.yaml 里，但未注入环境（用 start_bridge.sh 会自动注入）"
        else:
            detail = f"缺少 {env_key}，headless 无法调用模型"
        report("模型凭据", imported, detail)

    return 0 if ok else 1


def _headless_api_key_env():
    """Read apiKeyEnv from the headless profile's patch, if it declares one."""
    home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    patch = home / "profiles" / "headless" / "cordis.patch.yml"
    try:
        text = patch.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^\s*apiKeyEnv:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", text, re.M)
    return match.group(1) if match else None


def _stored_credential(name):
    """True when ~/.dsh/.credentials.yaml has a value for this name."""
    home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    path = home / ".credentials.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    match = re.search(rf"^\s*{re.escape(name)}:\s*\S", text, re.M)
    return bool(match)


def cmd_bridge_owner(args):
    """Print the app owner's open_id, the value to paste into allow_open_ids."""
    config = load_config(args.config)
    app = (config.get("channels") or {}).get("feishu_app") or {}
    if not app.get("app_id") or not app.get("app_secret"):
        print("先配置 channels.feishu_app 的 app_id / app_secret")
        return 1
    receive_id = app.get("receive_id") or ""
    if str(app.get("receive_id_type")) == "open_id" and receive_id:
        print(f"你的 open_id: {receive_id}")
        print("把它写进 bridge.allow_open_ids。")
        return 0
    print("当前 receive_id_type 不是 open_id，无法直接得出 open_id。")
    print("在飞书里给机器人发一条消息，bridge 日志会打印你的 open_id。")
    return 1