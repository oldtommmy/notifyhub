"""Feishu I/O sidecar. Agent execution stays inside the owning dsh process."""
import json
import queue
import sys
import threading

from .bridge_cli import _acquire_instance_lock
from .channels import feishu_app
from .config import load_config
from .feishu_listener import FeishuListener
from .log import get_logger


class NativeTransport:
    def __init__(self, config, logger, emit):
        self.config, self.logger, self.emit = config, logger, emit
        self.targets = {}
        self.listener = FeishuListener(config, self.inbound, logger, reply=self.refusal)
        self.outbound = queue.Queue(maxsize=256)
        self.worker = threading.Thread(target=self._send_loop, daemon=True)

    def inbound(self, **message):
        # Only messages that passed the listener's allowlist reach this point.
        if message.get('kind') == 'agent':
            message['kind'] = 'task'
        self.targets[message['message_id']] = message['chat_id']
        self.emit({'type': 'message', **message})

    def refusal(self, message_id, text):
        # Network calls never run on the SDK receive loop.
        self.enqueue({'type': 'reply', 'message_id': message_id, 'text': text, 'key': 'refusal'})

    def enqueue(self, response):
        if response.get('type') != 'reply':
            return
        if response.get('key') != 'refusal' and response.get('message_id') not in self.targets:
            self.logger.warning('native transport rejected reply to an unknown message')
            return
        try:
            self.outbound.put_nowait(response)
        except queue.Full:
            self.logger.error('native transport outbound queue full; response not sent')
            self.receipt(response, 'not_sent')

    def receipt(self, response, status):
        self.emit({'type': 'delivery', 'request_id': response.get('request_id'),
                   'key': response.get('key'), 'status': status})

    def _send_loop(self):
        while True:
            response = self.outbound.get()
            try:
                app = self.config['channels']['feishu_app']
                text = str(response.get('text', ''))
                limit = int(self.config.get('bridge', {}).get('max_message_chars', 3000))
                if len(text) > limit:
                    text = text[:limit] + '\n（内容截断，完整结果见 dsh 原会话）'
                feishu_app.reply_text(response['message_id'], text, app,
                                      dedupe_key=response.get('key', 'reply'))
            except Exception as error:
                # No blind retries or legacy outbox: a timeout may mean delivered.
                self.logger.warning('native reply outcome unconfirmed: %s', error)
                self.receipt(response, 'unknown')
            else:
                self.receipt(response, 'delivered')


def cmd_native_transport(args):
    config = load_config(args.config)
    settings = config.get('bridge') or {}
    if not settings.get('enabled') or not settings.get('allow_open_ids'):
        raise SystemExit('native bridge requires bridge.enabled=true and a nonempty allow_open_ids')
    logger = get_logger(config.get('log_path', ''), getattr(args, 'verbose', False))
    lock = _acquire_instance_lock(settings)
    stdout = sys.stdout
    write_lock = threading.Lock()
    def emit(value):
        with write_lock:
            stdout.write('NHBRIDGE:' + json.dumps(value, ensure_ascii=False) + '\n')
            stdout.flush()
    transport = NativeTransport(config, logger, emit)
    emit({'type': 'ready'})
    # Do not subscribe until the host has loaded its durable ledger.
    handshake = sys.stdin.readline()
    if not handshake or json.loads(handshake).get('type') != 'ready':
        return 1
    transport.worker.start()
    transport.listener.start()
    try:
        for line in sys.stdin:
            try:
                transport.enqueue(json.loads(line))
            except (ValueError, TypeError) as error:
                logger.warning('invalid native bridge frame: %s', error)
    finally:
        lock.close()
    return 0
