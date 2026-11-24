"""Install the independent macOS gateway with the existing verified bot settings."""
import os
from pathlib import Path
import sys

from . import installer, service
from .config import load_config
from .runtime import launcher_path


def install_gateway(config_file):
    if sys.platform != 'darwin':
        raise RuntimeError('当前自动常驻安装支持 macOS；其他 Unix 系统可前台运行 gateway。')
    target = Path(config_file).expanduser().resolve()
    cfg = load_config(str(target))
    bridge = cfg.get('bridge', {})
    app = cfg.get('channels', {}).get('feishu_app', {})
    if not bridge.get('enabled') or not bridge.get('allow_open_ids') or not app.get('app_id') or not app.get('app_secret'):
        raise ValueError('先配置飞书凭据、启用 bridge 并填写经过核实的 open_id 白名单。')
    state = Path.home() / '.local/state/notifyhub'
    state.mkdir(parents=True, exist_ok=True)
    plist = {
        'Label': 'com.notifyhub.gateway',
        'ProgramArguments': [sys.executable, str(launcher_path()), '--config', str(target), 'gateway'],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 5,
        'StandardOutPath': str(state / 'gateway.log'),
        'StandardErrorPath': str(state / 'gateway.log'),
        'EnvironmentVariables': {'PATH': os.environ.get('PATH', os.defpath), 'PYTHONDONTWRITEBYTECODE': '1'},
    }
    service.install(plist)
    return 0
