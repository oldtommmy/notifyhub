"""Install the Antigravity Stop hook and opt-in desktop sidecar."""
import sys
from pathlib import Path

from . import installer
from .config import ensure_config, load_config
from .runtime import hook_command, launcher_path


def install(config_file, continuation=False):
    config_file = Path(config_file).expanduser().resolve()
    target = config_file.with_name("gateway.json")
    if continuation:
        source = load_config(str(target if target.exists() else config_file))
        if not target.exists():
            source.setdefault("bridge", {}).update(dsh_enabled=False, codex_enabled=False)
        # Import a previously verified allowlist during migration only.
        previous = config_file.with_name("native-bridge.json")
        if not source.get("bridge", {}).get("allow_open_ids") and previous.exists():
            old = load_config(str(previous)).get("bridge", {})
            source.setdefault("bridge", {})["allow_open_ids"] = old.get("allow_open_ids", [])
            if not target.exists():
                source["bridge"]["card_actions"] = old["card_actions"]
        bridge = source.setdefault("bridge", {})
        app = source.get("channels", {}).get("feishu_app", {})
        if not bridge.get("allow_open_ids") or not app.get("app_id") or not app.get("app_secret"):
            print("请先在配置中填写飞书凭据和 bridge.allow_open_ids 白名单。", file=sys.stderr)
            return 1
        bridge.update(enabled=True, antigravity_enabled=True)
        installer._backup(target)
        installer._write(target, source)

    ensure_config(str(config_file))
    if not continuation:
        result = installer.install_target("antigravity", ["Stop"], hook_command(config_file, "antigravity"))
        print(f"Antigravity Stop hook 已安装：{result['path']}")
        return 0
    configure_sidecar(config_file, target)
    print("Antigravity Sidecar 已配置。启动独立 NotifyHub gateway 后发「Antigravity会话」。")
    return 0


def configure_sidecar(config_file, target):
    config_file = Path(config_file).expanduser().resolve()
    target = Path(target).expanduser().resolve()
    installer.install_target("antigravity", ["Stop"], hook_command(config_file, "antigravity"))
    root = installer.target_path("antigravity").parent
    settings_path = root / "config.json"
    settings = installer._read_antigravity(settings_path)
    sidecar = root / "sidecars" / "notifyhub" / "sidecar.json"
    installer._backup(sidecar)
    installer._write(sidecar, {
        "display_name": "NotifyHub Feishu Continuation",
        "command": sys.executable,
        "args": [str(launcher_path()), "--config", str(target), "antigravity-sidecar"],
        "restart_policy": "on-failure",
    })
    raw = installer._read_antigravity(config_file)
    installer._backup(config_file)
    raw.setdefault("antigravity", {})["track_sessions"] = True
    installer._write(config_file, raw)
    installer._backup(settings_path)
    settings.setdefault("sidecars", {}).setdefault("notifyhub", {})["enabled"] = True
    installer._write(settings_path, settings)
