import json
import os
import shutil
import subprocess
from pathlib import Path

from .runtime import format_command as _format_command

PROFILES = ("desktop", "web")


def find_plugin():
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "integrations" / "dsh" / "package.json"
        if candidate.exists():
            return candidate.parent
    return None


def dsh_binary():
    return shutil.which("dsh")


def desktop_app_installed():
    candidates = [
        Path("/Applications/DeepSeek Harness.app"),
        Path.home() / "Applications" / "DeepSeek Harness.app",
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "DeepSeek Harness")
    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.append(Path(program_files) / "DeepSeek Harness")
    return any(candidate.exists() for candidate in candidates)


def build_command(profile="web"):
    plugin = find_plugin()
    if plugin is None:
        raise FileNotFoundError("integrations/dsh not found")
    dsh = dsh_binary() or "dsh"
    link_path = plugin.as_posix() if os.name == "nt" else str(plugin)
    return [dsh, "plugin", "--profile", profile, "add", f"link:{link_path}"]


def format_command(command, windows=None):
    return _format_command(command, windows=windows)


def profile_installed(profile):
    home = Path(os.environ.get("DSH_HOME", "~/.dsh")).expanduser()
    path = home / "profiles" / profile / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    dependencies = data.get("dependencies") or {}
    return any("dsh-notifyhub" in key for key in dependencies)


def status():
    plugin = find_plugin()
    profiles = {profile: profile_installed(profile) for profile in PROFILES}
    installed = [profile for profile, ok in profiles.items() if ok]
    return {
        "dsh_found": bool(dsh_binary()),
        "plugin_available": plugin is not None,
        "profiles": profiles,
        "installed_profiles": installed,
        "profile_installed": bool(installed),
        "desktop_app": desktop_app_installed(),
        "plugin_path": str(plugin or ""),
    }


def install(profile="web"):
    if not profile or "/" in profile or "\\" in profile:
        return {"ok": False, "error": f"未知 profile：{profile}", "command": []}
    if not dsh_binary():
        return {
            "ok": False,
            "error": "未找到 dsh。先安装 DeepSeek Harness：npx @deepseek-ai/dsh web",
            "command": [],
        }
    command = build_command(profile)
    result = subprocess.run(command, capture_output=True, text=True)
    error = ""
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "安装失败").strip()[-500:]
    return {
        "ok": result.returncode == 0,
        "error": error,
        "command": command,
        "stdout": (result.stdout or "")[-2000:],
        "stderr": (result.stderr or "")[-2000:],
    }
