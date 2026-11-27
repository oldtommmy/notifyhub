from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Notification:
    source: str
    event: str
    status_text: str
    color: str = "blue"
    title: str = ""
    project: str = ""
    cwd: str = ""
    session_id: str = ""
    turn_id: str = ""
    model: str = ""
    goal_status: str = ""
    goal_objective: str = ""
    goal_tokens_used: int | None = None
    goal_token_budget: int | None = None
    goal_time_used_seconds: int | None = None
    thread_tokens_used: int | None = None
    quota_line: str = ""
    show_quota: bool = False
    recent_prompt: str = ""
    last_message: str = ""
    include_last_message: bool = True
    include_session_id: bool = False
    show_thread_tokens: bool = False
    dedupe_key: str = ""
    extra_lines: list[str] = field(default_factory=list)
    full_answer: str = ""
