from pathlib import Path
from urllib.parse import unquote, urlparse

from ..events import Notification
from ..render import shorten, shorten_markdown

STATUS = {
    "completed": ("✅ DSH 会话完成", "green"),
    "failed": ("❌ DSH 会话失败", "red"),
    "error": ("❌ DSH 会话失败", "red"),
    "aborted": ("🛑 DSH 已中断", "orange"),
    "interrupted": ("🛑 DSH 已中断", "orange"),
    "blocked": ("⛔ DSH 被阻止", "orange"),
    "max-tokens": ("⚠️ DSH 达到 token 上限", "orange"),
}


def _cwd(value):
    value = str(value or "")
    if value.startswith("file://"):
        return unquote(urlparse(value).path)
    return value


def parse(payload, config):
    dsh = config.get("dsh", {})
    if not dsh.get("enabled", True):
        return None
    if payload.get("origin") == "subagent" and not dsh.get("include_subagents", False):
        return None

    status = str(payload.get("status") or "completed").lower()
    status_text, color = STATUS.get(status, ("✅ DSH 会话完成", "green"))
    cwd = _cwd(payload.get("cwd"))
    session_id = str(payload.get("session_id", ""))
    title = str(payload.get("title") or "").strip()
    if not title:
        title = Path(cwd).name or session_id or "DeepSeek Harness"

    recent_prompt = ""
    if dsh.get("include_recent_prompt", True):
        recent_prompt = shorten(
            payload.get("recent_prompt", ""),
            int(dsh.get("max_prompt_chars", 160)),
        )

    last_message = ""
    if dsh.get("include_last_message", True):
        last_message = shorten_markdown(
            payload.get("last_assistant_message", ""),
            int(dsh.get("max_message_chars", 600)),
        )

    return Notification(
        source="dsh",
        event="SessionComplete",
        status_text=status_text,
        color=color,
        title=title,
        project=Path(cwd).name if cwd else "",
        cwd=cwd,
        session_id=session_id,
        turn_id=str(payload.get("seq") or payload.get("completed_at") or ""),
        model=str(payload.get("model", "")),
        recent_prompt=recent_prompt,
        last_message=last_message,
        include_last_message=dsh.get("include_last_message", True),
        include_session_id=dsh.get("include_session_id", False),
        show_quota=False,
        show_thread_tokens=False,
        dedupe_key=f"dsh:{session_id}:{payload.get('seq') or payload.get('completed_at') or ''}:{status}",
    )
