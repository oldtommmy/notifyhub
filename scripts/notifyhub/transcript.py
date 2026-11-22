"""Read a dsh session's transcript so another session can inherit its context.

dsh allows exactly one active write handle per session id, and the desktop app
keeps that handle for as long as it has the session loaded. So the bridge can
never resume a session the app is showing. What it can do is read: the event
log on disk is a plain zstd-compressed JSONL file, and reading it needs no lock.

That is what this module provides. A Feishu-side session stays independent, but
it is seeded with a digest of the desktop conversation, so "I started this on my
Mac and want to keep talking about it on the phone" works without fighting the
app for the write handle.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_TOOL_CHARS = 400
MAX_ASSISTANT_CHARS = 2000


@dataclass
class Exchange:
    """One user turn and the assistant reply that followed it."""

    user: str = ""
    assistant: str = ""


@dataclass
class Digest:
    session_id: str = ""
    title: str = ""
    exchanges: list = field(default_factory=list)
    total_turns: int = 0
    truncated: bool = False

    @property
    def empty(self):
        return not self.exchanges


# ------------------------------------------------------------------ locating


def find_session_dir(session_id, dsh_home=None):
    """Resolve a session id to its directory, with or without the prefix."""
    if not session_id:
        return None
    home = Path(dsh_home or os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    sessions = home / "sessions"
    if not sessions.is_dir():
        return None
    needle = str(session_id).strip()
    for pattern in (needle, f"session-{needle}"):
        try:
            for found in sessions.glob(f"*/{pattern}"):
                if found.is_dir():
                    return found
        except OSError:
            return None
    return None


def _read_log(path):
    """Decompress the event log to text, preferring the zstandard module."""
    try:
        import zstandard  # optional
    except ImportError:
        zstandard = None

    if zstandard is not None:
        try:
            with open(path, "rb") as handle:
                reader = zstandard.ZstdDecompressor().stream_reader(handle)
                return reader.read().decode("utf-8", "replace")
        except Exception:
            pass

    try:
        done = subprocess.run(
            ["zstd", "-dc", str(path)], capture_output=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.decode("utf-8", "replace")


def _events(session_dir):
    """Yield parsed events, tolerant of a truncated tail."""
    for name in ("session.v4.jsonl.zstd", "session.v4.jsonl"):
        path = session_dir / name
        if not path.is_file():
            continue
        text = _read_log(path) if name.endswith(".zstd") else _read_text(path)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event
        return


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ----------------------------------------------------------------- extracting


def _flatten(content):
    """Turn a dsh content array into readable text."""
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
        elif kind == "tool-call":
            name = str(block.get("name") or "tool")
            parts.append(f"[调用 {name}]")
    return "\n".join(parts).strip()


def _clip(text, limit):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


def build_digest(session_id, dsh_home=None, turns=6, max_chars=12000):
    """Summarise the last `turns` exchanges of a dsh session."""
    digest = Digest(session_id=str(session_id or ""))
    session_dir = find_session_dir(session_id, dsh_home)
    if session_dir is None:
        return digest

    current = None
    for event in _events(session_dir):
        kind = event.get("type")
        data = event.get("data") or {}
        if kind == "session/title":
            title = str(data.get("title") or "").strip()
            if title:
                digest.title = title
        elif kind == "user/message":
            text = _flatten(data.get("content"))
            source = data.get("source") or {}
            # Tool results and other synthetic injections are not user turns.
            if not text or source.get("kind") not in (None, "user"):
                continue
            current = Exchange(user=text)
            digest.exchanges.append(current)
        elif kind == "assistant/message" and current is not None:
            text = _flatten((data.get("message") or {}).get("content"))
            if text:
                current.assistant = (
                    current.assistant + "\n" + text if current.assistant else text
                )

    digest.total_turns = len(digest.exchanges)
    if turns and turns > 0 and len(digest.exchanges) > turns:
        digest.exchanges = digest.exchanges[-turns:]
        digest.truncated = True

    for exchange in digest.exchanges:
        exchange.user = _clip(exchange.user, 1500)
        exchange.assistant = _clip(exchange.assistant, MAX_ASSISTANT_CHARS)

    # Trim from the oldest end until the digest fits the character budget.
    if max_chars and max_chars > 0:
        while len(digest.exchanges) > 1 and _digest_size(digest) > max_chars:
            digest.exchanges.pop(0)
            digest.truncated = True
    return digest


def _digest_size(digest):
    return sum(len(e.user) + len(e.assistant) for e in digest.exchanges)


def render_digest(digest, agent_label="dsh"):
    """Render a digest as the preamble prepended to the first Feishu message."""
    if digest.empty:
        return ""
    header = [
        f"以下是你在桌面 {agent_label} 会话里已经聊过的内容"
        + (f"（{digest.title}）" if digest.title else ""),
        f"共 {digest.total_turns} 轮对话，这里只带上了最近 {len(digest.exchanges)} 轮"
        + ("。" if digest.truncated else "，是全部内容。"),
        "这段内容是只读的上下文，用户接下来的问题接着这里继续。",
        "",
    ]
    body = []
    for index, exchange in enumerate(digest.exchanges, 1):
        body.append(f"【第 {index} 轮】")
        body.append(f"用户：{exchange.user}")
        if exchange.assistant:
            body.append(f"你：{exchange.assistant}")
        body.append("")
    tail = ["--- 以上是历史，以下开始新的对话 ---", ""]
    return "\n".join(header + body + tail)