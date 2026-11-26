"""Short-lived card capabilities, bound to the originating chat and process."""
import hashlib
import hmac
import json
import secrets
import threading
import time

ACTION_LIFETIME = 900


class CardActions:
    def __init__(self):
        self.secret = secrets.token_bytes(32)
        self._answers = {}
        self._answer_lock = threading.Lock()

    def sign(self, chat_id, kind, payload='', engine='dsh'):
        value = {'chat': chat_id, 'kind': kind, 'payload': payload, 'engine': engine,
                 'expires': int(time.time()) + ACTION_LIFETIME, 'nonce': secrets.token_hex(12)}
        value['signature'] = self._digest(value)
        return value

    def _digest(self, value):
        raw = json.dumps({k: v for k, v in value.items() if k != 'signature'}, sort_keys=True).encode()
        return hmac.new(self.secret, raw, hashlib.sha256).hexdigest()

    def verify(self, value, chat_id):
        if not isinstance(value, dict) or value.get('chat') != chat_id:
            return False
        engine, kind = value.get('engine', 'dsh'), value.get('kind')
        if not (engine in ('dsh', 'codex', 'antigravity') and kind in ('attach', 'attach_list', 'status', 'help', 'view_answer')
                or engine == 'current' and kind == 'status'):
            return False
        try:
            return (int(value['expires']) >= time.time()
                    and hmac.compare_digest(str(value.get('signature', '')), self._digest(value)))
        except (KeyError, ValueError, TypeError):
            return False

    def remember_answer(self, chat_id, answer_id, view, pages):
        with self._answer_lock:
            self._answers[answer_id] = {
                'chat': chat_id, 'view': view, 'pages': pages,
                'expires': int(time.time()) + ACTION_LIFETIME,
            }

    def get_answer(self, chat_id, answer_id):
        now = time.time()
        with self._answer_lock:
            answer = self._answers.get(answer_id)
            if not answer or answer['chat'] != chat_id or answer['expires'] < now:
                return None
            answer['expires'] = int(now) + ACTION_LIFETIME
            return answer['view'], answer['pages']

    def prune_answers(self):
        now = time.time()
        with self._answer_lock:
            self._answers = {key: value for key, value in self._answers.items() if value['expires'] >= now}
