"""Short-lived card capabilities, bound to the originating chat and process."""
import hashlib
import hmac
import json
import secrets
import time


class CardActions:
    def __init__(self):
        self.secret = secrets.token_bytes(32)

    def sign(self, chat_id, kind, payload=''):
        value = {'chat': chat_id, 'kind': kind, 'payload': payload,
                 'expires': int(time.time()) + 900, 'nonce': secrets.token_hex(12)}
        value['signature'] = self._digest(value)
        return value

    def _digest(self, value):
        raw = json.dumps({k: v for k, v in value.items() if k != 'signature'}, sort_keys=True).encode()
        return hmac.new(self.secret, raw, hashlib.sha256).hexdigest()

    def verify(self, value, chat_id):
        if not isinstance(value, dict) or value.get('chat') != chat_id:
            return False
        if value.get('kind') not in ('attach', 'attach_list', 'status'):
            return False
        try:
            return (int(value['expires']) >= time.time()
                    and hmac.compare_digest(str(value.get('signature', '')), self._digest(value)))
        except (KeyError, ValueError, TypeError):
            return False
