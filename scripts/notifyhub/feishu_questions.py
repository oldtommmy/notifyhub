"""Text questions waiting for a human reply in a bound Feishu private chat."""
import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
import select
import socketserver
import string
import sys
import threading
import time

from .bridge import is_allowed

WAIT_SECONDS = 300
MAX_QUESTIONS = 256
OPTION_KEYS = string.ascii_uppercase[:6]
ENGINE_NAMES = {'codex': 'Codex', 'antigravity': 'Antigravity'}


def question_socket(path):
    path = Path(path)
    return path.with_name(f'{path.stem}-questions.sock')


@dataclass
class Turn:
    message_id: str
    chat: str
    sender: str
    engine: str
    title: str
    expires: float


@dataclass
class Question:
    turn: Turn
    prompt: str
    options: list[str]
    deadline: float
    ready: threading.Event = field(default_factory=threading.Event)
    result: dict = field(default_factory=lambda: {'status': 'expired'})

    def text(self):
        options = '\n'.join(f'{letter}. {option}' for letter, option in zip(OPTION_KEYS, self.options))
        return f'{ENGINE_NAMES[self.turn.engine]} · {self.turn.title}\n\n{self.prompt}\n\n{options}\n\n直接回复选项字母。这道题等待 5 分钟。'


class FeishuQuestions:
    def __init__(self, path, config):
        self.path, self.config = path, config
        self.send = None
        self.lock = threading.Lock()
        self.turns = {}
        self.pending = {}

    def prepare(self, frame, engine, title, text):
        if not frame.get('ask_in_feishu'):
            return text
        if frame.get('chat_type') != 'p2p' or not is_allowed(frame.get('sender_id'), self.config):
            raise ValueError('请在授权的机器人私聊中使用文字问答。')
        now = time.monotonic()
        with self.lock:
            self.turns = {key: turn for key, turn in self.turns.items() if turn.expires > now}
            if len(self.turns) >= MAX_QUESTIONS:
                raise ValueError('文字问答暂时繁忙，请稍后再试。')
            ticket = secrets.token_urlsafe(16)
            self.turns[ticket] = Turn(frame['message_id'], frame['chat_id'], frame['sender_id'],
                                      engine, str(title or '原会话')[:120], now + 24 * 3600)
        return (text + '\n\n若需要我在选项中做决定，请用 NotifyHub 的 ask_feishu 工具，'
                f'传入这轮的回复码 `{ticket}`。如果工具暂不可用，请用文字列出问题和选项，结束本轮，等我回复。')

    def forget(self, *, message_id=None, chat=None, other_than=None, status='session_changed'):
        with self.lock:
            for ticket, turn in list(self.turns.items()):
                belongs = turn.message_id == message_id if message_id else turn.chat == chat
                if belongs and turn.engine != other_than:
                    del self.turns[ticket]
                    pending = self.pending.get(turn.chat)
                    if pending and pending.turn is turn:
                        del self.pending[turn.chat]
                        pending.result = {'status': status}
                        pending.ready.set()

    def respond(self, chat, sender, *, choice=None):
        """Read or settle the exact waiting question for this sender and chat."""
        with self.lock:
            pending = self.pending.get(chat)
            if not pending or pending.turn.sender != sender or pending.deadline <= time.monotonic():
                return None
            if choice is None:
                return pending.text()
            letters = OPTION_KEYS[:len(pending.options)]
            if choice not in letters:
                return f'这道题可回复 {" / ".join(letters)}。'
            option = pending.options[letters.index(choice)]
            pending.result = {'status': 'answered', 'choice': choice, 'answer': option}
            del self.pending[chat]
            pending.ready.set()
            return f'已将 {choice} 交给原会话。'

    def ask(self, incoming, disconnected):
        if not isinstance(incoming, dict):
            return {'status': 'invalid_question'}
        engine, ticket = incoming.get('engine'), incoming.get('ticket')
        question, options = incoming.get('question'), incoming.get('options')
        if (not isinstance(ticket, str) or not isinstance(question, str) or not question.strip()
                or len(question) > 600 or not isinstance(options, list) or not 2 <= len(options) <= len(OPTION_KEYS)
                or any(not isinstance(option, str) or not option.strip() or len(option) > 160 for option in options)):
            return {'status': 'invalid_question'}
        with self.lock:
            turn = self.turns.get(ticket)
            if (not turn or turn.engine != engine or time.monotonic() >= turn.expires
                    or not is_allowed(turn.sender, self.config)):
                return {'status': 'invalid_ticket'}
            if turn.chat in self.pending:
                return {'status': 'question_pending'}
            pending = Question(turn, question.strip(), [option.strip() for option in options],
                               time.monotonic() + WAIT_SECONDS)
            self.pending[turn.chat] = pending
        try:
            if not self.send({'type': 'reply', 'message_id': turn.message_id, 'key': f'question:{secrets.token_hex(8)}',
                              'text': pending.text()}):
                return {'status': 'unavailable'}
            while time.monotonic() < pending.deadline and not disconnected():
                if pending.ready.wait(0.5):
                    break
            return pending.result
        finally:
            with self.lock:
                if self.pending.get(turn.chat) is pending:
                    del self.pending[turn.chat]

    def open(self):
        relay = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                try:
                    raw = self.rfile.readline(16000)
                    incoming = json.loads(raw)
                    result = relay.ask(incoming, lambda: bool(select.select([self.connection], [], [], 0)[0]))
                    self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')
                except (OSError, ValueError):
                    return

        self.path.unlink(missing_ok=True)
        server = socketserver.ThreadingUnixStreamServer(str(self.path), Handler)
        server.daemon_threads = True
        try:
            self.path.chmod(0o600)
        except OSError:
            server.server_close()
            self.path.unlink(missing_ok=True)
            raise
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def run_mcp(path, engine):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print('请在 MCP 使用的 Python 环境安装 mcp>=1.9,<2。', file=sys.stderr)
        return 1

    server = FastMCP('NotifyHub', instructions=(
        'Use ask_feishu for ordinary choices when the current user message includes a NotifyHub reply code. '
        'If it cannot receive an answer, present the same options in the final reply and wait for a follow-up.'))

    @server.tool()
    async def ask_feishu(ticket: str, question: str, options: list[str]) -> dict:
        """Show an ordinary multiple-choice question in Feishu and wait for one choice.

        Use the current turn's reply code as ticket. Supply 2 to 6 option texts in order.
        Feishu labels them A to F. Only an answered result contains the user's decision.
        """
        writer = None
        try:
            reader, writer = await asyncio.open_unix_connection(str(path))
            request = {'ticket': ticket, 'engine': engine, 'question': question, 'options': options}
            writer.write(json.dumps(request, ensure_ascii=False).encode() + b'\n')
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), WAIT_SECONDS + 10))
        except (OSError, ValueError, asyncio.TimeoutError):
            return {'status': 'unavailable'}
        finally:
            if writer is not None:
                writer.close()

    server.run(transport='stdio')
    return 0
