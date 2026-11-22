"""`notifyhub bridge service` — run the bridge as a login-agent-managed service.

Foreground `bridge start` dies with whatever started it. On macOS that means
quitting the desktop app kills the bridge too, because both live in the same
process group, so "quit the app and keep chatting from the phone" never worked.
A launchd agent owns the process instead: it starts at login, survives the app
quitting, and restarts if it crashes.

Everything here is macOS-specific (launchd). Other platforms get a clear error
rather than a silently broken install.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.notifyhub.bridge"


def _launch_agents_dir():
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label=LABEL):
    return _launch_agents_dir() / f"{label}.plist"


def _python_for(venv=None, root=None):
    """Prefer the checkout's venv so lark-oapi resolves without a PATH dance."""
    if venv:
        candidate = Path(venv).expanduser() / "bin" / "python"
        if candidate.is_file():
            return str(candidate)
    try:
        import lark_oapi  # noqa: F401

        return sys.executable
    except ImportError:
        pass
    if root:
        candidate = Path(root).expanduser() / ".venv" / "bin" / "python"
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def build_plist(
    *,
    python,
    script,
    config,
    workspace=None,
    log_path=None,
    env=None,
    label=LABEL,
    interval=30,
):
    """Build the launchd agent's property list."""
    script = Path(script)
    # Under launchd the credentials only exist where this plist put them, so
    # name the variables the child must read even when we have no value to
    # freeze in. Values stay out of the plist unless the caller passes them.
    environment = {name: os.environ.get(name, "")
                   for name in ("QWEN_API_KEY", "NOTIFYHUB_FEISHU_APP_SECRET")}
    environment.update(env or {})
    args = [python, str(script), "--config", str(config), "bridge", "start", "--force"]
    return {
        "Label": label,
        "ProgramArguments": args,
        "RunAtLoad": True,
        # Keep it up: a crashed bridge should come back on its own, and
        # quitting the desktop app must not be able to take it down.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": interval,
        "WorkingDirectory": str(workspace or script.parent),
        "StandardOutPath": str(log_path or (Path.home() / ".local/state/notifyhub/bridge.out")),
        "StandardErrorPath": str(log_path or (Path.home() / ".local/state/notifyhub/bridge.out")),
        "EnvironmentVariables": environment,
        "ProcessType": "Background",
    }


def write_plist(plist, path=None, mode=0o600):
    target = Path(path or plist_path(plist.get("Label", LABEL)))
    target.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(plist, sort_keys=False)
    target.write_bytes(data)
    try:
        os.chmod(target, mode)
    except OSError:
        pass
    return target


def _run(command):
    return subprocess.run(command, capture_output=True, text=True)


def is_loaded(label=LABEL):
    """True when launchd currently knows about the agent."""
    if not _is_macos():
        return False
    done = _run(["launchctl", "list", label])
    return done.returncode == 0


def status(label=LABEL):
    """Return (loaded, pid, last_exit_status) from launchctl."""
    if not _is_macos():
        return {"loaded": False, "pid": None, "reason": "只有 macOS 支持 launchd 常驻"}
    done = _run(["launchctl", "list", label])
    if done.returncode != 0:
        return {"loaded": False, "pid": None, "reason": "没有在 launchd 里注册"}
    pid = None
    last = None
    # launchctl emits a plist-ish block; ProgramArguments alone spans many
    # lines, so only take the scalar keys that have exactly two halves.
    for line in (done.stdout or "").splitlines():
        parts = line.split(" = ")
        if len(parts) != 2:
            continue
        key = parts[0].strip().strip('"')
        value = parts[1].strip().rstrip(";").strip().strip('"')
        if key == "PID":
            pid = value
        elif key == "LastExitStatus":
            last = value
    return {"loaded": True, "pid": pid, "last_exit": last, "reason": ""}


def _is_macos():
    return sys.platform == "darwin"


def install(plist, path=None):
    """Write the plist and load it into launchd."""
    if not _is_macos():
        raise SystemExit("bridge service 目前只支持 macOS（launchd）。")
    target = write_plist(plist, path)
    # bootout first: loading twice errors out and leaves the old definition.
    _run(["launchctl", "unload", str(target)])
    done = _run(["launchctl", "load", "-w", str(target)])
    if done.returncode != 0:
        raise SystemExit(f"launchctl load 失败: {(done.stderr or done.stdout).strip()}")
    return target


def uninstall(label=LABEL):
    """Unload and remove the agent. Idempotent."""
    if not _is_macos():
        raise SystemExit("bridge service 目前只支持 macOS（launchd）。")
    target = plist_path(label)
    loaded = _run(["launchctl", "unload", str(target)])
    removed = False
    try:
        target.unlink()
        removed = True
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SystemExit(f"删除 {target} 失败: {error}")
    return {
        "path": target,
        "unloaded": loaded.returncode == 0,
        "removed": removed,
    }


def restart(label=LABEL):
    """Stop and start the loaded agent without touching the plist."""
    if not _is_macos():
        raise SystemExit("bridge service 目前只支持 macOS（launchd）。")
    target = plist_path(label)
    if not target.is_file():
        raise SystemExit(f"没有安装过服务（找不到 {target}），先跑 bridge service install。")
    _run(["launchctl", "unload", str(target)])
    done = _run(["launchctl", "load", "-w", str(target)])
    if done.returncode != 0:
        raise SystemExit(f"launchctl load 失败: {(done.stderr or done.stdout).strip()}")
    return True