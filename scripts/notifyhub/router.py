import fnmatch
from datetime import datetime

from .channels import get_sender
from .config import resolve_path
from .outbox import notification_from_dict, outbox_for
from .state import StateStore, state_lock
from .channels.feishu_app import AmbiguousSendError


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


REQUIRED_FIELDS = {
    "feishu": ("webhook_url",),
    "feishu_app": ("app_id", "app_secret", "receive_id"),
    "imessage": ("to",),
}


def _is_configured(name, settings):
    """A channel must have every field it needs before it counts as enabled."""
    required = REQUIRED_FIELDS.get(name)
    if required is None:
        return True
    return all(settings.get(field) for field in required)


def dispatch(notification, config, logger, force=False, only_channel=None):
    path = resolve_path(config.get("state_path", "~/.local/state/notifyhub/state.json"))
    with state_lock(path):
        return _dispatch_locked(notification, config, logger, force, only_channel)


def _dispatch_locked(notification, config, logger, force=False, only_channel=None, queue_failure=True):
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
    uncertain = False
    for name, settings in config.get("channels", {}).items():
        if only_channel and name != only_channel:
            continue
        if not settings.get("enabled"):
            continue
        if not _is_configured(name, settings):
            # Enabled but missing its required field. Log once at WARNING
            # instead of raising an ERROR per notification: this is a config
            # mistake, not a delivery failure, and it used to bury real ones.
            logger.warning(
                "%s is enabled but not configured; skipping (see notifyhub doctor)",
                name,
            )
            continue

        try:
            sender = get_sender(name)
            sender(notification, settings, logger)
        except AmbiguousSendError as error:
            uncertain = True
            logger.warning("%s delivery outcome unknown; suppressing automatic resend: %s", name, error)
            results.append((name, False, str(error)))
            continue
        except Exception as error:
            logger.error("%s delivery failed: %s", name, error)
            results.append((name, False, str(error)))
            continue

        logger.info("%s delivered: %s", name, notification.status_text)
        results.append((name, True, ""))

    if not results:
        # Nothing enabled: that is a config problem, not a delivery failure, so
        # do not queue it. doctor reports enabled-but-unconfigured channels.
        logger.warning("no enabled channel configured")

    delivered = any(ok for _, ok, _ in results)
    if uncertain or delivered:
        state.mark(notification.dedupe_key)
        state.save()
    elif results and queue_failure:
        # Every enabled channel failed. Keep the notification so a dead proxy
        # does not silently swallow it; `notifyhub flush` replays it later.
        try:
            outbox = outbox_for(config, logger)
            entry = outbox.add(notification, results)
            if entry is not None:
                logger.warning(
                    "queued for retry (outbox id=%s, %d pending): %s",
                    entry["id"],
                    len(outbox),
                    entry["last_error"],
                )
        except Exception as error:  # never let the outbox break the hook
            logger.error("failed to queue notification: %s", error)

    return results


def replay_outbox(config, logger, limit=None):
    path = resolve_path(config.get("state_path", "~/.local/state/notifyhub/state.json"))
    with state_lock(path):
        return _replay_outbox_locked(config, logger, limit)


def _replay_outbox_locked(config, logger, limit=None):
    """Retry queued notifications. Returns (delivered, failed, remaining)."""
    outbox = outbox_for(config, logger)
    pending = outbox.pending()
    if limit is not None:
        pending = pending[:limit]

    delivered = 0
    failed = 0
    for entry in pending:
        try:
            notification = notification_from_dict(entry.get("notification") or {})
        except (TypeError, ValueError) as error:
            logger.error("outbox entry %s is unreadable, dropping: %s", entry.get("id"), error)
            outbox.remove(entry.get("id"))
            continue

        state_path = resolve_path(config.get("state_path", "~/.local/state/notifyhub/state.json"))
        if StateStore(state_path).is_seen(notification.dedupe_key):
            outbox.remove(entry.get("id"))
            logger.info("outbox entry already handled; removed without resending")
            continue
        channels = entry.get("channels") or []
        results = _dispatch_locked(
            notification,
            config,
            logger,
            force=True,
            queue_failure=False,
            only_channel=channels[0] if len(channels) == 1 else None,
        )
        if results and any(ok for _, ok, _ in results):
            outbox.remove(entry.get("id"))
            delivered += 1
            logger.info("outbox replay delivered: %s", notification.status_text)
        elif StateStore(state_path).is_seen(notification.dedupe_key):
            outbox.remove(entry.get("id"))
            failed += 1
            logger.warning("outbox delivery outcome unknown; removed from automatic replay")
        else:
            detail = "; ".join(d for _, _, d in results) or "no channel available"
            updated = outbox.record_failure(entry.get("id"), detail)
            failed += 1
            logger.warning(
                "outbox replay failed (attempt %s): %s",
                (updated or {}).get("attempts", "?"),
                detail,
            )

    return delivered, failed, len(outbox)
