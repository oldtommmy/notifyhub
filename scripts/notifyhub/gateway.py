"""Independent Feishu gateway and local dsh adapter connection."""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

from .bridge_cli import _acquire_instance_lock
from .config import load_config
from .log import get_logger


def socket_path(config):
    return Path(config.get('bridge', {}).get('gateway_socket') or '~/.local/state/notifyhub/gateway.sock').expanduser()


class DshConnection:
    def __init__(self):
        self.client = None
        self.lock = threading.Lock()
        self.transport = None

    def emit(self, frame):
        with self.lock:
            client = self.client
            if client is not None:
                try:
                    client.sendall((json.dumps(frame, ensure_ascii=False) + '\n').encode())
                    return
                except OSError:
                    self.client = None
        if frame.get('type') == 'message':
            self.transport.enqueue({'type': 'reply', 'message_id': frame['message_id'],
                                    'text': 'dsh 原会话未连接。请打开 dsh 桌面 App。Codex 和 Antigravity 可独立使用。'})

    def serve(self, path):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            path.unlink(missing_ok=True)
            server.bind(str(path))
            path.chmod(0o600)
            server.listen(1)
            while True:
                client, _ = server.accept()
                with self.lock:
                    self.client = client
                try:
                    with client, client.makefile('r', encoding='utf-8') as stream:
                        for line in stream:
                            frame = json.loads(line)
                            if frame.get('type') == 'reply':
                                self.transport.enqueue(frame)
                except (OSError, ValueError):
                    pass
                finally:
                    with self.lock:
                        if self.client is client:
                            self.client = None


def run_gateway(config_file):
    from .native_transport import NativeTransport
    config = load_config(str(config_file))
    settings = config.get('bridge', {})
    if not settings.get('enabled') or not settings.get('allow_open_ids'):
        raise SystemExit('网关需要启用 bridge 并设置 open_id 白名单。')
    if os.name == 'nt':
        raise SystemExit('独立网关的本地连接目前支持 macOS/Linux。')
    instance_lock = _acquire_instance_lock(settings)
    connection = DshConnection()
    transport = NativeTransport(config, get_logger(config.get('log_path', ''), False), connection.emit)
    connection.transport = transport
    path = socket_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=connection.serve, args=(path,), daemon=True).start()
    transport.worker.start()
    transport.listener.start()
    print('NotifyHub gateway started', flush=True)
    try:
        while True:
            time.sleep(10)
            if not transport.listener._thread.is_alive() or not transport.worker.is_alive():
                transport.logger.error('gateway worker stopped; exiting for service restart')
                return 1
            # The Feishu connection receives periodic heartbeat frames even
            # when no user is chatting. A live process alone is insufficient.
            if time.monotonic() - transport.listener.last_frame_at > 300:
                transport.logger.error('gateway received no Feishu frames for 300 seconds; exiting for service restart')
                return 1
    finally:
        transport.codex.close()
        instance_lock.close()
        path.unlink(missing_ok=True)


def run_dsh_client(config):
    """Adapt the existing dsh controller's stdio to the independent gateway."""
    print('NHBRIDGE:' + json.dumps({'type': 'ready'}), flush=True)
    line = sys.stdin.readline()
    if not line or json.loads(line).get('type') != 'ready':
        return 1
    connection = DshConnection()

    def forward_host():
        for line in sys.stdin:
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            # Replies are never replayed after an uncertain disconnection.
            with connection.lock:
                client = connection.client
                if client is not None:
                    try:
                        client.sendall((json.dumps(frame, ensure_ascii=False) + '\n').encode())
                    except OSError:
                        connection.client = None
        os._exit(0)

    threading.Thread(target=forward_host, daemon=True).start()
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(socket_path(config)))
                with connection.lock:
                    connection.client = client
                with client.makefile('r', encoding='utf-8') as stream:
                    for line in stream:
                        frame = json.loads(line)
                        print('NHBRIDGE:' + json.dumps(frame, ensure_ascii=False), flush=True)
        except (OSError, ValueError):
            pass
        finally:
            with connection.lock:
                connection.client = None
        time.sleep(2)
