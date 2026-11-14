import shlex
import sys
from pathlib import Path


def launcher_path():
    return Path(__file__).resolve().parent.parent / "notifyhub.py"


def hook_command(config_path):
    return " ".join(
        [
            shlex.quote(sys.executable),
            shlex.quote(str(launcher_path())),
            "--config",
            shlex.quote(str(config_path)),
            "hook",
            "codex",
        ]
    )
