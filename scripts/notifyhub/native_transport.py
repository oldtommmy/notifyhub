"""Feishu I/O sidecar. Agent execution stays inside the owning dsh process."""
import json
import queue
import sys
import threading
import time

from .bridge_cli import _acquire_instance_lock
from .channels import feishu_app
from .config import DEFAULT_CONFIG, load_config
from .feishu_listener import FeishuListener
from .log import get_logger
from .native_cards import answer_pages, build_full_answer_card, build_native_card
from .native_actions import CardActions
from .codex_bridge import CodexBridge
from .antigravity_bridge import AntigravityBridge
from .bridge import is_allowed
from .feishu_listener import SeenMessages


class NativeTransport:
    def __init__(self, config, logger, emit):
        self.config, self.logger, self.emit = config, logger, emit
        self.targets = {}
        self.chat_types = {}
        self.card_replies = {}
        self.help_image_key = None
        self.help_image_retry_at = 0.0
        self.actions = CardActions()
        self.card_seen = SeenMessages()
        bridge = config.get('bridge', {})
        self.prompt_limit = int(bridge.get('max_prompt_chars', DEFAULT_CONFIG['bridge']['max_prompt_chars']))
        self.answer_limit = int(bridge.get('max_message_chars', DEFAULT_CONFIG['bridge']['max_message_chars']))
        self.buttons_enabled = bool(bridge.get('card_actions', DEFAULT_CONFIG['bridge']['card_actions']))
        self.listener = FeishuListener(config, self.inbound, logger, reply=self.refusal, card_action=self.handle_card_action)
        self.outbound = queue.Queue(maxsize=256)
        self.codex = CodexBridge(config, self.enqueue, logger, state_path=bridge.get('codex_ledger_path'))
        self.antigravity = AntigravityBridge(config, self.enqueue)
        self.antigravity_chats = set()
        self.worker = threading.Thread(target=self._send_loop, daemon=True)

    def inbound(self, **message):
        # Only messages that passed the listener's allowlist reach this point.
        if message.get('kind') == 'agent':
            message['kind'] = 'task'
        message_id, chat = message['message_id'], message['chat_id']
        self.targets[message_id] = chat
        if message.get('chat_type'):
            self.chat_types[chat] = message['chat_type']
        if message.get('kind') == 'help':
            self.enqueue({'type': 'reply', 'message_id': message_id, 'key': 'help', 'view': {
                'type': 'help', 'dsh': self.codex.dsh_enabled,
                'codex': self.codex.enabled and message.get('chat_type') == 'p2p',
                'antigravity': self.antigravity.enabled and message.get('chat_type') == 'p2p',
            }})
            return
        engine = message.get('engine')
        agent, prompt = message.get('payload') if message.get('kind') == 'task' else ('', '')
        prompt = str(prompt)
        alias = prompt.strip().lower().replace(' ', '')
        if alias == 'antigravity会话':
            engine = 'antigravity'
            message.update(kind='attach_list', payload='')
        elif len(prompt) > self.prompt_limit:
            self.refusal(message_id, f'这条提问有 {len(prompt)} 个字符，超过 {self.prompt_limit} 字符上限。请分段发送。')
            return
        if engine == 'antigravity' or (not engine and (chat in self.antigravity_chats or
                                      self.antigravity.enabled and not self.codex.enabled and not self.codex.dsh_enabled)
                                      and alias not in ('dsh会话', 'codex会话') and agent != 'codex'):
            if self.antigravity.enabled and message.get('chat_type') == 'p2p':
                self.antigravity_chats.add(chat)
            self.antigravity.handle(message)
            return
        if engine in ('codex', 'dsh') or alias in ('dsh会话', 'codex会话') or agent == 'codex':
            self.antigravity_chats.discard(chat)
        if self.codex.wants(message):
            self.codex.enqueue({'type': 'message', **message})
        elif self.codex.dsh_enabled:
            self.emit({'type': 'message', **message})
        else:
            self.enqueue({'type': 'reply', 'message_id': message['message_id'],
                          'text': 'dsh 续聊未启用。发送“Codex会话”选择 Codex 对话。',
                          'key': 'dsh-disabled'})

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
            return toast('按钮已失效，请发送「帮助」获取新菜单。', 'warning')
        request_id = 'card:' + value['nonce']
        if not self.card_seen.check_and_add(request_id):
            return toast('已收到这次操作，请查看回复。')
        if value['kind'] == 'view_answer':
            card = self._answer_card(chat, value['payload'])
            if card is None:
                return toast('完整回答的查看入口已过期，请在桌面原会话查看。', 'warning')
            return P2CardActionTriggerResponse({'card': {'type': 'raw', 'data': card}})
        self.card_replies[request_id] = parent
        engine = value.get('engine', 'dsh')
        self.inbound(kind=value['kind'], payload=value['payload'], message_id=request_id,
                     chat_id=chat, sender_id=sender,
                     chat_type=self.chat_types.get(chat, 'p2p' if engine in ('codex', 'antigravity') else ''),
                     engine=None if engine == 'current' else engine)
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
        if (response.get('view') or {}).get('engine') == 'Antigravity' and response.get('key') == 'result':
            self.antigravity.receipt(response['message_id'], status)
        elif response.get('request_id') in self.codex.data['requests']:
            self.codex.enqueue(receipt)
        else:
            self.emit(receipt)

    def _help_logo(self, app):
        if self.help_image_key is None and time.monotonic() >= self.help_image_retry_at:
            try:
                self.help_image_key = feishu_app.help_logo_key(app)
            except (OSError, ValueError, feishu_app.FeishuAppError):
                self.help_image_retry_at = time.monotonic() + 60
                self.logger.info('NotifyHub help card logo is unavailable')
        return self.help_image_key

    def _action_value(self, chat_id, view):
        engine = view.get('engine', 'dsh').lower()
        return lambda kind, payload, selected=engine: self.actions.sign(chat_id, kind, payload, engine=selected)

    def _answer_card(self, chat_id, payload):
        if not isinstance(payload, str):
            return None
        answer_id, _, page_number = payload.rpartition(':')
        try:
            page = int(page_number)
        except ValueError:
            return None
        answer = self.actions.get_answer(chat_id, answer_id)
        if answer is None:
            return None
        view, pages = answer
        action_value = self._action_value(chat_id, view)
        if page == -1:
            return build_native_card(view, self.answer_limit, action_value, answer_id=answer_id)
        if 0 <= page < len(pages):
            return build_full_answer_card(view, pages, page, answer_id, action_value)
        return None

    def _send_loop(self):
        while True:
            try:
                response = self.outbound.get(timeout=2)
            except queue.Empty:
                self.actions.prune_answers()
                try:
                    response = self.antigravity.take_result(self.targets)
                except (OSError, ValueError) as error:
                    self.logger.warning('Antigravity result check failed: %s', error)
                    continue
                if response is None:
                    continue
            status = 'unknown'
            try:
                app = self.config['channels']['feishu_app']
                text = str(response.get('text', ''))
                limit = self.answer_limit
                if len(text) > limit:
                    text = text[:limit] + '\n（内容截断，完整结果见桌面原会话）'
                message_id = response['message_id']
                target = self.card_replies.get(message_id, message_id)
                key = response.get('key', 'reply')
                if message_id in self.card_replies:
                    key += ':' + message_id
                if response.get('view'):
                    view = response['view']
                    action_value = None
                    answer_id = None
                    if self.buttons_enabled:
                        chat_id = response.get('chat_id') or self.targets[message_id]
                        action_value = self._action_value(chat_id, view)
                        if view.get('type') == 'result' and view.get('answer_complete', True):
                            answer = str(view.get('text') or '')
                            if answer.strip():
                                answer_id = message_id
                                self.actions.remember_answer(chat_id, answer_id, {**view, 'text': answer}, answer_pages(answer))
                    logo_key = self._help_logo(app) if view.get('type') == 'help' else None
                    card = build_native_card(view, max_chars=limit, action_value=action_value,
                                             logo_key=logo_key, answer_id=answer_id)
                    feishu_app.reply_card(target, card, app, dedupe_key=key)
                else:
                    feishu_app.reply_text(target, text, app, dedupe_key=key)
            except Exception as error:
                # No blind retries or legacy outbox: a timeout may mean delivered.
                self.logger.warning('native reply outcome unconfirmed: %s', error)
            else:
                self.logger.info('native reply delivered message_id=%s key=%s', response['message_id'], response.get('key', 'reply'))
                status = 'delivered'
            try:
                self.receipt(response, status)
            except (OSError, ValueError) as error:
                self.logger.warning('cannot record native delivery outcome: %s', error)


def cmd_native_transport(args):
    config = load_config(args.config)
    if config.get('bridge', {}).get('gateway_enabled'):
        from .gateway import run_dsh_client
        return run_dsh_client(config)
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
