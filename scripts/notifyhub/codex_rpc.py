"""Local Codex control-protocol client; never resumes a competing writer."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import uuid


class CodexRpcError(RuntimeError):
    pass


class CodexRpcRejected(CodexRpcError):
    """The server returned an explicit rejection."""


class CodexRpc:
    def __init__(self, command=None, timeout=15):
        binary = os.environ.get('NOTIFYHUB_CODEX_BIN') or shutil.which('codex')
        if not binary and sys.platform == 'darwin':
            for path in map(Path, ('/opt/homebrew/bin/codex', '/usr/local/bin/codex')):
                if path.is_file() and os.access(path, os.X_OK):
                    binary = str(path)
                    break
        if not command and not binary:
            raise CodexRpcError('找不到 Codex CLI，请先安装并登录 Codex。')
        self.command = command or [binary, 'app-server', '--listen', 'stdio://']
        self.timeout = timeout
        self.process = None
        self.pending = {}
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()

    def start(self):
        if self.process is not None:
            if self.process.poll() is not None:
                raise CodexRpcError('Codex 连接已断开，请重启桥接；提交结果不明的消息不会重跑。')
            return
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        self.call('initialize', {'clientInfo': {'name': 'notifyhub_feishu', 'title': 'notifyhub', 'version': '0.9.2'},
                                 'capabilities': {'experimentalApi': True}})
        self._send({'method': 'initialized', 'params': {}})

    def _send(self, frame):
        with self.send_lock:
            self.process.stdin.write(json.dumps(frame, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if 'id' in frame and ('result' in frame or 'error' in frame):
                    with self.lock:
                        waiter = self.pending.get(frame['id'])
                    if waiter is not None:
                        waiter.put(frame)
                elif 'id' in frame and 'method' in frame:
                    # This client submits queue input only. It must not grant
                    # permissions or silently answer an interactive approval.
                    self._send({'id': frame['id'], 'error': {'code': -32601, 'message': 'Handle approvals in the owning Codex UI'}})
        finally:
            with self.lock:
                waiters = list(self.pending.values())
            for waiter in waiters:
                waiter.put({'disconnected': True})

    def call(self, method, params):
        if self.process is None and method != 'initialize':
            self.start()
        identity = uuid.uuid4().hex
        waiter = queue.Queue()
        with self.lock:
            self.pending[identity] = waiter
        try:
            self._send({'id': identity, 'method': method, 'params': params})
            frame = waiter.get(timeout=self.timeout)
            if frame.get('disconnected'):
                raise CodexRpcError('Codex 本机控制连接已关闭')
            if frame.get('error'):
                raise CodexRpcRejected(str(frame['error'].get('message') or frame['error'])[:700])
            return frame.get('result') or {}
        except (OSError, queue.Empty) as error:
            raise CodexRpcError('Codex 请求超时或连接中断，结果可能不明确。') from error
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
