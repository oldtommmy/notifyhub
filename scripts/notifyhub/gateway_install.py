"""Install the independent macOS gateway with the existing verified bot settings."""
import importlib.util
import os
from pathlib import Path
import re
import sys

from . import installer, service
from .config import load_config, save_config
from .runtime import launcher_path
from .state import state_lock

ENGINES = ("dsh", "codex", "antigravity")
LABEL = "com.notifyhub.gateway"


def gateway_path(config_file):
    return Path(config_file).expanduser().resolve().with_name("gateway.json")


def save_options(config_file, options):
    target = gateway_path(config_file)
    ids = options.get("allow_open_ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) or not re.fullmatch(r"ou_[A-Za-z0-9]+", item)
                                        for item in ids):
        raise ValueError("请填写核实过的 open_id，多个 ID 用逗号分隔。")
    enabled = options.get("enabled") is True
    engines = {name + "_enabled": options.get(name + "_enabled") is True for name in ENGINES}
    if enabled and (not ids or not any(engines.values())):
        raise ValueError("启用飞书续聊前，请填写白名单并选择至少一个 agent。")

    with state_lock(target):
        source = target if target.exists() else config_file
        config = load_config(source, expand=False)
        copy_app = target.exists() and options.get("use_notification_bot") is True
        if copy_app:
            config["channels"]["feishu_app"] = load_config(config_file, expand=False)["channels"]["feishu_app"]
        app = load_config(config_file if copy_app else source)["channels"]["feishu_app"]
        if enabled and not (app.get("app_id") and app.get("app_secret")):
            raise ValueError("请先保存飞书 App ID 和 App Secret。")
        bridge = config["bridge"]
        bridge.update(engines, enabled=enabled, allow_open_ids=list(dict.fromkeys(ids)),
                      card_actions=options.get("card_actions", bridge["card_actions"]) is True)
        installer._backup(target)
        save_config(config, target)
    return target


def install_gateway(config_file):
    if sys.platform != 'darwin':
        raise RuntimeError('当前自动常驻安装支持 macOS；其他 Unix 系统可前台运行 gateway。')
    target = Path(config_file).expanduser().resolve()
    cfg = load_config(str(target))
    bridge = cfg.get('bridge', {})
    app = cfg.get('channels', {}).get('feishu_app', {})
    if not bridge.get('enabled') or not bridge.get('allow_open_ids') or not app.get('app_id') or not app.get('app_secret'):
        raise ValueError('先配置飞书凭据、启用 bridge 并填写经过核实的 open_id 白名单。')
    if importlib.util.find_spec("lark_oapi") is None:
        raise RuntimeError('当前 Python 缺少飞书 SDK，请安装 lark-oapi>=1.8,<2 后再启动网关。')
    state = Path.home() / '.local/state/notifyhub'
    state.mkdir(parents=True, exist_ok=True)
    plist = {
        'Label': LABEL,
        'ProgramArguments': [sys.executable, str(launcher_path()), '--config', str(target), 'gateway'],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 5,
        'StandardOutPath': str(state / 'gateway.log'),
        'StandardErrorPath': str(state / 'gateway.log'),
        'EnvironmentVariables': {'PATH': os.environ.get('PATH', os.defpath), 'PYTHONDONTWRITEBYTECODE': '1'},
    }
    try:
        service.install(plist)
    except SystemExit as error:
        raise RuntimeError(str(error)) from error
    return 0


def stop_gateway():
    if service.is_loaded(LABEL):
        result = service._run(["launchctl", "unload", str(service.plist_path(LABEL))])
        if result.returncode:
            raise RuntimeError("网关停止失败，请在终端检查 launchd 状态。")
