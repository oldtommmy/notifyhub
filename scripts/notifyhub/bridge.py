"""Feishu -> local agent bridge.

The security-critical parts live here so they can be tested without Feishu or a
running agent: who is allowed to talk to this bridge, which agent and session a
message maps to, and how concurrent runs are serialized.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import agents
from .config import resolve_path


class BridgeDenied(RuntimeError):
    """Raised when a message is not allowed to reach an agent."""


# Shown when dsh itself refuses the write because another process owns the
# session. The probe in agents.session_in_use catches most of these up front;
# this is the race window where the desktop app grabbed the write lock while
# the bridge was starting its turn.
LOCKED_HINT = (
    "桌面 App 正在写这个会话，这次没能续上。"
    "稍等一下再发一次，或发 /handoff 把历史接到新会话（不冲突）。"
)


@dataclass
class Task:
    message_id: str
    chat_id: str
    sender_id: str
    prompt: str
    agent: str
    topic: str = ""
    created_at: float = field(default_factory=time.time)
    # Preamble carrying the handoff digest; sent once, then cleared.
    context: str = ""


@dataclass
class TaskResult:
    task: Task
    ok: bool
    text: str = ""
    session_id: str = ""
    error: str = ""
    duration_seconds: float = 0.0


# ------------------------------------------------------------------ 命令解析

HELP_TEXT = """notifyhub 远程续聊

直接发消息 → 用当前话题继续对话
@dsh <内容> → 强制用 dsh
@codex <内容> → 强制用 codex

话题（同一个私聊里开多个独立对话）
/topic <名字> → 切到该话题，不存在就新建
/topics → 列出所有话题
/del <名字> → 删除话题
/new → 清空当前话题的上下文

/status → 查看当前话题和运行状态
/help → 显示这条帮助

续聊电脑上已开的对话
/attach → 列出本机已有会话
/attach <序号> → 把它接到当前话题（序号从 1 开始）
/attach <session-id> → 也可以直接给 id
/handoff → 把桌面正在聊的会话历史接过来（手机接着聊）
/handoff <序号或 id> → 指定要接哪个会话
"""


def normalize_topic(name):
    """Sanitise a topic name: no whitespace, no path separators."""
    value = " ".join(str(name or "").split())
    value = value.strip().lstrip("#").strip()
    if not value:
        return ""
    if len(value) > 32:
        value = value[:32]
    if value in ("/", ".", ".."):
        return ""
    return value


def parse_command(text, config):
    """Split a Feishu message into (agent, prompt) or a local command.

    Returns (kind, payload) where kind is one of "agent", "new", "status",
    "help".
    """
    raw = str(text or "").strip()
    if not raw:
        raise BridgeDenied("空消息")

    lowered = raw.lower()
    if lowered in ("帮助", "命令", "菜单", "help", "command", "/help", "/command", "？", "?"):
        return "help", None
    if lowered in ("/new", "new", "/reset"):
        return "new", None
    if lowered in ("/status", "status"):
        return "status", None
    if lowered in ("/topics", "/topic", "/ls"):
        return "topics", None
    if lowered.startswith("/topic "):
        name = normalize_topic(raw[len("/topic ") :])
        if not name:
            raise BridgeDenied("话题名不能为空")
        return "switch", name
    if lowered.startswith("/use "):
        name = normalize_topic(raw[len("/use ") :])
        if not name:
            raise BridgeDenied("话题名不能为空")
        return "switch", name
    if lowered.startswith("/del "):
        name = normalize_topic(raw[len("/del ") :])
        if not name:
            raise BridgeDenied("话题名不能为空")
        return "drop", name
    if lowered in ("/attach", "/sessions", "/list"):
        return "attach_list", None
    if lowered.startswith("/attach "):
        return "attach", raw[len("/attach ") :].strip()
    if lowered in ("/handoff", "/takeover", "/接续"):
        return "handoff", ""
    if lowered.startswith("/handoff "):
        return "handoff", raw[len("/handoff ") :].strip()
    if lowered.startswith("/takeover "):
        return "handoff", raw[len("/takeover ") :].strip()

    bridge = config.get("bridge") or {}
    prefixes = bridge.get("prefixes") or {}
    for agent, prefix in prefixes.items():
        if prefix and raw.lower().startswith(prefix.lower()):
            prompt = raw[len(prefix) :].strip()
            if not prompt:
                raise BridgeDenied(f"{prefix} 后面没有内容")
            if agent not in ("dsh", "codex"):
                raise BridgeDenied(f"不支持的 agent: {agent}")
            return "agent", (agent, prompt)

    # Common shorthand even when no prefix is configured. Match on the first token
    # so a trailing-space-only message ("@codex   ") is still recognised and
    # rejected for having no prompt.
    for agent in ("dsh", "codex"):
        tag = f"@{agent}"
        lowered_raw = raw.lower()
        if lowered_raw == tag or lowered_raw.startswith(tag + " "):
            prompt = raw[len(tag) :].strip()
            if not prompt:
                raise BridgeDenied(f"{tag} 后面没有内容")
            return "agent", (agent, prompt)

    default = str(bridge.get("default_agent") or "dsh")
    if default not in ("dsh", "codex"):
        raise BridgeDenied(f"default_agent 配置无效: {default}")
    return "agent", (default, raw)


def help_text(config):
    default = str((config.get("bridge") or {}).get("default_agent") or "dsh")
    return HELP_TEXT + f"\n\n默认 agent: {default}"


# ------------------------------------------------------------------- 白名单


def is_allowed(sender_id, config):
    """Deny-by-default allowlist.

    An empty allow_open_ids denies everyone. Failing closed matters here: the
    bridge can run shell commands, so a half-configured install must not become
    an open remote shell.
    """
    allowed = (config.get("bridge") or {}).get("allow_open_ids") or []
    allowed = [str(item).strip() for item in allowed if str(item).strip()]
    if not allowed:
        return False
    return str(sender_id or "").strip() in allowed


# -------------------------------------------------------------- 会话映射表


DEFAULT_TOPIC = "main"


class SessionMap:
    """(chat_id, topic) -> {agent -> dsh/codex session id}.

    One Feishu chat can host several independent conversations ("topics"), so
    unrelated work does not share context. Each topic keeps its own dsh and
    codex session, and the chat remembers which topic is currently active.
    """

    def __init__(self, path, logger=None):
        self.path = resolve_path(str(path))
        self.logger = logger
        self._data = {}
        self._active = {}
        self._generation = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return

        # Current format: {"topics": {chat: {topic: {agent: session}}}, "active": {...}}
        if "topics" in data:
            topics = data.get("topics")
            if isinstance(topics, dict):
                for chat_id, per_topic in topics.items():
                    if not isinstance(per_topic, dict):
                        continue
                    self._data[chat_id] = {
                        str(topic): dict(sessions)
                        for topic, sessions in per_topic.items()
                        if isinstance(sessions, dict)
                    }
            active = data.get("active")
            if isinstance(active, dict):
                self._active = {chat: str(topic) for chat, topic in active.items()}
            return

        # Legacy {chat: {agent: session}} files are read as topic "main".
        for chat_id, value in data.items():
            if not isinstance(value, dict):
                continue
            if _looks_like_topic_map(value):
                self._data[chat_id] = {
                    topic: dict(sessions)
                    for topic, sessions in value.items()
                    if isinstance(sessions, dict)
                }
            else:
                self._data[chat_id] = {DEFAULT_TOPIC: dict(value)}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(
                json.dumps(
                    {
                        "topics": self._data,
                        "active": self._active,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as error:
            if self.logger:
                self.logger.warning("cannot write session map %s: %s", self.path, error)

    # ---------------------------------------------------------------- topics

    def active_topic(self, chat_id):
        with self._lock:
            return self._active.get(chat_id) or DEFAULT_TOPIC

    def set_active_topic(self, chat_id, topic):
        with self._lock:
            if topic == DEFAULT_TOPIC:
                self._active.pop(chat_id, None)
            else:
                self._active[chat_id] = topic
            self._save()

    def topics(self, chat_id):
        """Topic names for this chat, default first, then alphabetical."""
        with self._lock:
            names = set((self._data.get(chat_id) or {}).keys())
        names.add(DEFAULT_TOPIC)
        ordered = [DEFAULT_TOPIC] + sorted(name for name in names if name != DEFAULT_TOPIC)
        return ordered

    # -------------------------------------------------------------- sessions

    def get(self, chat_id, agent, topic=None):
        topic = topic or self.active_topic(chat_id)
        with self._lock:
            return ((self._data.get(chat_id) or {}).get(topic) or {}).get(agent, "")

    def set(self, chat_id, agent, session_id, generation=None, topic=None):
        """Record a session id for a topic.

        When `generation` is given, the write is skipped if `/new` ran in the
        meantime, so a run that finishes after a reset cannot resurrect the old
        session.
        """
        if not session_id:
            return
        topic = topic or self.active_topic(chat_id)
        with self._lock:
            key = (chat_id, topic)
            if generation is not None and self._generation.get(key, 0) != generation:
                if self.logger:
                    self.logger.info(
                        "bridge: dropping stale session %s for %s/%s (reset during run)",
                        session_id,
                        chat_id,
                        topic,
                    )
                return
            self._data.setdefault(chat_id, {}).setdefault(topic, {})[agent] = session_id
            self._save()

    def generation(self, chat_id, topic=None):
        topic = topic or self.active_topic(chat_id)
        with self._lock:
            return self._generation.get((chat_id, topic), 0)

    def reset(self, chat_id, topic=None):
        """Forget the sessions of one topic (the active one by default)."""
        topic = topic or self.active_topic(chat_id)
        with self._lock:
            key = (chat_id, topic)
            self._generation[key] = self._generation.get(key, 0) + 1
            per_topic = self._data.get(chat_id) or {}
            had = per_topic.pop(topic, None)
            if had is not None:
                self._save()
            return bool(had)

    def drop_topic(self, chat_id, topic):
        """Remove a topic entirely and fall back to the default one."""
        with self._lock:
            key = (chat_id, topic)
            self._generation[key] = self._generation.get(key, 0) + 1
            per_topic = self._data.get(chat_id) or {}
            had = per_topic.pop(topic, None)
            if not per_topic:
                self._data.pop(chat_id, None)
            if self._active.get(chat_id) == topic:
                self._active.pop(chat_id, None)
            if had is not None:
                self._save()
            return bool(had)

    def all_sessions(self):
        with self._lock:
            return {
                chat: {topic: dict(sessions) for topic, sessions in per.items()}
                for chat, per in self._data.items()
            }

    def active_map(self):
        with self._lock:
            return dict(self._active)


def _looks_like_topic_map(value):
    """True for {topic: {agent: session}} as opposed to {agent: session}."""
    known_agents = {"dsh", "codex"}
    keys = [key for key in value.keys() if isinstance(key, str)]
    if not keys:
        return False
    if any(key in known_agents for key in keys):
        return False
    return all(isinstance(item, dict) for item in value.values())


# ------------------------------------------------------------ 会话锁与串行


class RunGuard:
    """One agent run per session at a time."""

    def __init__(self):
        self._active = {}
        self._lock = threading.Lock()

    def busy(self, key):
        with self._lock:
            return key in self._active

    def acquire(self, key, label=""):
        with self._lock:
            if key in self._active:
                return False
            self._active[key] = label
            return True

    def release(self, key):
        with self._lock:
            self._active.pop(key, None)

    def active(self):
        with self._lock:
            return dict(self._active)


# --------------------------------------------------------------- 任务队列


class Worker:
    """Serial worker: Feishu callbacks must never block on an agent run."""

    def __init__(self, handler, logger=None, name="bridge-worker"):
        self.handler = handler
        self.logger = logger
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._started = False
        self._stopped = False

    def start(self):
        if not self._started:
            self._thread.start()
            self._started = True

    def submit(self, item):
        self._queue.put(item)

    def stop(self):
        self._stopped = True
        self._queue.put(None)

    def pending(self):
        return self._queue.qsize()

    def _loop(self):
        while not self._stopped:
            item = self._queue.get()
            if item is None:
                break
            try:
                self.handler(item)
            except Exception as error:  # a bad task must not kill the worker
                if self.logger:
                    self.logger.exception("bridge task failed: %s", error)


# ------------------------------------------------------------------ 执行器


def run_task(task, config, session_map, logger=None, runner=None):
    """Run one agent turn for a task and return a TaskResult."""
    bridge = config.get("bridge") or {}
    agent = task.agent
    workspace = str(resolve_path(str(bridge.get("workspace") or "~")))
    timeout = float(bridge.get("timeout_seconds", 1800))
    topic = task.topic or DEFAULT_TOPIC
    session_id = session_map.get(task.chat_id, agent, topic) if session_map else ""
    # Snapshot the generation so a /new issued while this run is in flight wins.
    generation = session_map.generation(task.chat_id, topic) if session_map else None

    if agent == "dsh" and agents.session_in_use(agent, session_id):
        return TaskResult(
            task=task,
            ok=False,
            session_id=session_id,
            error=LOCKED_HINT,
        )

    execute = runner or agents.get_runner(agent)
    kwargs = {"timeout": timeout}
    if task.context:
        kwargs["context"] = task.context
    if agent == "dsh":
        kwargs["permission_mode"] = bridge.get("permission_mode") or None
        kwargs["approval_policy"] = bridge.get("approval_policy") or None
    elif agent == "codex":
        kwargs["sandbox"] = "workspace-write"
        kwargs["approval"] = "never"

    started = time.time()
    try:
        result = execute(session_id, task.prompt, workspace, **kwargs)
    except agents.RunnerError as error:
        return TaskResult(
            task=task,
            ok=False,
            session_id=session_id,
            error=str(error),
            duration_seconds=time.time() - started,
        )
    except Exception as error:  # never let an unexpected error kill the worker
        if logger:
            logger.exception("agent run crashed: %s", error)
        return TaskResult(
            task=task,
            ok=False,
            session_id=session_id,
            error=f"运行异常: {error}",
            duration_seconds=time.time() - started,
        )

    if session_map and result.session_id:
        session_map.set(task.chat_id, agent, result.session_id, generation=generation, topic=topic)

    error = result.error
    if agent == "dsh" and not result.ok and agents.is_already_owned(result):
        # The pre-flight probe only sees a lock that is already held; the app
        # can grab the write handle between the probe and dsh's own write open.
        error = LOCKED_HINT

    return TaskResult(
        task=task,
        ok=result.ok,
        text=result.text,
        session_id=result.session_id or session_id,
        error=error,
        duration_seconds=result.duration_seconds,
    )
