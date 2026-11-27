from hashlib import sha1
from pathlib import Path

from ..events import Notification
from ..render import shorten, shorten_markdown
from ..transcripts import scan

EVENT_STATUS = {
    "Stop": ("✅ 本轮完成", "green"),
    "SubagentStop": ("🤖 子任务完成", "blue"),
    "SessionEnd": ("💤 会话结束", "grey"),
    "Notification": ("🔔 通知", "blue"),
}


def parse_hook(payload, settings, source, default_title, model_fallback=""):
    event = str(payload.get("hook_event_name") or "Stop")
    if event not in settings.get("events", ["Stop"]):
        return None

    transcript = str(payload.get("transcript_path") or payload.get("transcriptPath") or "")
    data = scan(transcript) if transcript else {}

    session_id = str(payload.get("session_id") or data.get("session_id") or "")
    cwd = str(payload.get("cwd") or data.get("cwd") or "")
    title = (
        str(data.get("title") or "").strip()
        or str(data.get("first_user_text") or "").strip()
        or Path(cwd).name
        or default_title
    )

    include_last_message = bool(settings.get("include_last_message", True))
    full_answer = str(payload.get("last_assistant_message") or data.get("last_message") or "").strip()
    if not include_last_message:
        full_answer = ""
    last_message = shorten_markdown(full_answer, int(settings.get("max_message_chars", 600)))

    recent_prompt = ""
    if settings.get("include_recent_prompt", True):
        recent_prompt = shorten(
            str(data.get("recent_prompt") or payload.get("prompt") or ""),
            int(settings.get("max_prompt_chars", 160)),
        )

    model = str(payload.get("model") or data.get("model") or model_fallback or "")
    status_text, color = EVENT_STATUS.get(event, (f"✅ {event}", "green"))
    turn_id = str(payload.get("turn_id") or data.get("turn_id") or "")
    digest = sha1((last_message or recent_prompt or session_id).encode("utf-8")).hexdigest()[:12]

    return Notification(
        source=source,
        event=event,
        status_text=status_text,
        color=color,
        title=title,
        project=Path(cwd).name if cwd else "",
        cwd=cwd,
        session_id=session_id,
        turn_id=turn_id,
        model=model,
        recent_prompt=recent_prompt,
        last_message=last_message,
        include_last_message=include_last_message,
        include_session_id=settings.get("include_session_id", False),
        show_quota=False,
        show_thread_tokens=False,
        dedupe_key=f"{source}:{event}:{session_id}:{turn_id or digest}",
        full_answer=full_answer,
    )
