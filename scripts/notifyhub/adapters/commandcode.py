import json
from pathlib import Path

from .hooks import parse_hook


def _config_model():
    try:
        data = json.loads(
            (Path.home() / ".commandcode" / "config.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    return str(data.get("model") or "")


def parse(payload, config):
    settings = config.get("commandcode", {})
    if not settings.get("enabled", True):
        return None
    return parse_hook(
        payload,
        settings,
        "commandcode",
        "Command Code",
        model_fallback=_config_model(),
    )