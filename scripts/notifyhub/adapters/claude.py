from .hooks import parse_hook


def parse(payload, config):
    settings = config.get("claude", {})
    if not settings.get("enabled", True):
        return None
    return parse_hook(payload, settings, "claude", "Claude Code")