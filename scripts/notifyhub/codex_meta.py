import json
import os
import re
import sqlite3
from pathlib import Path

from .transcripts import _read_events


def clean_prompt(text):
    text = str(text or "").strip()
    if text.startswith(("# Files mentioned by the user:", "Files mentioned by the user:")):
        marker = re.search(r"(?m)^## My request:\s*\n", text)
        if marker:
            return text[marker.end():].strip()
        return "附件消息"
    if text.startswith(("<environment_context>", "<permissions instructions>", "<turn_aborted>")):
        return ""
    return text


def read_turn_prompt(thread, turn_id):
    """Read user text belonging to the hook's exact turn from its local rollout."""
    path = thread.get("rollout_path")
    if not path or not turn_id:
        return ""
    active = False
    prompt = ""
    for row in _read_events(path):
        payload = row.get("payload") or {}
        kind = row.get("type")
        if kind == "event_msg" and payload.get("type") == "task_started":
            active = payload.get("turn_id") == turn_id
        elif kind == "turn_context":
            active = payload.get("turn_id") == turn_id
        elif active and kind == "response_item" and payload.get("role") == "user":
            parts = [part.get("text", "") for part in payload.get("content", [])
                     if isinstance(part, dict) and part.get("type") in ("text", "input_text")
                     and not str(part.get("text", "")).lstrip().startswith(("<image ", "</image>"))]
            text = clean_prompt("\n".join(parts))
            if text:
                prompt = text
        elif active and kind == "event_msg" and payload.get("type") == "user_message":
            text = clean_prompt(payload.get("message", ""))
            if text:
                prompt = text
    return prompt


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def _version(path: Path) -> int:
    match = re.search(r"_(\d+)\.sqlite$", path.name)
    return int(match.group(1)) if match else -1


def find_db(prefix: str):
    try:
        candidates = sorted(codex_home().glob(f"{prefix}_*.sqlite"), key=_version)
    except OSError:
        return None
    return candidates[-1] if candidates else None


def _connect(path: Path):
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error:
        return None


def read_thread(session_id: str):
    path = find_db("state")
    if not session_id or path is None:
        return None
    connection = _connect(path)
    if connection is None:
        return None
    try:
        row = connection.execute("SELECT * FROM threads WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def read_recent_prompt(session_id: str):
    if not session_id:
        return ""
    path = codex_home() / "history.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 262144))
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""

    for line in reversed(data.splitlines()):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("session_id") == session_id:
            text = str(item.get("text", "")).strip()
            if text:
                return text
    return ""


def read_goal(session_id: str):
    path = find_db("goals")
    if not session_id or path is None:
        return None
    connection = _connect(path)
    if connection is None:
        return None
    try:
        row = connection.execute(
            "SELECT objective, status, token_budget, tokens_used, time_used_seconds, updated_at_ms "
            "FROM thread_goals WHERE thread_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()
