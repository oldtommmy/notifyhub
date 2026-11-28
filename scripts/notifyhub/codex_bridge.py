"""Queue-only Codex continuation sharing the existing Feishu transport."""
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid

from .codex_rpc import CodexRpc, CodexRpcRejected


LIVE_STATUSES = {'active': 'running', 'idle': 'idle', 'systemError': 'failed'}
ENDED_STATUSES = {'completed', 'interrupted', 'failed'}


def _answer_text(turn):
    return '\n\n'.join(item['text'] for item in turn.get('items', [])
                      if item.get('type') == 'agentMessage' and item.get('phase') != 'commentary' and item.get('text'))


class CodexBridge:
    def __init__(self, config, reply, logger, state_path=None, rpc_factory=CodexRpc, questions=None):
        bridge = config.get('bridge') or {}
        self.enabled = bool(bridge.get('codex_enabled', False))
        self.dsh_enabled = bool(bridge.get('dsh_enabled', True))
        self.reply, self.logger = reply, logger
        self.questions = questions
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
        elif message.get('kind') in ('task', 'reply_text') and message.get('payload', [''])[0] == 'codex':
            engine = 'codex'
        if (engine == 'codex' or engine == 'dsh' and self.dsh_enabled) and self.enabled and message.get('chat_type') == 'p2p':
            self.routes[message['chat_id']] = engine
        if engine:
            return engine == 'codex'
        default_engine = 'dsh' if self.dsh_enabled else 'codex'
        return self.routes.get(message.get('chat_id'), default_engine) == 'codex'

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
        runtime = thread.get('status') or {}
        flags = runtime.get('activeFlags') or []
        status = LIVE_STATUSES.get(runtime.get('type'), 'historical')
        if status == 'running' and 'waitingOnApproval' in flags:
            status = 'waiting_approval'
        elif status == 'running' and 'waitingOnUserInput' in flags:
            status = 'waiting_input'
        return {'id': thread['id'], 'title': thread.get('name') or thread.get('preview', '')[:60] or '未命名 Codex 会话',
                'project': Path(cwd).name, 'cwd': cwd, 'model': thread.get('model') or '', 'status': status}

    def _read_turns(self, thread_id, cursor=None, limit=10):
        params = {'threadId': thread_id, 'limit': limit, 'sortDirection': 'desc', 'itemsView': 'full'}
        if cursor:
            params['cursor'] = cursor
        return self._call('thread/turns/list', params)

    def session_view(self, thread_id, kind):
        thread = self._call('thread/read', {'threadId': thread_id})['thread']
        session = self.metadata(thread)
        page = self._read_turns(thread_id)
        latest = next(iter(page.get('data', [])), {})
        detail = ''
        if session['status'] == 'historical':
            waiting = [request for request in self.data['requests'].values()
                       if request['thread'] == thread_id and request['state'] in ('queued', 'submitting', 'unknown')]
            admitted = {item.get('clientId') for item in latest.get('items', []) if item.get('type') == 'userMessage'}
            outstanding = [request for request in waiting if request['client_id'] not in admitted]
            if latest.get('startedAt') and latest.get('completedAt') is None:
                session['status'] = 'unfinished'
                detail = '原会话已有开始记录，尚未读到结束记录。'
                if any(request['state'] == 'queued' for request in outstanding):
                    detail += '另有手机消息在队列中。'
            elif outstanding:
                queued = any(request['state'] == 'queued' for request in outstanding)
                session['status'] = 'queued' if queued else 'submitted'
                detail = '手机消息在队列中，等待原会话接收。' if queued else '手机消息的提交结果待确认。'
            elif latest.get('completedAt') and latest.get('status') in ENDED_STATUSES:
                session['status'] = latest['status']
                detail = '显示最近一轮已记录的状态。'
        if session['status'] == 'waiting_input':
            detail = 'Codex 正在等待你回答。请在拥有该轮的 Codex 桌面会话处理。'
        elif session['status'] == 'waiting_approval':
            detail = 'Codex 正在等待权限确认。请在原桌面会话查看请求。'
        elif kind == 'pending_input':
            detail = (detail + ' ' if detail else '') + '普通问题可以用「回复 原文」发给当前会话。桌面的选择或确认请在 Codex 处理。'
        view = {'type': kind, 'engine': 'Codex', 'session': session, 'detail': detail,
                'activity_at': latest.get('completedAt') or latest.get('startedAt')}
        if kind == 'latest_answer':
            seen = set()
            while True:
                for turn in page.get('data', []):
                    if turn.get('completedAt') is not None and turn.get('status') in ENDED_STATUSES:
                        answer = _answer_text(turn)
                        if answer:
                            return {**view, 'text': answer, 'activity_at': turn['completedAt']}
                cursor = page.get('nextCursor')
                if not cursor or cursor in seen:
                    break
                seen.add(cursor)
                page = self._read_turns(thread_id, cursor)
        return view

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
        text = str(frame.get('payload', ['', ''])[1]).strip() if kind in ('task', 'reply_text') else ''
        if kind == 'task' and text in ('会话', '切换会话', '会话列表'):
            kind = 'attach_list'
            frame['payload'] = ''
        elif kind == 'task' and text in ('状态', '/status'):
            kind = 'status'
        elif kind == 'task' and text in ('下一页', '上一页'):
            kind = 'attach_list'
            frame['payload'] = max(0, self.cache.get(chat, {}).get('page', 0) + (1 if text == '下一页' else -1))
        elif kind == 'task' and text.isdigit() and chat in self.cache:
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
            if self.questions and self.data['bindings'].get(chat) != thread['id']:
                self.questions.forget(chat=chat)
            self.data['bindings'][chat] = thread['id']
            self._save()
            self.cache.pop(chat, None)
            self._reply(frame, '已绑定 Codex 原会话。', view={'type': 'bound', 'engine': 'Codex', 'session': self.metadata(thread)})
            return
        thread_id = self.data['bindings'].get(chat)
        if not thread_id:
            self._reply(frame, '先发“Codex会话”，选择要继续的原会话。')
            return
        if kind in ('status', 'latest_answer', 'pending_input', 'answer_input'):
            view = self.session_view(thread_id, 'pending_input' if kind == 'answer_input' else kind)
            self._reply(frame, view.get('text') or view['detail'] or '当前 Codex 会话。', view=view)
            return
        if kind not in ('task', 'reply_text'):
            self._reply(frame, '发“Codex会话”选择对话，直接发消息续聊；发“DSH会话”切回 dsh。')
            return
        identity = frame['message_id']
        if identity in self.data['requests']:
            self._reply(frame, '这条消息已经登记，不会重复执行。', 'duplicate')
            return
        if any(r['thread'] == thread_id and r['state'] in ('submitting', 'queued', 'unknown') for r in self.data['requests'].values()):
            self._reply(frame, '这个 Codex 会话还有未完成的手机消息，请等待原会话处理。')
            return
        thread = self._call('thread/read', {'threadId': thread_id})['thread']
        if self.questions:
            try:
                text = self.questions.prepare(frame, 'codex', self.metadata(thread)['title'], text)
            except ValueError as error:
                self._reply(frame, str(error))
                return
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
            if self.questions:
                self.questions.forget(message_id=identity)
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
            # A separate app-server can report the desktop's unfinished turn as
            # interrupted. Only a recorded completion time confirms it ended.
            if not turn or turn.get('completedAt') is None or turn.get('status') not in ('completed', 'failed', 'interrupted'):
                continue
            thread = self._call('thread/read', {'threadId': request['thread']})['thread'] if request['message_id'] in self.active_requests else None
            request['state'] = 'finished'
            request['turn_id'] = turn['id']
            self._save()
            if self.questions:
                self.questions.forget(message_id=request['message_id'])
            if request['message_id'] not in self.active_requests:
                continue
            self.active_requests.discard(request['message_id'])
            text = _answer_text(turn) or (turn.get('error') or {}).get('message') or '本轮结束，没有最终文本回答。'
            self._reply(request, text, 'result', {'type': 'result', 'engine': 'Codex', 'session': {**self.metadata(thread), 'status': 'completed' if turn['status'] == 'completed' else 'failed'}, 'text': text, 'ok': turn['status'] == 'completed', 'turn': turn['id'][:8], 'duration': round(time.time() - request['created_at'])})

    def close(self):
        self.stopped = True
        if self.rpc:
            self.rpc.close()
