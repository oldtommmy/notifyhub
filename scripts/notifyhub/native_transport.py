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
from .native_cards import build_native_card
from .native_actions import CardActions
from .codex_bridge import CodexBridge
from .bridge import is_allowed
from .feishu_listener import SeenMessages


class NativeTransport:
    def __init__(self, config, logger, emit):
        self.config, self.logger, self.emit = config, logger, emit
        self.targets = {}
        self.card_replies = {}
        self.actions = CardActions()
        self.card_seen = SeenMessages()
        self.buttons_enabled = bool(config.get('bridge', {}).get('card_actions', False))
        self.listener = FeishuListener(config, self.inbound, logger, reply=self.refusal, card_action=self.handle_card_action)
        self.outbound = queue.Queue(maxsize=256)
        self.codex = CodexBridge(config, self.enqueue, logger, state_path=config.get('bridge', {}).get('codex_ledger_path'))
        self.worker = threading.Thread(target=self._send_loop, daemon=True)

    def inbound(self, **message):
        # Only messages that passed the listener's allowlist reach this point.
        if message.get('kind') == 'agent':
            message['kind'] = 'task'
        self.targets[message['message_id']] = message['chat_id']
        if self.codex.wants(message):
            self.codex.enqueue({'type': 'message', **message})
        else:
            self.emit({'type': 'message', **message})

    def handle_card_action(self, data):
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
        def toast(text, kind='info'):
            return P2CardActionTriggerResponse({'toast': {'type': kind, 'content': text}})
        event = getattr(data, 'event', None)
        sender = getattr(getattr(event, 'operator', None), 'open_id', '')
        context = getattr(event, 'context', None)
        chat = getattr(context, 'open_chat_id', '')
        parent = getattr(context, 'open_message_id', '')
        value = getattr(getattr(event, 'action', None), 'value', None)
        if not is_allowed(sender, self.config):
            return toast('你没有此机器人的操作权限。', 'error')
        if not self.buttons_enabled or not parent or not self.actions.verify(value, chat):
            return toast('按钮已失效，请发送「会话」获取新卡片。', 'warning')
        request_id = 'card:' + value['nonce']
        if not self.card_seen.check_and_add(request_id):
            return toast('已收到这次操作，请查看回复。')
        self.card_replies[request_id] = parent
        self.inbound(kind=value['kind'], payload=value['payload'], message_id=request_id,
                     chat_id=chat, sender_id=sender, chat_type='p2p' if value.get('engine') == 'codex' else '', engine=value.get('engine', 'dsh'))
        return toast('已收到，正在处理。')

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
        receipt = {'type': 'delivery', 'request_id': response.get('request_id'), 'key': response.get('key'), 'status': status}
        if response.get('request_id') in self.codex.data['requests']:
            self.codex.enqueue(receipt)
        else:
            self.emit(receipt)

    def _send_loop(self):
        while True:
            response = self.outbound.get()
            try:
                app = self.config['channels']['feishu_app']
                text = str(response.get('text', ''))
                limit = int(self.config.get('bridge', {}).get('max_message_chars', 3000))
                if len(text) > limit:
                    text = text[:limit] + '\n（内容截断，完整结果见 dsh 原会话）'
                message_id = response['message_id']
                target = self.card_replies.get(message_id, message_id)
                key = response.get('key', 'reply')
                if message_id in self.card_replies:
                    key += ':' + message_id
                if response.get('view'):
                    action_value = (lambda kind, payload: self.actions.sign(self.targets[message_id], kind, payload, engine='codex' if response['view'].get('engine') == 'Codex' else 'dsh')) if self.buttons_enabled else None
                    card = build_native_card(response['view'], max_chars=limit, action_value=action_value)
                    feishu_app.reply_card(target, card, app, dedupe_key=key)
                else:
                    feishu_app.reply_text(target, text, app, dedupe_key=key)
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
        transport.codex.close()
        lock.close()
    return 0
