import os
import shlex
import subprocess
import sys
from pathlib import Path


def launcher_path():
    return Path(__file__).resolve().parent.parent / "notifyhub.py"


def format_command(parts, windows=None):
    if windows is None:
        windows = os.name == "nt"
    values = [str(part) for part in parts]
    if windows:
        return subprocess.list2cmdline(values)
    return " ".join(shlex.quote(part) for part in values)


def hook_command(config_path, source="codex"):
    return format_command(
        [
            sys.executable,
            str(launcher_path()),
            "--config",
            str(config_path),
            "hook",
            source,
        ]
    )
