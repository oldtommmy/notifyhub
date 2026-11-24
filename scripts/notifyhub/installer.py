import json
import os
import shutil
import stat
import time
from pathlib import Path

TARGETS = ("codex", "claude", "commandcode", "antigravity")


def target_path(target, home=None):
    """Return the settings file that holds hooks for an agent target."""
    base = Path(home).expanduser() if home else Path.home()
    if target == "codex":
        if home is None:
            base = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        else:
            base = base / ".codex"
        return base / "hooks.json"
    if target == "claude":
        return base / ".claude" / "settings.json"
    if target == "commandcode":
        return base / ".commandcode" / "settings.json"
    if target == "antigravity":
        return base / ".gemini" / "config" / "hooks.json"
    raise ValueError(f"unknown target: {target}")


def _is_ours(handler, source="codex"):
    command = str(handler.get("command", ""))
    return "notifyhub" in command and f"hook {source}" in command


def _read(path):
    if not path.exists():
        return {"hooks": {}}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("hooks file must contain an object")
    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    return data


def _read_antigravity(path):
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Antigravity hooks file must contain an object")
    return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temp, mode)
    except OSError:
        pass
    os.replace(temp, path)


def _backup(path):
    if not path.exists():
        return None
    backup = path.with_name(path.name + f".bak-{int(time.time())}")
    shutil.copy2(path, backup)
    return backup


def install_hooks(path, events, command, source="codex"):
    path = Path(path).expanduser()
    if source == "antigravity":
        if set(events) != {"Stop"}:
            raise ValueError("Antigravity NotifyHub 只支持 Stop 事件")
        data = _read_antigravity(path)
        group = data.setdefault("notifyhub-antigravity", {})
        if not isinstance(group, dict):
            raise ValueError("notifyhub-antigravity hook group must contain an object")
        backup = _backup(path)
        group.update(enabled=True, Stop=[{"type": "command", "command": command, "timeout": 20}])
        _write(path, data)
        return {"path": path, "backup": backup, "events": ["Stop"], "command": command, "target": source}
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
                if isinstance(handler, dict) and _is_ours(handler, source):
                    handler.update({"type": "command", "command": command, "timeout": 20})
                    found = True
            if found:
                break

        if not found:
            groups.append({"hooks": [{"type": "command", "command": command, "timeout": 20}]})

    _write(path, data)
    return {
        "path": path,
        "backup": backup,
        "events": list(events),
        "command": command,
        "target": source,
    }


def uninstall_hooks(path, events=None, source="codex"):
    path = Path(path).expanduser()
    if not path.exists():
        return {"path": path, "backup": None, "removed": 0, "target": source}
    if source == "antigravity":
        data = _read_antigravity(path)
        group = data.get("notifyhub-antigravity")
        handlers = group.get("Stop", []) if isinstance(group, dict) else []
        if not isinstance(handlers, list) or (events is not None and "Stop" not in events):
            handlers = []
        kept = [item for item in handlers if not isinstance(item, dict) or not _is_ours(item, source)]
        removed = len(handlers) - len(kept)
        backup = None
        if removed:
            backup = _backup(path)
            if kept:
                group["Stop"] = kept
            else:
                group.pop("Stop")
                if set(group) <= {"enabled"}:
                    data.pop("notifyhub-antigravity")
            _write(path, data)
        return {"path": path, "backup": backup, "removed": removed, "target": source}

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
            kept_handlers = [
                handler
                for handler in handlers
                if not (isinstance(handler, dict) and _is_ours(handler, source))
            ]
            removed += len(handlers) - len(kept_handlers)
            if kept_handlers:
                group["hooks"] = kept_handlers
                kept_groups.append(group)

        if kept_groups:
            data["hooks"][event] = kept_groups
        else:
            data["hooks"].pop(event, None)

    _write(path, data)
    return {"path": path, "backup": backup, "removed": removed, "target": source}


def has_hooks(path, events=None, source="codex"):
    path = Path(path).expanduser()
    if not path.exists():
        return False
    if source == "antigravity":
        try:
            group = _read_antigravity(path).get("notifyhub-antigravity")
        except (OSError, ValueError):
            return False
        handlers = group.get("Stop") if isinstance(group, dict) and group.get("enabled", True) else None
        return isinstance(handlers, list) and any(isinstance(item, dict) and _is_ours(item, source) for item in handlers)
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
                if isinstance(handler, dict) and _is_ours(handler, source):
                    return True
    return False


def install_target(target, events, command, home=None):
    return install_hooks(target_path(target, home=home), events, command, source=target)


def uninstall_target(target, events=None, home=None):
    return uninstall_hooks(target_path(target, home=home), events, source=target)


def has_target_hooks(target, events=None, home=None):
    return has_hooks(target_path(target, home=home), events, source=target)


# Backwards-compatible Codex helpers used by the GUI and existing tests.


def install_codex(codex_home, events, command):
    return install_hooks(Path(codex_home).expanduser() / "hooks.json", events, command, "codex")


def uninstall_codex(codex_home, events=None):
    return uninstall_hooks(Path(codex_home).expanduser() / "hooks.json", events, "codex")


def has_notifyhub_hook(codex_home, events=None):
    return has_hooks(Path(codex_home).expanduser() / "hooks.json", events, "codex")
