"""Keep a completion card's complete answer available to its Feishu callback."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time

ACTION = "notification_answer"
HEARTBEAT_INTERVAL = 20
RECEIVER_TIMEOUT = 60
ANSWER_LIFETIME = 24 * 60 * 60
CARD_REQUEST_BYTES = 30_000
ANSWER_PAGE_BYTES = 24_000


def answer_pages(text):
    """Return lossless page boundaries with room for the card and buttons."""
    pages = []
    start = 0
    while start < len(text):
        end = min(len(text), start + ANSWER_PAGE_BYTES)
        low, high = start, end + 1
        while low + 1 < high:
            candidate = (low + high) // 2
            size = len(json.dumps(text[start:candidate], ensure_ascii=False).encode("utf-8"))
            if size <= ANSWER_PAGE_BYTES:
                low = candidate
            else:
                high = candidate
        end = low
        if end < len(text):
            line_end = text.rfind("\n", start + (end - start) // 2, end)
            if line_end >= 0:
                end = line_end + 1
        pages.append((start, end))
        start = end
    return pages


def _button(label, key, page, primary=False):
    return {"tag": "button", "text": {"tag": "plain_text", "content": label},
            "type": "primary" if primary else "default",
            "behaviors": [{"type": "callback", "value": {"kind": ACTION, "key": key, "page": page}}]}


class NotificationAnswers:
    def __init__(self, app_id):
        root = Path.home() / ".local/state/notifyhub/notification-answers"
        self.directory = root / hashlib.sha256(app_id.encode()).hexdigest() if app_id else None
        self.next_heartbeat = 0

    def heartbeat(self):
        if self.directory is None or time.monotonic() < self.next_heartbeat:
            return
        self.next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            (self.directory / "receiver").touch(mode=0o600)
            cutoff = time.time() - ANSWER_LIFETIME
            for path in self.directory.glob("*.json"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    def _receiver_is_ready(self):
        if self.directory is None:
            return False
        try:
            return time.time() - (self.directory / "receiver").stat().st_mtime < RECEIVER_TIMEOUT
        except OSError:
            return False

    def prepare(self, notification, payload):
        if (payload["msg_type"] != "interactive" or not notification.include_last_message
                or not notification.full_answer.strip() or not self._receiver_is_ready()):
            return None
        key = secrets.token_hex(16)
        card = json.loads(payload["content"])
        card["body"]["elements"].append(_button("显示完整回答", key, 0, primary=True))
        content = json.dumps(card, ensure_ascii=False)
        if len(json.dumps({**payload, "content": content}, ensure_ascii=False).encode()) > CARD_REQUEST_BYTES:
            return None
        payload["content"] = content
        return key, card

    def remember(self, prepared, answer, message):
        if not isinstance(message, dict):
            return
        key, preview = prepared
        chat, message_id = message.get("chat_id"), message.get("message_id")
        if not chat or not message_id:
            return
        record = {"chat": chat, "message_id": message_id, "preview": preview, "answer": answer,
                  "expires": time.time() + ANSWER_LIFETIME}
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as stream:
                temp = Path(stream.name)
                json.dump(record, stream, ensure_ascii=False)
            os.replace(temp, self.directory / (key + ".json"))
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def card(self, action, chat, message_id):
        key, page = action.get("key"), action.get("page")
        if (self.directory is None or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key)
                or type(page) is not int or page < -1):
            return None
        try:
            record = json.loads((self.directory / (key + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get("chat") != chat or record.get("message_id") != message_id:
            return None
        expires = record.get("expires")
        preview, text = record.get("preview"), record.get("answer")
        if (not isinstance(expires, (int, float)) or expires < time.time()
                or not isinstance(preview, dict) or not isinstance(text, str)):
            return None
        if page == -1:
            return preview
        pages = answer_pages(text)
        if page >= len(pages):
            return None
        start, end = pages[page]
        elements = [{"tag": "markdown", "content": text[start:end]}]
        if len(pages) > 1:
            elements.append({"tag": "markdown", "content": f"第 {page + 1} 段，共 {len(pages)} 段"})
        if page > 0:
            elements.append(_button("上一段", key, page - 1))
        if page + 1 < len(pages):
            elements.append(_button("下一段", key, page + 1, primary=True))
        elements.append(_button("收起回答", key, -1))
        return {**preview, "body": {"elements": elements}}
