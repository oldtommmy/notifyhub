import json
from pathlib import Path

MAX_BYTES = 8_000_000
HEAD_BYTES = 512_000

SYNTHETIC_PREFIXES = (
    "<system-reminder>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "[Request interrupted",
)


def _text_from_content(content):
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            value = part.get("text") or part.get("content") or ""
            if value:
                parts.append(str(value))
        elif kind is None and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "\n".join(parts).strip()


def _is_synthetic_user_text(text):
    value = text.lstrip()
    return any(value.startswith(prefix) for prefix in SYNTHETIC_PREFIXES)


def _read_bytes(path, start, length):
    try:
        with Path(path).expanduser().open("rb") as handle:
            handle.seek(start)
            return handle.read(length).decode("utf-8", "replace")
    except OSError:
        return ""


def _read_events(path, max_bytes=MAX_BYTES, head_bytes=HEAD_BYTES):
    target = Path(path).expanduser()
    try:
        size = target.stat().st_size
    except OSError:
        return []

    if size <= max_bytes + head_bytes:
        raw = _read_bytes(target, 0, size)
    else:
        # The session header and the first prompt live at the head; the last
        # turn lives at the tail. Read both and drop the partial boundary lines.
        head_lines = _read_bytes(target, 0, head_bytes).splitlines()
        tail_lines = _read_bytes(target, size - max_bytes, max_bytes).splitlines()
        if head_lines:
            head_lines = head_lines[:-1]
        if tail_lines:
            tail_lines = tail_lines[1:]
        raw = "\n".join(head_lines + tail_lines)

    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def scan(path):
    """Summarize a Claude Code / Command Code JSONL transcript.

    Only user-visible ``text`` blocks are considered: ``thinking``, ``tool_use``,
    ``tool_result``, and other non-text blocks never become the notification body
    or the recent prompt.
    """
    result = {
        "session_id": "",
        "cwd": "",
        "title": "",
        "recent_prompt": "",
        "last_message": "",
        "model": "",
        "turn_id": "",
        "first_user_text": "",
        "last_user_text": "",
    }

    for event in _read_events(path):
        if not isinstance(event, dict):
            continue
        if event.get("isSidechain"):
            continue
        if not result["session_id"] and event.get("sessionId"):
            result["session_id"] = str(event["sessionId"])

        kind = event.get("type")
        if kind == "session":
            result["session_id"] = str(event.get("id") or result["session_id"])
            result["cwd"] = str(event.get("cwd") or result["cwd"])
            continue
        if kind == "ai-title":
            title = str(event.get("aiTitle") or "").strip()
            if title:
                result["title"] = title
            continue
        if kind == "last-prompt":
            prompt = str(event.get("lastPrompt") or "").strip()
            if prompt and not _is_synthetic_user_text(prompt):
                result["recent_prompt"] = prompt
            continue
        if kind == "model_change":
            model = str(event.get("model") or "").strip()
            if model:
                result["model"] = model
            continue

        message = event.get("message")
        if not isinstance(message, dict):
            if event.get("role"):
                message = event
            else:
                continue
        role = str(message.get("role") or event.get("role") or "").lower()
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"
        if role not in ("user", "assistant"):
            continue

        text = _text_from_content(message.get("content"))
        if not text:
            continue

        if role == "user":
            if _is_synthetic_user_text(text):
                continue
            if not result["first_user_text"]:
                result["first_user_text"] = text
            result["last_user_text"] = text
            result["recent_prompt"] = text
        else:
            result["last_message"] = text
            result["turn_id"] = str(
                event.get("uuid") or event.get("id") or message.get("id") or ""
            )
            model = str(event.get("model") or message.get("model") or "").strip()
            if model:
                result["model"] = model

    if not result["recent_prompt"]:
        result["recent_prompt"] = result["last_user_text"]
    if not result["title"]:
        result["title"] = result["first_user_text"]
    return result