import copy
import json
import os
import re
from pathlib import Path

DEFAULT_CONFIG = {
    "version": 1,
    "channels": {
        "feishu": {
            # Off by default: an enabled channel with an empty webhook_url logs
            # an ERROR on every single notification, which buries real failures.
            "enabled": False,
            "webhook_url": "",
            "secret": "",
            "message_type": "card",
            "at_all": False,
            "timeout_seconds": 5,
        },
        "feishu_app": {
            "enabled": False,
            "base_url": "https://open.feishu.cn",
            "app_id": "",
            "app_secret": "",
            "receive_id_type": "email",
            "receive_id": "",
            "message_type": "card",
            "at_all": False,
            "timeout_seconds": 5,
        },
        "imessage": {
            "enabled": False,
            "to": "",
            "max_chars": 3000,
            "timeout_seconds": 15,
        },
    },
    "codex": {
        "enabled": True,
        "include_last_message": True,
        "max_message_chars": 600,
        "notify_goal_rounds": True,
        "include_session_id": False,
        "include_thread_tokens": False,
        "include_remaining_quota": True,
        "include_recent_prompt": True,
        "max_prompt_chars": 160,
        "events": ["Stop"],
    },
    "dsh": {
        "enabled": True,
        "include_last_message": True,
        "include_recent_prompt": True,
        "include_subagents": False,
        "include_session_id": False,
        "max_message_chars": 600,
        "max_prompt_chars": 160,
    },
    "claude": {
        "enabled": True,
        "include_last_message": True,
        "include_recent_prompt": True,
        "include_session_id": False,
        "max_message_chars": 600,
        "max_prompt_chars": 160,
        "events": ["Stop"],
    },
    "commandcode": {
        "enabled": True,
        "include_last_message": True,
        "include_recent_prompt": True,
        "include_session_id": False,
        "max_message_chars": 600,
        "max_prompt_chars": 160,
        "events": ["Stop"],
    },
    "filters": {
        "projects_allow": [],
        "projects_deny": [],
        "quiet_hours": {"enabled": False, "start": "23:00", "end": "08:00"},
    },
    "state_path": "~/.local/state/notifyhub/state.json",
    "log_path": "~/.local/state/notifyhub/notifyhub.log",
    "outbox": {
        # Failed deliveries are persisted here and replayed by
        # `notifyhub flush`, so a dead proxy does not silently lose them.
        "enabled": True,
        "path": "~/.local/state/notifyhub/outbox.json",
        "max_items": 200,
        "max_age_hours": 72,
        "max_attempts": 8,
    },
    "bridge": {
        # Inbound Feishu messages -> local headless agent. Off by default: it
        # grants anyone in allow_open_ids the ability to run commands.
        "enabled": False,
        # MUST be filled. Empty means "deny everyone", never "allow everyone".
        "allow_open_ids": [],
        "default_agent": "dsh",
        # Workspace the agent runs in. Empty means the config's own directory.
        "workspace": "~",
        # Nobody is at the keyboard, so approvals cannot be interactive.
        "approval_policy": "never",
        "permission_mode": "workspace-write",
        "timeout_seconds": 1800,
        "max_message_chars": 3000,
        # Reject a new task while the same session is still running.
        "reject_when_busy": True,
        "sessions_path": "~/.local/state/notifyhub/bridge_sessions.json",
        # A dsh session is only blocked when another process actually holds the
        # write lock (flock on session.lock). The lock file itself is never
        # deleted, so it is not a "busy" signal, and an idle session merely
        # loaded in the desktop app can be resumed normally.
        # /handoff copies a desktop conversation into the phone session. How
        # much to carry over: recent exchanges, and a hard character ceiling.
        "handoff_turns": 6,
        "handoff_max_chars": 12000,
        "reply": {
            "ack": True,
            "on_start": True,
            "on_finish": True,
        },
        # Optional command prefixes, e.g. {"codex": "@codex ", "dsh": "@dsh "}.
        # A bare message uses default_agent.
        "prefixes": {"dsh": "", "codex": ""},
    },
}

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def config_path() -> Path:
    override = os.environ.get("NOTIFYHUB_CONFIG")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "notifyhub" / "config.json"


def resolve_path(value: str) -> Path:
    return Path(os.path.expanduser(value))


def _expand_env(value):
    if isinstance(value, str):
        return _ENV_RE.sub(lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path=None, expand=True):
    target = resolve_path(str(path)) if path else config_path()
    data = {}
    if target.exists():
        with target.open(encoding="utf-8") as handle:
            data = json.load(handle)
    merged = deep_merge(DEFAULT_CONFIG, data)
    if not expand:
        return merged

    feishu = merged["channels"]["feishu"]
    if os.environ.get("NOTIFYHUB_FEISHU_WEBHOOK"):
        feishu["webhook_url"] = os.environ["NOTIFYHUB_FEISHU_WEBHOOK"]
    if os.environ.get("NOTIFYHUB_FEISHU_SECRET"):
        feishu["secret"] = os.environ["NOTIFYHUB_FEISHU_SECRET"]

    feishu_app = merged["channels"]["feishu_app"]
    if os.environ.get("NOTIFYHUB_FEISHU_APP_ID"):
        feishu_app["app_id"] = os.environ["NOTIFYHUB_FEISHU_APP_ID"]
    if os.environ.get("NOTIFYHUB_FEISHU_APP_SECRET"):
        feishu_app["app_secret"] = os.environ["NOTIFYHUB_FEISHU_APP_SECRET"]
    if os.environ.get("NOTIFYHUB_FEISHU_RECEIVE_ID"):
        feishu_app["receive_id"] = os.environ["NOTIFYHUB_FEISHU_RECEIVE_ID"]
    if os.environ.get("NOTIFYHUB_FEISHU_RECEIVE_ID_TYPE"):
        feishu_app["receive_id_type"] = os.environ["NOTIFYHUB_FEISHU_RECEIVE_ID_TYPE"]

    return _expand_env(merged)


def save_config(config, path=None):
    target = resolve_path(str(path)) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def ensure_config(path=None, force=False):
    target = resolve_path(str(path)) if path else config_path()
    if target.exists() and not force:
        return target, False
    save_config(DEFAULT_CONFIG, target)
    return target, True
