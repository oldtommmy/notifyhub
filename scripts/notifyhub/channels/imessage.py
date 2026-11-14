import subprocess

from ..render import render_text


class IMessageError(RuntimeError):
    pass


SCRIPT = """
on run argv
    set targetHandle to item 1 of argv
    set targetText to item 2 of argv
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy targetHandle of targetService
        send targetText to targetBuddy
    end tell
end run
"""


def send(notification, config, logger=None):
    handle = config.get("to", "")
    if not handle:
        raise IMessageError("imessage.to is not configured")

    text = render_text(notification)
    limit = int(config.get("max_chars", 3000))
    if len(text) > limit:
        text = text[: limit - 1] + "…"

    try:
        result = subprocess.run(
            ["osascript", "-e", SCRIPT, "--", handle, text],
            capture_output=True,
            text=True,
            timeout=float(config.get("timeout_seconds", 15)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise IMessageError(str(error)) from error

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise IMessageError(detail[:300] or f"osascript exited {result.returncode}")

    return result.stdout.strip()
