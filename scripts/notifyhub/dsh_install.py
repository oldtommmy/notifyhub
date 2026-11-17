import json
import os
import shutil
import subprocess
from pathlib import Path


def find_plugin():
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "integrations" / "dsh" / "package.json"
        if candidate.exists():
            return candidate.parent
    return None


def dsh_binary():
    return shutil.which("dsh")


def build_command(profile="web"):
    plugin = find_plugin()
    if plugin is None:
        raise FileNotFoundError("integrations/dsh not found")
    dsh = dsh_binary() or "dsh"
    return [dsh, "plugin", "--profile", profile, "add", f"link:{plugin}"]


def profile_installed(profile="web"):
    home = Path(os.environ.get("DSH_HOME", "~/.dsh")).expanduser()
    path = home / "profiles" / profile / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    dependencies = data.get("dependencies") or {}
    return any("dsh-notifyhub" in key for key in dependencies)


def status(profile="web"):
    plugin = find_plugin()
    return {
        "dsh_found": bool(dsh_binary()),
        "plugin_available": plugin is not None,
        "profile_installed": profile_installed(profile),
        "plugin_path": str(plugin or ""),
        "profile": profile,
    }


def install(profile="web"):
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
