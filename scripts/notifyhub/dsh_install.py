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
        if (parent / "lib/native-bridge.js").is_file() and (parent / "package.json").is_file():
            return parent
    return None


def _desktop_cli_candidates():
    for app in (Path("/Applications/DeepSeek Harness.app"),
                Path.home() / "Applications/DeepSeek Harness.app"):
        yield app / "Contents/Resources/runtime/cli/bin/dsh"
    for variable, suffix in (("LOCALAPPDATA", "Programs/DeepSeek Harness"),
                             ("PROGRAMFILES", "DeepSeek Harness")):
        if os.environ.get(variable):
            for name in ("dsh.exe", "dsh.cmd", "dsh.bat"):
                yield Path(os.environ[variable]) / suffix / "resources/runtime/cli/bin" / name


def _executable(path):
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def dsh_binary(profile=None):
    override = os.environ.get("NOTIFYHUB_DSH_BIN")
    if override:
        candidate = Path(os.path.expandvars(override)).expanduser()
        if _executable(candidate):
            return str(candidate)
        found = shutil.which(override)
        if found:
            return found
    bundled = next((str(p) for p in _desktop_cli_candidates() if _executable(p)), None)
    if profile == "desktop" and bundled:
        return bundled
    return shutil.which("dsh") or bundled


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
    dsh = dsh_binary(profile) or "dsh"
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
    return "dsh-notifyhub" in dependencies


def status():
    plugin = find_plugin()
    profiles = {profile: profile_installed(profile) for profile in PROFILES}
    installed = [profile for profile, ok in profiles.items() if ok]
    return {
        "dsh_found": bool(dsh_binary("desktop")),
        "cli_path": dsh_binary("desktop") or "",
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
    if not dsh_binary(profile):
        return {
            "ok": False,
            "error": "未找到可用的 dsh CLI（已检查 PATH 和桌面 App）。可设置 NOTIFYHUB_DSH_BIN 为 CLI 完整路径并重启 GUI；未安装时先准备 Node.js/npm，执行 npm install -g @deepseek-ai/dsh，再用 dsh --version 验证。",
            "command": [],
        }
    command = build_command(profile)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": f"无法执行 dsh 插件安装：{error}", "command": command}
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
