import json
import os
import time
from pathlib import Path


def _is_ours(handler):
    command = handler.get("command", "")
    return "notifyhub" in command and "hook codex" in command


def _read(path):
    if not path.exists():
        return {"hooks": {}}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("hooks.json must contain an object")
    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _backup(path):
    if not path.exists():
        return None
    backup = path.with_name(path.name + f".bak-{int(time.time())}")
    backup.write_bytes(path.read_bytes())
    return backup


def install_codex(codex_home, events, command):
    path = Path(codex_home).expanduser() / "hooks.json"
    data = _read(path)
    backup = _backup(path)

    for event in events:
        groups = data["hooks"].setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.{event} must be an array")

        found = False
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.setdefault("hooks", [])
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                if isinstance(handler, dict) and _is_ours(handler):
                    handler.update({"type": "command", "command": command, "timeout": 20})
                    found = True
            if found:
                break

        if not found:
            groups.append({"hooks": [{"type": "command", "command": command, "timeout": 20}]})

    _write(path, data)
    return {"path": path, "backup": backup, "events": list(events), "command": command}


def uninstall_codex(codex_home, events=None):
    path = Path(codex_home).expanduser() / "hooks.json"
    if not path.exists():
        return {"path": path, "backup": None, "removed": 0}

    data = _read(path)
    backup = _backup(path)
    removed = 0
    targets = events if events is not None else list(data["hooks"])

    for event in targets:
        groups = data["hooks"].get(event)
        if not isinstance(groups, list):
            continue

        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept_handlers = [handler for handler in handlers if not (isinstance(handler, dict) and _is_ours(handler))]
            removed += len(handlers) - len(kept_handlers)
            if kept_handlers:
                group["hooks"] = kept_handlers
                kept_groups.append(group)

        if kept_groups:
            data["hooks"][event] = kept_groups
        else:
            data["hooks"].pop(event, None)

    _write(path, data)
    return {"path": path, "backup": backup, "removed": removed}


def has_notifyhub_hook(codex_home, events=None):
    path = Path(codex_home).expanduser() / "hooks.json"
    if not path.exists():
        return False
    try:
        data = _read(path)
    except (OSError, ValueError):
        return False

    targets = events if events is not None else list(data["hooks"])
    for event in targets:
        for group in data["hooks"].get(event, []):
            if not isinstance(group, dict):
                continue
            for handler in group.get("hooks", []):
                if isinstance(handler, dict) and _is_ours(handler):
                    return True
    return False
