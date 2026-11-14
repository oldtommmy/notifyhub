import fnmatch
from datetime import datetime

from .channels import get_sender
from .config import resolve_path
from .state import StateStore


def _matches_project(notification, filters):
    targets = [value for value in (notification.cwd, notification.project) if value]

    deny = filters.get("projects_deny") or []
    if any(fnmatch.fnmatch(target, pattern) for pattern in deny for target in targets):
        return False

    allow = filters.get("projects_allow") or []
    if allow and not any(fnmatch.fnmatch(target, pattern) for pattern in allow for target in targets):
        return False

    return True


def _in_quiet_hours(config):
    settings = config.get("filters", {}).get("quiet_hours") or {}
    if not settings.get("enabled"):
        return False
    try:
        start_hour, start_minute = (int(part) for part in settings["start"].split(":"))
        end_hour, end_minute = (int(part) for part in settings["end"].split(":"))
    except (KeyError, ValueError):
        return False

    now = datetime.now()
    current = now.hour * 60 + now.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute

    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def dispatch(notification, config, logger, force=False, only_channel=None):
    if not force and not _matches_project(notification, config.get("filters", {})):
        logger.info("filtered by project rules: %s", notification.cwd)
        return []

    if not force and _in_quiet_hours(config):
        logger.info("suppressed by quiet hours")
        return []

    state = StateStore(resolve_path(config.get("state_path", "~/.local/state/notifyhub/state.json")))
    if not force and state.is_seen(notification.dedupe_key):
        logger.info("duplicate notification ignored: %s", notification.dedupe_key)
        return []

    results = []
    for name, settings in config.get("channels", {}).items():
        if only_channel and name != only_channel:
            continue
        if not settings.get("enabled"):
            continue

        try:
            sender = get_sender(name)
            sender(notification, settings, logger)
        except Exception as error:
            logger.error("%s delivery failed: %s", name, error)
            results.append((name, False, str(error)))
            continue

        logger.info("%s delivered: %s", name, notification.status_text)
        results.append((name, True, ""))

    if not results:
        logger.warning("no enabled channel configured")

    if any(ok for _, ok, _ in results):
        state.mark(notification.dedupe_key)
        state.save()

    return results
