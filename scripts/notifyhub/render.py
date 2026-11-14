import re

from .events import Notification

GOAL_LABELS = {
    "active": "进行中",
    "paused": "已暂停",
    "blocked": "受阻",
    "usage_limited": "额度受限",
    "budget_limited": "预算耗尽",
    "complete": "已完成",
}


def shorten(text, limit):
    collapsed = " ".join(str(text or "").split())
    if limit and len(collapsed) > limit:
        return collapsed[: max(0, limit - 1)].rstrip() + "…"
    return collapsed


def shorten_markdown(text, limit):
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"\n{3,}", "\n\n", value)
    if limit and len(value) > limit:
        value = value[: max(0, limit - 1)].rstrip() + "…"
    return value


def goal_parts(notification: Notification, max_chars=120):
    if not notification.goal_status:
        return "", ""
    label = GOAL_LABELS.get(notification.goal_status, notification.goal_status)
    objective = shorten(notification.goal_objective, max_chars) or "(无标题)"

    usage = []
    if notification.goal_tokens_used is not None:
        if notification.goal_token_budget:
            usage.append(f"{notification.goal_tokens_used:,}/{notification.goal_token_budget:,} tokens")
        else:
            usage.append(f"{notification.goal_tokens_used:,} tokens")
    if notification.goal_time_used_seconds:
        seconds = notification.goal_time_used_seconds
        usage.append(f"{seconds // 60}m{seconds % 60:02d}s")

    meta = " · ".join([label] + usage)
    return objective, meta


def goal_line(notification: Notification, max_chars=120):
    objective, meta = goal_parts(notification, max_chars)
    if not objective:
        return ""
    return f"Goal: {objective} · {meta}" if meta else f"Goal: {objective}"


def meta_lines(notification: Notification):
    lines = []
    if notification.project:
        lines.append(f"项目: {notification.project}")
    if notification.title and notification.title != notification.project:
        lines.append(f"会话: {notification.title}")
    if notification.model:
        lines.append(f"模型: {notification.model}")

    if notification.recent_prompt:
        lines.append(f"最近提问: {notification.recent_prompt}")

    goal = goal_line(notification)
    if goal:
        lines.append(goal)

    if notification.show_quota and notification.quota_line:
        lines.append(notification.quota_line)

    if notification.show_thread_tokens and notification.thread_tokens_used:
        lines.append(f"累计 tokens: {notification.thread_tokens_used:,}")

    lines.extend(notification.extra_lines)

    return lines


def body_lines(notification: Notification):
    lines = meta_lines(notification)
    if notification.include_last_message and notification.last_message:
        if lines:
            lines.append("")
        lines.append(notification.last_message)
    return lines


def card_fields(notification: Notification):
    fields = []

    if notification.recent_prompt:
        fields.append(
            {
                "is_short": False,
                "text": {"tag": "lark_md", "content": f"**最近提问**\n{notification.recent_prompt}"},
            }
        )

    short = []
    if notification.model:
        short.append(("模型", notification.model))
    if notification.show_quota and notification.quota_line:
        short.append(("剩余额度", notification.quota_line.replace("剩余额度: ", "", 1)))
    for label, value in short:
        fields.append(
            {
                "is_short": len(short) > 1,
                "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"},
            }
        )

    objective, goal_meta = goal_parts(notification)
    if objective:
        value = objective
        if goal_meta:
            value += f"\n{goal_meta}"
        fields.append(
            {
                "is_short": False,
                "text": {"tag": "lark_md", "content": f"**Goal**\n{value}"},
            }
        )

    if notification.show_thread_tokens and notification.thread_tokens_used:
        fields.append(
            {
                "is_short": False,
                "text": {"tag": "lark_md", "content": f"**累计 tokens**\n{notification.thread_tokens_used:,}"},
            }
        )

    if notification.include_session_id and notification.session_id:
        fields.append(
            {
                "is_short": False,
                "text": {"tag": "lark_md", "content": f"**Session**\n{notification.session_id}"},
            }
        )

    if notification.extra_lines:
        fields.append(
            {
                "is_short": False,
                "text": {"tag": "lark_md", "content": "\n".join(notification.extra_lines)},
            }
        )

    return fields


def render_text(notification: Notification):
    header = f"[{notification.source.upper()}] {notification.status_text}"
    if notification.project:
        header += f" · {notification.project}"

    lines = [header]
    lines.extend(body_lines(notification))
    if notification.include_session_id and notification.session_id:
        lines.append(f"Session: {notification.session_id}")
    return "\n".join(lines)


def build_card(notification: Notification, at_all=False):
    elements = []
    fields = card_fields(notification)
    if fields:
        elements.append({"tag": "div", "fields": fields})

    if notification.include_last_message and notification.last_message:
        if fields:
            elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": notification.last_message})

    if at_all:
        elements.append({"tag": "markdown", "content": '<at id="all"></at>'})

    subtitle = notification.title or ""
    if not subtitle or subtitle == "Codex":
        subtitle = notification.project or "未命名会话"
    if subtitle == "Codex":
        subtitle = "未命名会话"

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "template": notification.color,
            "title": {"tag": "plain_text", "content": shorten(notification.status_text, 60)},
            "subtitle": {"tag": "plain_text", "content": shorten(subtitle, 80)},
        },
        "body": {"elements": elements},
    }
