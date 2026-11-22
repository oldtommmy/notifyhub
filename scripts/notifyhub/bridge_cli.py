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

from . import agents, bridge, service, sessions_index, transcript
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
        self._attach_cache = {}
        # chat_id -> rendered handoff preamble waiting for the next prompt.
        self._handoff_pending = {}
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
            except feishu_app.AmbiguousSendError as error:
                # Same reasoning as _send: an unanswered reply may already be
                # in the chat, and a second copy is worse than none.
                self.logger.warning(
                    "bridge: reply outcome unknown (%s); not retrying",
                    str(error)[:160],
                )
                return False
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
            except feishu_app.AmbiguousSendError as error:
                # No answer came back: Feishu may well have delivered it. A
                # retry here is what shows up as a duplicate card in the chat.
                last_error = error
                self.logger.warning(
                    "bridge: card send outcome unknown (%s); not retrying to "
                    "avoid a duplicate",
                    str(error)[:160],
                )
                return False
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
        if kind == "attach_list":
            self._reply(message_id, self._attach_list_text(chat_id))
            return
        if kind == "attach":
            return self._attach_session(message_id, chat_id, payload)
        if kind == "handoff":
            return self._handoff(message_id, chat_id, payload)
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
        preamble = self._handoff_pending.pop(chat_id, "") if agent == "dsh" else ""
        task = bridge.Task(
            message_id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            prompt=prompt,
            agent=agent,
            topic=topic,
            context=preamble,
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

    # --------------------------------------------------------- attach (电脑上的会话)

    def _attach_list_text(self, chat_id):
        """List sessions already on this machine so one can be attached."""
        items = sessions_index.list_sessions(limit=10)
        # Remember the exact ordering so "/attach 3" means the third row here.
        self._attach_cache[chat_id] = items

        if not items:
            return "本机没有找到 dsh / codex 的已有会话。"

        lines = ["本机已有会话（最近 10 个）："]
        for index, item in enumerate(items, start=1):
            title = (item.title or "(无标题)")[:28]
            age = item.age_hours()
            when = f"{age*60:.0f}分钟前" if age < 1 else f"{age:.1f}小时前"
            cwd = Path(item.cwd).name if item.cwd else ""
            busy = item.agent == "dsh" and agents.session_in_use("dsh", item.session_id)
            mark = " 🔒" if busy else ""
            lines.append(f"{index}. [{item.agent}] {title}{mark}")
            lines.append(f"    {when} · {cwd}")
        if any(line.endswith("🔒") for line in lines):
            lines.append("")
            lines.append("🔒 = 电脑上的 DSH 此刻正在写它（真正持有写锁）；")
            lines.append("     稍后再发，或用 /handoff 接上下文（不冲突）。")
        lines.append("")
        lines.append("/attach <序号> 接到当前话题")
        return "\n".join(lines)

    def _attach_session(self, message_id, chat_id, argument):
        """Bind an existing session id to the active topic."""
        topic = self.session_map.active_topic(chat_id)
        items = self._attach_cache.get(chat_id) or []

        choosen = None
        raw = str(argument or "").strip()
        # Feishu clients can wrap the number in angle brackets (<2>) or add a
        # markdown backtick; treat those as the number they contain.
        text = raw.strip("<>`'\"").strip()
        if text.isdigit():
            index = int(text)
            if not items:
                self._reply(
                    message_id,
                    "先发 /attach 看看有哪些会话（列表只对同一次运行有效）。",
                )
                return
            if not 1 <= index <= len(items):
                self._reply(message_id, f"序号要在 1 到 {len(items)} 之间。")
                return
            chosen = items[index - 1]
        elif text:
            # A raw session id: dsh uses session-*, codex uses a bare uuid.
            # Be strict enough to reject chatter, loose enough to accept any
            # well-formed id the two agents might produce.
            looks_like_dsh = re.fullmatch(r"session-[A-Za-z0-9-]{6,}", text)
            looks_like_codex = re.fullmatch(r"[0-9a-fA-F-]{20,}", text)
            if not (looks_like_dsh or looks_like_codex):
                self._reply(
                    message_id,
                    f"没看懂「{raw[:20]}」。用法：\n"
                    "/attach → 列出会话\n"
                    "/attach 2 → 接第 2 个\n"
                    "/attach <完整的 session id>",
                )
                return
            agent = "dsh" if looks_like_dsh else "codex"
            chosen = sessions_index.ExistingSession(agent=agent, session_id=text)

        if not chosen or not chosen.session_id:
            self._reply(message_id, "没有识别到会话 id。")
            return

        # A session the desktop app is *writing* right now cannot be resumed in
        # parallel, so say so up front — but a session merely loaded in the app
        # holds no lock and is resumed normally.
        blocked = chosen.agent == "dsh" and agents.session_in_use(
            "dsh", chosen.session_id
        )

        self.session_map.set(chat_id, chosen.agent, chosen.session_id, topic=topic)
        self.logger.info(
            "bridge: attach topic=%s chat=%s agent=%s session=%s",
            self._topic_label(topic),
            chat_id,
            chosen.agent,
            chosen.session_id,
        )
        title = (chosen.title or "").strip()
        detail = f"「{title[:24]}」" if title else ""
        if blocked:
            self._reply(
                message_id,
                f"已记下 {chosen.agent} 会话 {chosen.session_id} {detail}，"
                f"但电脑上的 DSH 此刻正在写它，写不进去。\n"
                "两个选择：\n"
                "1. 等它写完再发消息（通常几秒），就是真正的续写。\n"
                "2. 发 /handoff 把它的历史接到飞书这边，马上就能聊"
                "（手机说的话不会写回电脑）。",
            )
        else:
            self._reply(
                message_id,
                f"已把 {chosen.agent} 会话 {chosen.session_id} {detail} "
                f"接到话题 {self._topic_label(topic)}。\n"
                "现在直接发消息就会接着那个对话说。",
            )

    # ------------------------------------------------- handoff (接桌面会话的上下文)

    def _handoff(self, message_id, chat_id, argument):
        """Seed the next message with a digest of a desktop conversation.

        dsh allows one write handle per session id and the desktop app holds it
        while the session is open, so the bridge cannot resume that session.
        Reading the event log needs no handle, so instead the phone gets its own
        session that starts out knowing what the Mac conversation said.
        """
        settings = _bridge_settings(self.config)
        turns = int(settings.get("handoff_turns", 6))
        max_chars = int(settings.get("handoff_max_chars", 12000))

        session_id = str(argument or "").strip()
        items = self._attach_cache.get(chat_id) or []
        if session_id.isdigit():
            index = int(session_id)
            if not items:
                self._reply(
                    message_id,
                    "先发 /handoff（不带参数）看看有哪些会话可以接。",
                )
                return
            if not 1 <= index <= len(items):
                self._reply(message_id, f"序号要在 1 到 {len(items)} 之间。")
                return
            session_id = items[index - 1].session_id

        if not session_id:
            # Default to the most recently written dsh session: that is almost
            # always the one the user is talking to on the Mac right now.
            candidates = [
                item
                for item in sessions_index.list_sessions(agent="dsh", limit=10)
                if item.session_id
            ]
            if candidates:
                session_id = candidates[0].session_id

        if not session_id:
            self._reply(message_id, "没有找到可以接续的 dsh 会话。")
            return

        digest = transcript.build_digest(
            session_id, turns=turns, max_chars=max_chars
        )
        if digest.empty:
            self._reply(
                message_id,
                f"会话 {session_id[:24]} 里没有可用的对话记录，接不过来。",
            )
            return

        preamble = transcript.render_digest(digest)
        self._handoff_pending[chat_id] = preamble

        # The phone session must be new: continuing an old one would mix two
        # histories together and confuse the model.
        topic = self.session_map.active_topic(chat_id)
        self.session_map.reset(chat_id, topic)

        title = (digest.title or "(无标题)")[:24]
        self.logger.info(
            "bridge: handoff chat=%s session=%s turns=%d chars=%d",
            chat_id,
            session_id,
            len(digest.exchanges),
            len(preamble),
        )
        self._reply(
            message_id,
            f"已接上「{title}」的上下文（共 {digest.total_turns} 轮，"
            f"带上最近 {len(digest.exchanges)} 轮）。\n"
            "话题已重置为新会话，下一条消息就会带着这段历史发给 dsh。\n"
            "注意：手机这边说的话不会写回电脑那个会话。",
        )

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
            if not result.ok:
                # Without this a failed run logs only "ok=False" and the cause
                # (busy session, bad credentials, timeout) is invisible.
                self.logger.warning(
                    "bridge: %s failed: %s", task.agent, (result.error or "(无错误信息)")[:300]
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

def cmd_bridge_logs(args):
    """Tail the bridge log, so status checks don't need a terminal scrollback."""
    config = load_config(args.config)
    path = Path(str(config.get("log_path") or "~/.local/state/notifyhub/notifyhub.log")).expanduser()
    if not path.is_file():
        print(f"还没有日志文件：{path}")
        return 1
    if args.follow:
        try:
            os.execvp("tail", ["tail", "-n", str(args.lines), "-f", str(path)])
        except OSError as error:
            print(f"无法跟踪 {path}: {error}")
            return 1
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-max(1, args.lines):]:
        print(line)
    return 0


def cmd_bridge_service(args):
    """Install / remove / inspect the launchd-managed bridge."""
    config = load_config(args.config)
    settings = _bridge_settings(config)
    action = args.service_command

    if action == "status":
        info = service.status()
        loaded = bool(info.get("loaded"))
        pid = info.get("pid")
        alive = bool(pid)
        print(f"launchd 注册 : {'是' if loaded else '否'}"
              + (f"  PID {pid}" if pid else ""))
        if loaded:
            print(f"运行状态     : {'运行中' if alive else '已退出（launchd 会在下次登录重启）'}")
        if info.get("reason"):
            print(f"状态说明     : {info['reason']}")
        if info.get("last_exit") not in (None, "0"):
            print(f"上次退出码   : {info['last_exit']} （非 0 说明崩过，launchd 会重拉）")
        plist = service.plist_path()
        print(f"plist        : {plist}{'' if plist.is_file() else ' （不存在）'}")
        log = Path(str(config.get("log_path") or "")).expanduser()
        if log.is_file():
            print(f"日志         : {log}")
        return 0

    if action == "uninstall":
        result = service.uninstall(args.label)
        print(f"已卸载 {result['path']}")
        return 0

    if action == "restart":
        service.restart(args.label)
        print("已重启。")
        return 0

    # install
    if not settings.get("enabled"):
        print("bridge.enabled 是 false，常驻服务会起不来。先把它改成 true。")
        return 1
    root = Path(args.root).expanduser().resolve() if args.root else _default_root()
    python = args.python or service._python_for(venv=args.venv, root=root)
    python = _absolute_interpreter(python, args.venv)
    script = Path(args.script or (root / "notifyhub" / "scripts" / "notifyhub.py"))
    if not script.is_file():
        print(f"找不到入口脚本：{script}（用 --script 指定）")
        return 1

    env = {}
    for name in ("QWEN_API_KEY", "NOTIFYHUB_FEISHU_APP_SECRET"):
        value = _resolve_credential(args.env, name)
        if value:
            env[name] = value
    plist = service.build_plist(
        python=python,
        script=script,
        config=Path(args.config).expanduser() if args.config else _active_config_path(),
        workspace=resolve_path(str(settings.get("workspace") or "~")),
        log_path=Path(str(config.get("log_path") or "~/bridge.out")).expanduser().with_suffix(".out"),
        env=env,
        label=args.label,
    )
    target = service.install(plist, args.plist)
    print(f"已安装 launchd 服务 {args.label}")
    print(f"  plist : {target}")
    print(f"  python: {python}")
    print(f"  脚本  : {script}")
    if env:
        print(f"  注入  : {', '.join(sorted(env))}（值已写入 plist，mode 600）")
    else:
        print("  注入  : 无（服务会从本机凭据文件自己读取）")
    print()
    print("常用操作：")
    print("  notifyhub bridge service status     # 看是否在跑")
    print("  notifyhub bridge service restart    # 重启")
    print("  notifyhub bridge service uninstall  # 卸载并停止")
    print("  notifyhub bridge logs -n 50         # 看日志")
    return 0


def _default_root():
    return Path(__file__).resolve().parents[2]


def _absolute_interpreter(python, venv=None):
    """Make the interpreter path absolute WITHOUT following the venv symlink.

    launchd has no meaningful cwd, so a relative path fails. But resolving the
    symlink leaves the venv and loses its site-packages, so anchor the path at
    the venv directory instead.
    """
    path = Path(python).expanduser()
    if path.is_absolute():
        return str(path)
    roots = [Path(venv).expanduser()] if venv else []
    roots += [Path.cwd(), Path(__file__).resolve().parents[2]]
    for root in roots:
        candidate = (root / path)
        if candidate.is_file():
            return str(candidate)
    return str(Path.cwd() / path)


def _active_config_path():
    """Best guess at the config file the caller is using."""
    explicit = os.environ.get("NOTIFYHUB_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return Path("~/.config/notifyhub/config.json").expanduser()


def _resolve_credential(args, name):
    """Pick a credential up from argv, then the environment."""
    flag = "--" + name.lower().replace("_", "-")
    for index, arg in enumerate(args or []):
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return os.environ.get(name, "")
