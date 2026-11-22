"""Queue-only Codex continuation sharing the existing Feishu transport."""
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid

from .codex_rpc import CodexRpc, CodexRpcRejected


class CodexBridge:
    def __init__(self, config, reply, logger, state_path=None, rpc_factory=CodexRpc):
        self.enabled = bool(config.get('bridge', {}).get('codex_enabled', False))
        self.reply, self.logger = reply, logger
        self.path = Path(state_path or '~/.local/state/notifyhub/codex-bridge.json').expanduser()
        self.data = {'bindings': {}, 'requests': {}}
        try:
            if self.enabled:
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            pass
        self.rpc_factory, self.rpc = rpc_factory, None
        self.inbox = queue.Queue(maxsize=256)
        self.worker = None
        self.stopped = False
        self.cache = {}
        self.routes = {}
        self.active_requests = set()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.path)

    def wants(self, message):
        engine = message.pop('engine', None)
        text = str((message.get('payload') or ['', ''])[1]) if message.get('kind') == 'task' else ''
        if text.lower() in ('codex会话', 'codex 会话'):
            engine = 'codex'
            message.update(kind='attach_list', payload='')
        elif text.lower() in ('dsh会话', 'dsh 会话'):
            engine = 'dsh'
            message.update(kind='attach_list', payload='')
        elif message.get('kind') == 'task' and message.get('payload', [''])[0] == 'codex':
            engine = 'codex'
        if engine in ('codex', 'dsh') and self.enabled and message.get('chat_type') == 'p2p':
            self.routes[message['chat_id']] = engine
        return engine == 'codex' or (engine != 'dsh' and self.routes.get(message.get('chat_id')) == 'codex')

    def enqueue(self, frame):
        if self.worker is None:
            self.worker = threading.Thread(target=self._loop, daemon=True, name='codex-feishu')
            self.worker.start()
        try:
            self.inbox.put_nowait(dict(frame))
        except queue.Full:
            if frame.get('message_id'):
                self._reply(frame, 'Codex 续聊请求太多，请稍后再试。', 'busy')

    def _loop(self):
        while not self.stopped:
            frame = None
            try:
                frame = self.inbox.get(timeout=2)
                self.handle(frame)
            except queue.Empty:
                pass
            except Exception as error:
                self.logger.warning('Codex bridge request failed: %s', error)
                if frame and frame.get('message_id'):
                    self._reply(frame, 'Codex 暂时不可用。请检查 CLI 登录和本机会话状态；提交状态不明确时，请先查看原会话。', 'error')
            try:
                self.poll()
            except Exception as error:
                self.logger.warning('Codex result check failed: %s', error)

    def _call(self, method, params):
        if self.rpc is None:
            self.rpc = self.rpc_factory()
        return self.rpc.call(method, params)

    def _reply(self, frame, text, key='reply', view=None):
        self.reply({'type': 'reply', 'message_id': frame['message_id'],
                    'request_id': frame['message_id'], 'text': text, 'key': key, 'view': view})

    @staticmethod
    def metadata(thread):
        cwd = str(thread.get('cwd') or '')
        return {'id': thread['id'], 'title': thread.get('name') or thread.get('preview', '')[:60] or '未命名 Codex 会话',
                'project': Path(cwd).name, 'cwd': cwd, 'model': thread.get('model') or '', 'status': 'historical'}

    def handle(self, frame):
        if frame.get('type') == 'delivery':
            request = self.data['requests'].get(frame.get('request_id'))
            if request and frame.get('key') == 'result':
                request['delivery'] = frame['status']
                self._save()
            return
        if frame.get('chat_type') != 'p2p':
            self._reply(frame, 'Codex 续聊只支持与机器人的私聊。')
            return
        if not self.enabled:
            self._reply(frame, 'Codex 续聊尚未启用，请在 GUI 的续聊选项中勾选 Codex 入口。')
            return
        kind, chat = frame['kind'], frame['chat_id']
        text = str(frame.get('payload', ['', ''])[1]).strip() if kind == 'task' else ''
        if text in ('会话', '切换会话', '会话列表'):
            kind = 'attach_list'
            frame['payload'] = ''
        elif text in ('状态', '/status'):
            kind = 'status'
        elif text in ('下一页', '上一页'):
            kind = 'attach_list'
            frame['payload'] = max(0, self.cache.get(chat, {}).get('page', 0) + (1 if text == '下一页' else -1))
        elif text.isdigit() and chat in self.cache:
            kind = 'attach'
            frame['payload'] = text
        if kind == 'attach_list':
            cached = self.cache.get(chat, {})
            page = max(0, int(frame.get('payload') or 0))
            if page == 0:
                cached = {'cursors': [None]}
            cursors = cached.get('cursors', [None])
            if page >= len(cursors):
                self._reply(frame, '请先刷新 Codex 会话列表。')
                return
            result = self._call('thread/list', {'limit': 10, 'cursor': cursors[page], 'sortKey': 'updated_at', 'sortDirection': 'desc'})
            rows = [t for t in result.get('data', []) if not t.get('parentThreadId') and t.get('canAcceptDirectInput') is not False]
            next_cursor = result.get('nextCursor')
            cursors = cursors[:page + 1] + ([next_cursor] if next_cursor else [])
            self.cache[chat] = {'ids': [t['id'] for t in rows], 'cursors': cursors, 'page': page, 'expires': time.time() + 300}
            view = {'type': 'sessions', 'engine': 'Codex', 'sessions': [{**self.metadata(t), 'current': t['id'] == self.data['bindings'].get(chat)} for t in rows], 'page': page, 'pages': page + (2 if next_cursor else 1), 'total': None}
            self._reply(frame, '选择一个 Codex 会话。', view=view)
            return
        if kind == 'attach':
            argument = str(frame.get('payload') or '').strip()
            if argument.isdigit():
                cached = self.cache.get(chat, {})
                if cached.get('expires', 0) < time.time():
                    self._reply(frame, '列表已过期，请发“Codex会话”刷新。')
                    return
                index = int(argument) - 1
                if index < 0 or index >= len(cached.get('ids', [])):
                    self._reply(frame, '请回复本页有效的会话序号。')
                    return
                argument = cached['ids'][index]
            thread = self._call('thread/read', {'threadId': argument})['thread']
            if thread.get('parentThreadId') or thread.get('canAcceptDirectInput') is False:
                self._reply(frame, '该会话不接受直接输入，请选择主会话。')
                return
            self.data['bindings'][chat] = thread['id']
            self._save()
            self.cache.pop(chat, None)
            self._reply(frame, '已绑定 Codex 原会话。', view={'type': 'bound', 'engine': 'Codex', 'session': self.metadata(thread)})
            return
        thread_id = self.data['bindings'].get(chat)
        if not thread_id:
            self._reply(frame, '先发“Codex会话”，选择要继续的原会话。')
            return
        if kind == 'status':
            thread = self._call('thread/read', {'threadId': thread_id})['thread']
            self._reply(frame, '当前 Codex 会话。', view={'type': 'status', 'engine': 'Codex', 'session': self.metadata(thread)})
            return
        if kind != 'task':
            self._reply(frame, '发“Codex会话”选择对话，直接发消息续聊；发“DSH会话”切回 dsh。')
            return
        identity = frame['message_id']
        if identity in self.data['requests']:
            self._reply(frame, '这条消息已经登记，不会重复执行。', 'duplicate')
            return
        if any(r['thread'] == thread_id and r['state'] in ('submitting', 'queued', 'unknown') for r in self.data['requests'].values()):
            self._reply(frame, '这个 Codex 会话还有未完成的手机消息，请等待原会话处理。')
            return
        self._call('thread/read', {'threadId': thread_id})
        request = {'message_id': identity, 'thread': thread_id, 'client_id': str(uuid.uuid4()), 'state': 'submitting', 'created_at': time.time()}
        self.data['requests'][identity] = request
        self._save()
        self.active_requests.add(identity)
        try:
            result = self._call('thread/queue/add', {'threadId': thread_id, 'clientUserMessageId': request['client_id'], 'input': [{'type': 'text', 'text': text}]})
            request['queue_id'] = result['queuedSubmission']['id']
            request['state'] = 'queued'
            self._save()
        except CodexRpcRejected:
            request['state'] = 'rejected'
            self._save()
            self.active_requests.discard(identity)
            self._reply(frame, 'Codex 拒绝了入队请求，请检查会话是否仍可用。', 'rejected')
            return
        except Exception:
            request['state'] = 'unknown'
            self._save()
            self._reply(frame, '入队结果不明确，请检查 Codex 原会话。为避免重复，本消息不会自动重试。', 'uncertain')
            return
        self._reply(frame, '已加入 Codex 原会话队列。请保持目标桌面会话打开；入队不等于本轮已经开始执行。', 'accepted')

    def poll(self):
        if not self.enabled:
            return
        for request in self.data['requests'].values():
            if request['state'] not in ('queued', 'unknown', 'submitting'):
                continue
            turns = self._call('thread/turns/list', {'threadId': request['thread'], 'limit': 20, 'sortDirection': 'desc', 'itemsView': 'full'})
            turn = next((turn for turn in turns.get('data', []) if any(item.get('type') == 'userMessage' and item.get('clientId') == request['client_id'] for item in turn.get('items', []))), None)
            if not turn or turn.get('status') not in ('completed', 'failed', 'interrupted'):
                continue
            texts = [item['text'] for item in turn.get('items', []) if item.get('type') == 'agentMessage' and item.get('phase') != 'commentary' and item.get('text')]
            thread = self._call('thread/read', {'threadId': request['thread']})['thread'] if request['message_id'] in self.active_requests else None
            request['state'] = 'finished'
            request['turn_id'] = turn['id']
            self._save()
            if request['message_id'] not in self.active_requests:
                continue
            self.active_requests.discard(request['message_id'])
            text = '\n\n'.join(texts) or (turn.get('error') or {}).get('message') or '本轮结束，没有最终文本回答。'
            self._reply(request, text, 'result', {'type': 'result', 'engine': 'Codex', 'session': {**self.metadata(thread), 'status': 'completed' if turn['status'] == 'completed' else 'failed'}, 'text': text, 'ok': turn['status'] == 'completed', 'turn': turn['id'][:8], 'duration': round(time.time() - request['created_at'])})

    def close(self):
        self.stopped = True
        if self.rpc:
            self.rpc.close()
