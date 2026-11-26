"""Notifications from the Antigravity Stop lifecycle hook."""

import hashlib
import re
import sqlite3
from pathlib import Path

from ..events import Notification
from ..render import shorten, shorten_markdown
from ..transcripts import _read_events


def clean_user_input(text):
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.S)
    return match.group(1).strip() if match else text.strip()


def session_metadata(conversation):
    path = Path.home() / ".gemini/antigravity/conversation_summaries.db"
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT title FROM conversation_summaries WHERE conversation_id = ?", (conversation,)).fetchone()
            return dict(row) if row else {}
    except sqlite3.Error:
        return {}


def turn_text(path):
    first, prompt, answer = "", "", ""
    turn = ""
    if not path:
        return first, prompt, answer, turn
    for item in _read_events(path):
        if not isinstance(item, dict) or item.get("status") != "DONE":
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        kind = item.get("type")
        if kind == "USER_INPUT":
            first = first or clean_user_input(content)
            prompt, answer = clean_user_input(content), ""
            turn = str(item.get("created_at") or item.get("step_index") or "")
        elif kind == "PLANNER_RESPONSE" and prompt:
            answer = content
    return first, prompt, answer, turn


def parse(payload, config):
    settings = config.get("antigravity", {})
    conversation = str(payload.get("conversationId") or "")
    execution = payload.get("executionNum")
    reason = str(payload.get("terminationReason") or "")
    if (not settings.get("enabled", True) or payload.get("fullyIdle") is not True
            or not conversation or execution is None or not reason):
        return None
    first, recorded_prompt, answer, turn = turn_text(payload.get("transcriptPath"))
    prompt = payload.get("lastUserInput") or recorded_prompt
    if not isinstance(prompt, str):
        prompt = recorded_prompt
    prompt = clean_user_input(prompt)
    if prompt.strip() != recorded_prompt.strip():
        answer = ""
    workspaces = payload.get("workspacePaths") or []
    cwd = workspaces[0] if isinstance(workspaces, list) and workspaces and isinstance(workspaces[0], str) else ""
    cwd = cwd or str(payload.get("cwd") or "")
    project = Path(cwd).name if cwd else ""
    done = reason.lower() in ("model_stop", "no_tool_call") and not payload.get("error")
    status = "✅ Antigravity 本轮完成" if done else "⚠️ Antigravity 本轮结束"
    digest = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    return Notification(
        source="antigravity", event="Stop", status_text=status, color="green" if done else "orange",
        title=shorten(session_metadata(conversation).get("title") or first or project or "Antigravity", 60), project=project, cwd=cwd,
        session_id=conversation, turn_id=turn or str(execution), model=str(payload.get("modelName") or ""),
        recent_prompt=shorten(prompt, int(settings.get("max_prompt_chars", 160))) if settings.get("include_recent_prompt", True) else "",
        last_message=shorten_markdown(answer, int(settings.get("max_message_chars", 600))),
        include_last_message=settings.get("include_last_message", True),
        include_session_id=settings.get("include_session_id", False),
        dedupe_key=f"antigravity:{conversation}:{turn or execution}:{digest}",
        extra_lines=["[在 Antigravity 继续会话](https://antigravity.google.com/)"] if settings.get("remote_control_link", False) else [],
    )
