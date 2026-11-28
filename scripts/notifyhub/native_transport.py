"""Feishu I/O sidecar. Agent execution stays inside the owning dsh process."""
import json
from hashlib import sha256
from pathlib import Path
import queue
import sys
import threading
import time
import unicodedata

from .bridge_cli import _acquire_instance_lock
from .channels import feishu_app
from .config import DEFAULT_CONFIG, load_config
from .feishu_listener import FeishuListener
from .installer import _write
from .log import get_logger
from .native_cards import build_full_answer_card, build_native_card
from .native_actions import CardActions
from .notification_answers import ACTION as NOTIFICATION_ANSWER, NotificationAnswers, answer_pages
from .codex_bridge import CodexBridge
from .antigravity_bridge import AntigravityBridge
from .bridge import is_allowed
from .feishu_listener import SeenMessages
from .state import state_lock


SESSION_QUERIES = {
    '状态': 'status',
    '最新回答': 'latest_answer', '最近回答': 'latest_answer', '/latest': 'latest_answer',
    '待回复': 'pending_input', '/pending': 'pending_input',
}
ENGINE_COMMANDS = {'codex会话': 'codex', 'dsh会话': 'dsh', 'antigravity会话': 'antigravity'}


class NativeTransport:
    def __init__(self, config, logger, emit, questions=None):
        self.config, self.logger, self.emit = config, logger, emit
        self.questions = questions
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
        self.notification_answers = NotificationAnswers(config.get('channels', {}).get('feishu_app', {}).get('app_id', ''))
        self.listener = FeishuListener(config, self.inbound, logger, reply=self.refusal, card_action=self.handle_card_action)
        self.outbound = queue.Queue(maxsize=256)
        self.codex = CodexBridge(config, self.enqueue, logger, state_path=bridge.get('codex_ledger_path'), questions=questions)
        self.antigravity = AntigravityBridge(config, self.enqueue, logger, questions=questions)
        if questions is not None:
            questions.send = self.enqueue
        app_id = config.get('channels', {}).get('feishu_app', {}).get('app_id', '')
        self.routes_path = (Path.home() / '.local/state/notifyhub' / f'feishu-routes-{sha256(app_id.encode()).hexdigest()}.json'
                            if app_id else None)
        try:
            routes = json.loads(self.routes_path.read_text(encoding='utf-8')) if self.routes_path else {}
        except (OSError, ValueError):
            routes = {}
        self.routes = ({chat: engine for chat, engine in routes.items() if engine in ENGINE_COMMANDS.values()}
                       if isinstance(routes, dict) else {})
        self.worker = threading.Thread(target=self._send_loop, daemon=True)

    def _select_engine(self, chat, engine):
        if self.routes.get(chat) == engine:
            return
        if self.questions:
            self.questions.forget(chat=chat, other_than=engine)
        self.routes[chat] = engine
        if self.routes_path:
            try:
                with state_lock(self.routes_path):
                    _write(self.routes_path, self.routes)
            except OSError as error:
                self.logger.warning('Cannot save Feishu agent selection: %s', error)

    def inbound(self, **message):
        # Only messages that passed the listener's allowlist reach this point.
        if message.get('kind') == 'agent':
            message['kind'] = 'task'
        message_id, chat = message['message_id'], message['chat_id']
        self.targets[message_id] = chat
        if message.get('chat_type'):
            self.chat_types[chat] = message['chat_type']
        if message.get('kind') == 'help':
            view = {
                'type': 'help', 'dsh': self.codex.dsh_enabled,
                'codex': self.codex.enabled and message.get('chat_type') == 'p2p',
                'antigravity': self.antigravity.enabled and message.get('chat_type') == 'p2p',
            }
            if self.questions and (view['codex'] or view['antigravity']):
                view['text_questions'] = True
            self.enqueue({'type': 'reply', 'message_id': message_id, 'key': 'help', 'view': view})
            return
        engine = message.get('engine')
        agent, prompt = message['payload'] if message.get('kind') == 'task' else ('', '')
        prompt = str(prompt).strip()
        alias = prompt.lower().replace(' ', '')
        if message['kind'] == 'task':
            if alias in ENGINE_COMMANDS:
                engine = ENGINE_COMMANDS[alias]
                message.update(kind='attach_list', payload='')
            elif prompt.lower() in SESSION_QUERIES:
                message.update(kind=SESSION_QUERIES[prompt.lower()], payload=None)
            else:
                parts = prompt.split(maxsplit=1)
                command = parts[0].lower() if parts else ''
                if command in ('回复', '/reply', '回答', '/answer', '文字问答', '/ask'):
                    if len(parts) < 2:
                        self.refusal(message_id, '请在命令后填写内容。发送「帮助」查看用法。')
                        return
                    prompt = parts[1]
                    if command in ('回复', '/reply'):
                        message.update(kind='reply_text', payload=[agent, prompt])
                    elif command in ('文字问答', '/ask'):
                        message.update(kind='reply_text', payload=[agent, prompt], ask_in_feishu=True)
                    else:
                        message.update(kind='answer_input', payload=prompt)
        if message['kind'] in ('task', 'reply_text', 'answer_input') and len(prompt) > self.prompt_limit:
            self.refusal(message_id, f'这条提问有 {len(prompt)} 个字符，超过 {self.prompt_limit} 字符上限。请分段发送。')
            return
        if message['kind'] in ('latest_answer', 'pending_input', 'answer_input') and message.get('chat_type') != 'p2p':
            self.refusal(message_id, '请与机器人私聊，查看会话回答或处理待回复问题。')
            return
        if engine is None and agent == 'codex' and self.config.get('bridge', {}).get('default_agent') != 'codex':
            engine = 'codex'
        if message.get('chat_type') == 'p2p' and engine in ENGINE_COMMANDS.values() and message['kind'] in (
                'attach', 'attach_list', 'task', 'reply_text'):
            self._select_engine(chat, engine)
        engine = engine or self.routes.get(chat)
        if not engine and self.antigravity.enabled and not self.codex.enabled and not self.codex.dsh_enabled:
            engine = 'antigravity'
        if not engine:
            engine = 'codex' if self.codex.wants(message) else 'dsh'
        if message.get('ask_in_feishu') and (not self.questions or engine == 'dsh' or message.get('chat_type') != 'p2p'):
            self.refusal(message_id, '文字问答需要独立网关。请在机器人私聊选中 Codex 或 Antigravity 会话。')
            return
        if self.questions and message.get('chat_type') == 'p2p':
            kind = message['kind']
            letter = unicodedata.normalize('NFKC', prompt).upper()
            choice = letter if kind in ('task', 'answer_input') and len(letter) == 1 and 'A' <= letter <= 'Z' else None
            if choice or kind == 'pending_input':
                result = self.questions.respond(chat, message.get('sender_id'), choice=choice)
                if result:
                    self.enqueue({'type': 'reply', 'message_id': message_id,
                                  'key': 'question-choice' if choice else 'question-reminder', 'text': result})
                    return
        if engine == 'antigravity':
            self.antigravity.enqueue(message)
            return
        if engine == 'codex':
            self.codex.enqueue({'type': 'message', **message})
        elif self.codex.dsh_enabled:
            if message['kind'] in ('task', 'reply_text'):
                message['payload'] = ['dsh', message['payload'][1]]
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
        if isinstance(value, dict) and value.get('kind') == NOTIFICATION_ANSWER:
            card = self.notification_answers.card(value, chat, parent) if self.buttons_enabled and sender else None
            if card is None:
                return toast('这张卡片的完整回答已不可用，请在桌面原会话查看。', 'warning')
            return P2CardActionTriggerResponse({'card': {'type': 'raw', 'data': card}})
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
            return False
        if response.get('key') != 'refusal' and response.get('message_id') not in self.targets:
            self.logger.warning('native transport rejected reply to an unknown message')
            return False
        try:
            self.outbound.put_nowait(response)
            return True
        except queue.Full:
            self.logger.error('native transport outbound queue full; response not sent')
            self.receipt(response, 'not_sent')
            return False

    def receipt(self, response, status):
        if response.get('key', '').startswith('question'):
            if status != 'delivered' and response['key'].startswith('question:') and self.questions:
                self.questions.forget(message_id=response['message_id'], status='unavailable')
            return
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
            if self.buttons_enabled:
                self.notification_answers.heartbeat()
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
                if len(text) > limit and not response.get('key', '').startswith('question'):
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
                        if view.get('type') in ('result', 'latest_answer') and view.get('answer_complete', True):
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
