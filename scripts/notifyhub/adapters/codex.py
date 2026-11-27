import json
from pathlib import Path

from .. import quota
from ..codex_meta import clean_prompt, read_goal, read_recent_prompt, read_thread, read_turn_prompt
from ..events import Notification
from ..render import shorten, shorten_markdown

EVENT_STATUS = {
    "SubagentStop": ("🤖 子任务完成", "blue"),
    "Interrupt": ("🛑 已中断", "orange"),
    "SessionEnd": ("💤 会话结束", "grey"),
}


def _goal_status_text(goal, notify_goal_rounds):
    if not goal:
        return "✅ 本轮完成", "green"

    status = goal.get("status", "")
    if status == "complete":
        return "🎉 Goal 完成", "green"
    if status == "blocked":
        return "⛔ Goal 受阻", "red"
    if status == "paused":
        return "⏸️ Goal 已暂停", "orange"
    if status in ("usage_limited", "budget_limited"):
        return "⚠️ Goal 额度受限", "orange"
    if status:
        if not notify_goal_rounds:
            return None
        return "🔁 Goal 进行中", "blue"
    return "✅ 本轮完成", "green"


def parse(payload, config):
    codex_config = config["codex"]
    if not codex_config.get("enabled", True):
        return None

    event = payload.get("hook_event_name", "")
    if event not in set(codex_config.get("events", ["Stop"])):
        return None

    session_id = str(payload.get("session_id", ""))
    thread = read_thread(session_id) or {}
    if not thread:
        # Desktop suggestion generation also emits Stop for ephemeral threads.
        # Recognize its output contract without suppressing a user's saved chat.
        try:
            output = json.loads(payload.get("last_assistant_message", ""))
        except (ValueError, TypeError):
            output = None
        if (isinstance(output, dict) and set(output) == {"suggestions"}
                and isinstance(output["suggestions"], list)
                and all(isinstance(item, dict) and {"title", "description", "prompt", "appId", "pluginId"} <= item.keys()
                        for item in output["suggestions"])):
            return None
    goal = read_goal(session_id)

    if event == "Stop":
        status = _goal_status_text(goal, codex_config.get("notify_goal_rounds", True))
        if status is None:
            return None
        status_text, color = status
    else:
        status_text, color = EVENT_STATUS.get(event, (f"Codex {event}", "blue"))

    cwd = payload.get("cwd") or thread.get("cwd") or ""
    project = Path(cwd).name if cwd else ""
    title = thread.get("title") or project or "Codex"

    include_last_message = codex_config.get("include_last_message", True)
    full_answer = str(payload.get("last_assistant_message") or "").strip() if include_last_message else ""
    last_message = shorten_markdown(full_answer, int(codex_config.get("max_message_chars", 600)))

    goal_status = goal.get("status", "") if goal else ""
    turn_id = str(payload.get("turn_id", ""))
    agent_id = str(payload.get("agent_id", "") or payload.get("agent_type", ""))

    recent_prompt = ""
    if codex_config.get("include_recent_prompt", True):
        recent_prompt = (
            read_turn_prompt(thread, turn_id)
            or read_recent_prompt(session_id)
            or thread.get("preview")
            or thread.get("first_user_message")
            or ""
        )
        recent_prompt = shorten(clean_prompt(recent_prompt), int(codex_config.get("max_prompt_chars", 160)))

    quota_line = ""
    show_quota = False
    if codex_config.get("include_remaining_quota", True):
        quota_data = quota.get(timeout=8)
        quota_line = quota.format_line(quota_data)
        show_quota = bool(quota_line)

    return Notification(
        source="codex",
        event=event,
        status_text=status_text,
        color=color,
        title=title,
        project=project,
        cwd=cwd,
        session_id=session_id,
        turn_id=turn_id,
        model=payload.get("model") or thread.get("model") or "",
        goal_status=goal_status,
        goal_objective=goal.get("objective", "") if goal else "",
        goal_tokens_used=goal.get("tokens_used") if goal else None,
        goal_token_budget=goal.get("token_budget") if goal else None,
        goal_time_used_seconds=goal.get("time_used_seconds") if goal else None,
        thread_tokens_used=thread.get("tokens_used"),
        quota_line=quota_line,
        show_quota=show_quota,
        recent_prompt=recent_prompt,
        last_message=last_message,
        include_last_message=include_last_message,
        include_session_id=codex_config.get("include_session_id", False),
        show_thread_tokens=False,
        dedupe_key=f"codex:{event}:{session_id}:{turn_id}:{agent_id}",
        full_answer=full_answer,
    )
