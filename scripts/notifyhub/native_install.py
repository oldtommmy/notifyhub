"""Install native continuation from the local GUI into a stable desktop runtime."""
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from . import dsh_install
from .config import load_config
from .state import state_lock


def native_config_path(config_file):
    path = Path(config_file)
    return path.with_name('native-bridge.json' if path.name == 'config.json' else path.stem + '.native.json')


def runtime_path():
    return Path.home() / '.local/share/notifyhub/native-bridge'


def _python(runtime):
    return runtime / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _patch_profile(text, command, enabled, plugin_id="notifyhub-native-bridge"):
    first = next((line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')), '')
    if first and not first.startswith('- '):
        raise ValueError('desktop 的 cordis.patch.yml 不是块式列表，请先按原生插件说明整理配置。')
    chunks = re.split(r'(?m)(?=^- )', text)
    match = re.compile(r'''(?m)^(?:- |  )id:\s*["']?''' + re.escape(plugin_id) + r'''["']?\s*(?:#.*)?$''')
    existing = [i for i, chunk in enumerate(chunks) if match.search(chunk)]
    if len(existing) > 1:
        raise ValueError('desktop 存在重复的原生续聊配置，请先清理重复项。')
    extra = ''
    if existing:
        # Preserve the one additional supported plugin option, including its YAML quoting.
        found = re.search(r'(?m)^    state_path:.*$', chunks[existing[0]])
        if found:
            extra = found.group(0) + '\n'
    block = '- id: ' + plugin_id + '\n  config:\n    enabled: ' + str(enabled).lower() + '\n    command: ' + json.dumps(command, ensure_ascii=False) + '\n' + extra
    if existing:
        chunks[existing[0]] = block
        return ''.join(chunks)
    return text.rstrip() + '\n\n' + block


def status(config_file):
    target = native_config_path(config_file)
    cfg = load_config(str(target)) if target.exists() else {}
    bridge = cfg.get('bridge') or {}
    home = Path(os.environ.get('DSH_HOME', '~/.dsh')).expanduser()
    try:
        package = json.loads((home / 'profiles/desktop/package.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        package = {}
    return {'installed': 'dsh-notifyhub-native-bridge' in package.get('dependencies', {}),
            'enabled': bool(bridge.get('enabled')),
            'dsh_enabled': bool(bridge.get('enabled') and bridge.get('dsh_enabled', True)),
            'allow_open_ids': bridge.get('allow_open_ids', []),
            'card_actions': bool(bridge.get('card_actions')), 'codex_enabled': bool(bridge.get('enabled') and bridge.get('codex_enabled')), 'config_path': str(target),
            'runtime_ready': _python(runtime_path()).is_file(),
            'restart_required': True}


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.install-' + uuid.uuid4().hex)
    fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(content)
    os.replace(temp, path)


def _run(command, timeout=180):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('安装命令失败：' + (result.stderr or result.stdout or '未知错误')[-600:])
    return result


def install(config_file, options, runtime=None, dsh_home=None):
    if options.get('profile', 'desktop') != 'desktop':
        return {'ok': False, 'error': '原生续聊当前仅支持 dsh 桌面版。'}
    ids = options.get('allow_open_ids', [])
    if not isinstance(ids, list) or any(not isinstance(x, str) or not re.fullmatch(r'ou_[A-Za-z0-9]+', x) for x in ids):
        return {'ok': False, 'error': '请填写经过核实的 open_id，多个 ID 用逗号分隔。'}
    if 'dsh_enabled' in options:
        dsh_enabled = options.get('dsh_enabled') is True
        codex_enabled = options.get('codex_enabled') is True
    else:
        # Older clients sent enabled as the shared bridge switch.
        dsh_enabled = options.get('enabled') is True
        codex_enabled = dsh_enabled and options.get('codex_enabled') is True
    enabled = dsh_enabled or codex_enabled
    cards = options.get('card_actions') is True
    if enabled and not ids:
        return {'ok': False, 'error': '启用续聊前请填写允许使用者的 open_id。'}
    source_config = load_config(config_file)
    app = source_config['channels']['feishu_app']
    if not app.get('app_id') or not app.get('app_secret') or str(app['app_secret']).startswith('${'):
        return {'ok': False, 'error': '请先在 GUI 保存有效的飞书 App ID 和 App Secret。'}
    binary = dsh_install.dsh_binary('desktop')
    source = dsh_install.find_plugin()
    if not binary or not source:
        return {'ok': False, 'error': '未找到 dsh CLI 或 notifyhub 插件，请先完成环境检查。'}
    root = source.parents[1]
    if not (root / 'integrations/dsh-native-bridge/package.json').is_file():
        return {'ok': False, 'error': '当前 notifyhub 包缺少原生续聊插件，请更新完整安装包。'}
    runtime = Path(runtime) if runtime else runtime_path()
    home = Path(dsh_home) if dsh_home else Path(os.environ.get('DSH_HOME', '~/.dsh')).expanduser()
    profile = home / 'profiles/desktop/cordis.patch.yml'
    target = native_config_path(config_file)
    command = [str(_python(runtime)), str(runtime / 'scripts/notifyhub.py'), '--config', str(target), 'bridge', 'native-transport']
    try:
        with state_lock(runtime / 'installer'):
            old_patch = profile.read_text(encoding='utf-8') if profile.exists() else ''
            new_patch = _patch_profile(old_patch, command, enabled)
            notify_command = [str(_python(runtime)), str(runtime / 'scripts/notifyhub.py'),
                              '--config', str(Path(config_file)), 'hook', 'dsh']
            new_patch = _patch_profile(new_patch, notify_command, True, plugin_id='dsh-notifyhub')
            backup = runtime.parent / ('native-backup-' + uuid.uuid4().hex[:12])
            backup.mkdir(parents=True, mode=0o700)
            for path, name in [(target, 'native-config.json'), (profile, 'cordis.patch.yml'), (profile.with_name('package.json'), 'package.json')]:
                if path.exists():
                    shutil.copy2(path, backup / name)
            if (runtime / 'scripts').exists():
                shutil.copytree(runtime / 'scripts', backup / 'scripts', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            if (runtime / 'integrations').exists():
                shutil.copytree(runtime / 'integrations', backup / 'integrations', ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
            shutil.copytree(root / 'scripts', runtime / 'scripts', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for name in ('dsh', 'dsh-native-bridge'):
                shutil.copytree(root / 'integrations' / name, runtime / 'integrations' / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
            python = _python(runtime)
            if not python.is_file():
                _run([sys.executable, '-m', 'venv', str(runtime / '.venv')])
            probe = subprocess.run([str(python), '-c', 'import lark_oapi'], capture_output=True, text=True, timeout=15)
            if probe.returncode:
                _run([str(python), '-m', 'pip', 'install', 'lark-oapi>=1.8,<2'])
            _run([binary, 'plugin', '--profile', 'desktop', 'add',
                  'link:' + (runtime / 'integrations/dsh').as_posix(),
                  'link:' + (runtime / 'integrations/dsh-native-bridge').as_posix()])
            if (profile.read_text(encoding='utf-8') if profile.exists() else '') != old_patch:
                raise RuntimeError('安装期间 desktop 配置发生变化，未覆盖新配置；请刷新后重试。')
            native = load_config(str(target)) if target.exists() else source_config
            native['channels']['feishu_app'] = dict(app)
            native.setdefault('bridge', {}).update(enabled=enabled, allow_open_ids=list(dict.fromkeys(ids)), default_agent='dsh', card_actions=cards,
                dsh_enabled=dsh_enabled, codex_enabled=codex_enabled)
            # Keep ledgers and session bindings untouched; neither process is restarted here.
            _write(target, json.dumps(native, ensure_ascii=False, indent=2))
            try:
                _write(profile, new_patch)
            except OSError:
                if (backup / 'native-config.json').exists():
                    shutil.copy2(backup / 'native-config.json', target)
                else:
                    target.unlink(missing_ok=True)
                raise
            return {'ok': True, 'mode': 'native', 'enabled': enabled, 'config_path': str(target),
                    'backup_path': str(backup), 'restart_required': True,
                    'message': '通知与续聊插件已安装。重启 dsh 后生效。' if enabled else '通知与续聊插件已安装，续聊暂未启用。'}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        return {'ok': False, 'error': str(error)}
