"""Discover sessions that already exist on this machine.

The bridge only knows about conversations it started. This module lists what is
already on disk so a Mac-side session can be attached to a Feishu topic and
continued from the phone.

Nothing here runs an agent; it only reads metadata.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExistingSession:
    agent: str
    session_id: str
    title: str = ""
    cwd: str = ""
    updated_at: float = 0.0

    def age_hours(self, now=None):
        now = time.time() if now is None else now
        return max(0.0, (now - self.updated_at) / 3600) if self.updated_at else 0.0


def dsh_home():
    return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


# ----------------------------------------------------------------------- dsh


def _dsh_title(path):
    """Read the session title from the compressed event log, best effort."""
    title = ""
    try:
        import zstandard  # optional
    except ImportError:
        zstandard = None

    try:
        raw = path.read_bytes()
    except OSError:
        return ""

    text = None
    if zstandard is not None:
        try:
            text = zstandard.ZstdDecompressor().decompressobj().decompress(raw).decode(
                "utf-8", "replace"
            )
        except Exception:
            text = None
    if text is None:
        # Fall back to the system zstd binary when the module is absent.
        import subprocess

        try:
            done = subprocess.run(
                ["zstd", "-dc", str(path)],
                capture_output=True,
                timeout=10,
            )
            text = done.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            return ""

    for line in text.splitlines():
        if '"session/title"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        value = ((event.get("data") or {}).get("title") or "").strip()
        if value:
            title = value
    return title


def list_dsh_sessions(limit=20):
    """Sessions under <DSH_HOME>/sessions/<cwd-slug>/<session-id>/."""
    root = dsh_home() / "sessions"
    if not root.is_dir():
        return []

    found = []
    try:
        chat_dirs = [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        return []

    for chat_dir in chat_dirs:
        try:
            session_dirs = [path for path in chat_dir.iterdir() if path.is_dir()]
        except OSError:
            continue
        for directory in session_dirs:
            log = directory / "session.v4.jsonl.zstd"
            if not log.is_file():
                continue
            try:
                stamp = log.stat().st_mtime
            except OSError:
                stamp = 0.0
            found.append(
                ExistingSession(
                    agent="dsh",
                    session_id=directory.name,
                    title=_dsh_title(log),
                    cwd=_dsh_cwd_from_slug(chat_dir.name),
                    updated_at=stamp,
                )
            )

    found.sort(key=lambda item: item.updated_at, reverse=True)
    return found[:limit]


def _dsh_cwd_from_slug(slug):
    """`--Users-cyh-proj--` -> `/Users/cyh/proj`."""
    name = str(slug or "")
    if not (name.startswith("--") and name.endswith("--")):
        return ""
    inner = name[2:-2]
    return "/" + inner.replace("-", "/") if inner else ""


# --------------------------------------------------------------------- codex


def _codex_session_meta(path):
    """session_id, cwd, and the first real user message as a title."""
    session_id = ""
    cwd = ""
    title = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index > 200:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                payload = event.get("payload") or {}
                kind = event.get("type")
                if kind == "session_meta":
                    session_id = str(payload.get("id") or "")
                    cwd = str(payload.get("cwd") or "")
                elif kind == "response_item" and not title:
                    if payload.get("type") == "message" and payload.get("role") == "user":
                        title = _codex_text(payload.get("content"))
                if session_id and title:
                    break
    except OSError:
        return "", "", ""
    return session_id, cwd, title


def _codex_text(content):
    """Join user text parts, skipping injected context blocks.

    Codex prepends system blocks as user messages: <environment_context>,
    <recommended_plugins>, <user_instructions>, and similar. They all start
    with a tag, so anything of that shape is not a real prompt.
    """
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("input_text", "text"):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if text.startswith("<") or text.startswith("The following "):
            continue
        parts.append(text)
    return " ".join(parts).strip()[:120]


def list_codex_sessions(limit=20, days=30):
    """Rollout files under <CODEX_HOME>/sessions/YYYY/MM/DD/."""
    root = codex_home() / "sessions"
    if not root.is_dir():
        return []

    cutoff = time.time() - days * 86400
    paths = []
    try:
        for path in root.rglob("rollout-*.jsonl"):
            try:
                if path.stat().st_mtime >= cutoff:
                    paths.append(path)
            except OSError:
                continue
    except OSError:
        return []

    paths.sort(key=lambda item: item.stat().st_mtime, reverse=True)

    found = []
    for path in paths[: limit * 2]:
        session_id, cwd, title = _codex_session_meta(path)
        if not session_id:
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            stamp = 0.0
        found.append(
            ExistingSession(
                agent="codex",
                session_id=session_id,
                title=title,
                cwd=cwd,
                updated_at=stamp,
            )
        )
        if len(found) >= limit:
            break
    return found


# -------------------------------------------------------------------- 通用


def list_sessions(agent=None, limit=20):
    items = []
    if agent in (None, "dsh"):
        items.extend(list_dsh_sessions(limit=limit))
    if agent in (None, "codex"):
        items.extend(list_codex_sessions(limit=limit))
    items.sort(key=lambda item: item.updated_at, reverse=True)
    return items[:limit]