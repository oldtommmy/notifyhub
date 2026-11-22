"""Persistent outbox for notifications that could not be delivered.

A notification is written here only after every enabled channel failed. The
next hook run, `notifyhub flush`, or the GUI replays it. Storing the rendered
payload would freeze the layout at failure time, so we keep the notification
fields and re-render on replay with whatever config is current.
"""

import json
import os
import time
from dataclasses import asdict, fields
from pathlib import Path

from .config import resolve_path
from .events import Notification

# Feishu error codes that will never succeed on retry: bad credentials,
# missing permission, invalid receiver, revoked/blocked bot. Replaying these
# just burns the retry budget and hides the real cause in the log.
PERMANENT_CODES = {
    "99991663",  # wrong app_id / app_secret
    "99991672",  # missing app permission scope
    "99991661",  # tenant_access_token invalid
    "230001",    # invalid receive_id
    "230002",    # bot not in chat
    "230006",    # bot ability not enabled
}

PERMANENT_HINTS = (
    "is not configured",
    "unsupported receive_id_type",
    "receive_id is not configured",
    "app_id / app_secret is not configured",
)


def is_permanent_error(message):
    """True when retrying this failure cannot possibly help."""
    text = str(message or "")
    for code in PERMANENT_CODES:
        if f"code={code}" in text:
            return True
    lower = text.lower()
    return any(hint in lower for hint in PERMANENT_HINTS)


def notification_to_dict(notification):
    data = {field.name: getattr(notification, field.name) for field in fields(notification)}
    return data


def notification_from_dict(data):
    known = {field.name for field in fields(Notification)}
    return Notification(**{key: value for key, value in data.items() if key in known})


class Outbox:
    def __init__(self, settings, logger=None):
        settings = settings or {}
        self.enabled = bool(settings.get("enabled", True))
        self.path = resolve_path(settings.get("path") or "~/.local/state/notifyhub/outbox.json")
        self.max_items = int(settings.get("max_items", 200))
        self.max_age_hours = float(settings.get("max_age_hours", 72))
        self.max_attempts = int(settings.get("max_attempts", 8))
        self.logger = logger
        self._entries = []
        self._load()

    # ---------------------------------------------------------------- storage

    def _load(self):
        try:
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            self._entries = []
            return
        entries = data.get("entries") if isinstance(data, dict) else None
        self._entries = [entry for entry in (entries or []) if isinstance(entry, dict)]

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(
                json.dumps({"entries": self._entries}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as error:
            if self.logger:
                self.logger.warning("cannot write outbox %s: %s", self.path, error)

    # ------------------------------------------------------------------- api

    def __len__(self):
        return len(self._entries)

    def entries(self):
        return list(self._entries)

    @property
    def path_str(self):
        return str(self.path)

    def add(self, notification, results, now=None):
        """Persist a notification whose delivery failed on every channel."""
        if not self.enabled:
            return None

        now = time.time() if now is None else now
        failures = [(name, detail) for name, ok, detail in results if not ok]
        permanent = all(is_permanent_error(detail) for _, detail in failures) if failures else False

        # Keep one entry per dedupe key so a retry storm cannot pile up copies
        # of the same notification.
        if notification.dedupe_key:
            self._entries = [
                entry
                for entry in self._entries
                if entry.get("notification", {}).get("dedupe_key") != notification.dedupe_key
            ]

        entry = {
            "id": f"{int(now * 1000)}-{len(self._entries)}",
            "created_at": now,
            "attempts": 0,
            "next_attempt_at": now,
            "dead": permanent,
            "last_error": "; ".join(f"{name}: {detail}" for name, detail in failures),
            "channels": [name for name, _ in failures],
            "notification": notification_to_dict(notification),
        }
        self._entries.append(entry)
        self._prune(now)
        self._save()

        if permanent and self.logger:
            self.logger.warning(
                "outbox: %s marked dead (permanent error, will not retry): %s",
                notification.dedupe_key or entry["id"],
                entry["last_error"],
            )
        return entry

    def pending(self, now=None):
        """Entries that are due for a replay attempt."""
        now = time.time() if now is None else now
        return [
            entry
            for entry in self._entries
            if not entry.get("dead") and float(entry.get("next_attempt_at", 0)) <= now
        ]

    def due_count(self, now=None):
        return len(self.pending(now))

    def stats(self, now=None):
        now = time.time() if now is None else now
        dead = sum(1 for entry in self._entries if entry.get("dead"))
        return {
            "count": len(self._entries),
            "due": self.due_count(now),
            "dead": dead,
            "oldest_age_hours": (
                round((now - min(float(e.get("created_at", now)) for e in self._entries)) / 3600, 1)
                if self._entries
                else 0
            ),
        }

    def remove(self, entry_id):
        before = len(self._entries)
        self._entries = [entry for entry in self._entries if entry.get("id") != entry_id]
        if len(self._entries) != before:
            self._save()
            return True
        return False

    def record_failure(self, entry_id, error, now=None):
        """Bump the attempt counter and schedule the next try with backoff."""
        now = time.time() if now is None else now
        for entry in self._entries:
            if entry.get("id") != entry_id:
                continue
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            entry["last_error"] = str(error)
            if is_permanent_error(error) or entry["attempts"] >= self.max_attempts:
                entry["dead"] = True
            else:
                entry["next_attempt_at"] = now + backoff_seconds(entry["attempts"])
            self._save()
            return entry
        return None

    def clear(self):
        count = len(self._entries)
        self._entries = []
        self._save()
        return count

    def revive_dead(self, now=None):
        """Put dead entries back in the queue, e.g. after fixing permissions."""
        now = time.time() if now is None else now
        revived = 0
        for entry in self._entries:
            if entry.get("dead"):
                entry["dead"] = False
                entry["attempts"] = 0
                entry["next_attempt_at"] = now
                revived += 1
        if revived:
            self._save()
        return revived

    def _prune(self, now):
        cutoff = now - self.max_age_hours * 3600
        kept = []
        for entry in self._entries:
            if float(entry.get("created_at", 0)) < cutoff:
                if self.logger:
                    self.logger.warning(
                        "outbox: dropping entry older than %sh: %s",
                        self.max_age_hours,
                        entry.get("last_error", ""),
                    )
                continue
            kept.append(entry)
        self._entries = kept[-self.max_items :] if self.max_items else kept


def backoff_seconds(attempts):
    """Exponential backoff with a ceiling: 60s, 2m, 4m, ... capped at 1h."""
    return min(60 * (2 ** max(0, attempts - 1)), 3600)


def outbox_for(config, logger=None):
    return Outbox(config.get("outbox") or {}, logger)