"""Feishu long-connection (WebSocket) listener for the bridge.

The connection is outbound: the local machine dials Feishu, so this works behind
NAT, behind Tailscale, or on a laptop with no public IP. No webhook, port
forwarding, or relay is required.

Two hard rules enforced here:

1. The SDK handler runs inside the WebSocket receive loop. Blocking it delays
   every subsequent event, so the handler only validates and enqueues.
2. Authorization happens before any agent is invoked, and the allowlist fails
   closed.
"""

from __future__ import annotations

import collections
import json
import threading
import time

from .bridge import BridgeDenied, is_allowed, parse_command

SDK_MISSING = (
    "缺少 lark-oapi（飞书长连接需要它）：\n"
    "  python3 -m pip install 'lark-oapi>=1.8'"
)


def import_sdk():
    try:
        import lark_oapi as lark
    except ImportError as error:
        raise RuntimeError(SDK_MISSING) from error
    return lark


def extract_text(content, message_type):
    """Feishu sends content as a JSON string, not an object."""
    try:
        parsed = json.loads(content or "{}")
    except ValueError:
        return str(content or "").strip()
    if not isinstance(parsed, dict):
        return str(content or "").strip()

    if message_type == "text":
        return str(parsed.get("text") or "").strip()
    if message_type == "post":
        # Rich text: concatenate the text runs in order.
        parts = []
        for block in (parsed.get("content") or []):
            for node in block if isinstance(block, list) else []:
                if isinstance(node, dict) and node.get("tag") == "text":
                    parts.append(str(node.get("text") or ""))
        return "\n".join(part for part in parts if part).strip()
    return ""


def bot_mentions_text(text):
    """Strip the '@bot' token Feishu injects into group messages."""
    cleaned = str(text or "").strip()
    while cleaned.startswith("@_user_"):
        parts = cleaned.split(" ", 1)
        cleaned = parts[1].strip() if len(parts) > 1 else ""
    return cleaned


class SeenMessages:
    """Remembers recently handled message ids so a redelivery is ignored.

    Feishu redelivers an event when its acknowledgement does not arrive in
    time (a flaky network, or a client that stalls). Without this the same
    message runs the agent twice, which burns tokens and can replay tools.
    The window is small and bounded: a redelivery arrives within seconds, so
    keeping the last few hundred ids is plenty.
    """

    def __init__(self, capacity=512):
        self.capacity = capacity
        self._order = collections.deque()
        self._seen = set()
        self._lock = threading.Lock()

    def check_and_add(self, message_id):
        """True the first time an id is seen, False on any repeat."""
        if not message_id:
            # Nothing to dedupe on; let it through rather than drop a message.
            return True
        with self._lock:
            if message_id in self._seen:
                return False
            self._seen.add(message_id)
            self._order.append(message_id)
            while len(self._order) > self.capacity:
                self._seen.discard(self._order.popleft())
            return True


class FeishuListener:
    def __init__(self, config, on_message, logger=None, reply=None, card_action=None):
        self.last_frame_at = time.monotonic()
        self.card_action = card_action
        self.config = config
        self.on_message = on_message
        self.reply = reply
        self.logger = logger
        self._thread = None
        self._client = None
        self._seen = SeenMessages()

    # ------------------------------------------------------------- validation

    def handle_event(self, data):
        """Validate one inbound event and hand the task to the worker.

        Runs on the SDK receive loop: keep it short and never block.
        """
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if message is None or sender is None:
            return

        message_type = str(getattr(message, "message_type", "") or "")
        if message_type not in ("text", "post"):
            if self.logger:
                self.logger.info("bridge: ignoring message_type=%s", message_type)
            return

        sender_id = getattr(getattr(sender, "sender_id", None), "open_id", "") or ""
        chat_id = str(getattr(message, "chat_id", "") or "")
        message_id = str(getattr(message, "message_id", "") or "")
        chat_type = str(getattr(message, "chat_type", "") or "")

        if not self._seen.check_and_add(message_id):
            if self.logger:
                self.logger.info(
                    "bridge: ignoring redelivered message_id=%s chat=%s",
                    message_id,
                    chat_id,
                )
            return

        if not is_allowed(sender_id, self.config):
            # Log the refusal without echoing message content.
            if self.logger:
                self.logger.warning(
                    "bridge: denied sender open_id=%s chat=%s (add it to bridge.allow_open_ids)",
                    sender_id or "<unknown>",
                    chat_id,
                )
            self._reply(message_id, "未授权：你的 open_id 不在 allow_open_ids 里，消息已被拒绝。")
            return

        text = bot_mentions_text(extract_text(getattr(message, "content", ""), message_type))
        try:
            kind, payload = parse_command(text, self.config)
        except BridgeDenied as error:
            self._reply(message_id, f"{error}")
            return

        if self.logger:
            self.logger.info("bridge: received command=%s message_id=%s", kind, message_id)
        self.on_message(
            kind=kind,
            payload=payload,
            message_id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            chat_type=chat_type,
        )

    # ---------------------------------------------------------------- replies

    def _reply(self, message_id, text):
        if not message_id or self.reply is None:
            return
        try:
            # Reply to the original message so threading stays on the phone.
            self.reply(message_id, text)
        except Exception as error:
            if self.logger:
                self.logger.error("bridge: reply failed: %s", error)

    # ------------------------------------------------------------------- run

    def build_client(self):
        lark = import_sdk()
        app = (self.config.get("channels") or {}).get("feishu_app") or {}
        app_id = str(app.get("app_id") or "")
        app_secret = str(app.get("app_secret") or "")
        if not app_id or not app_secret:
            raise RuntimeError("bridge 需要 channels.feishu_app 的 app_id / app_secret")

        builder = lark.EventDispatcherHandler.builder("", "")
        if self.card_action:
            builder.register_p2_card_action_trigger(self.card_action)
        handler = (
            builder
            .register_p2_im_message_receive_v1(self.handle_event)
            # Read receipts arrive constantly; without a handler the SDK logs a
            # "processor not found" ERROR for each one, which buries real
            # failures in the terminal.
            .register_p2_im_message_message_read_v1(lambda _data: None)
            .build()
        )
        listener = self

        class MonitoredClient(lark.ws.Client):
            async def _handle_message(self, message):
                listener.last_frame_at = time.monotonic()
                return await super()._handle_message(message)

        return MonitoredClient(
            app_id,
            app_secret,
            event_handler=handler,
            domain=str(app.get("base_url") or "https://open.feishu.cn"),
            auto_reconnect=True,
            log_level=lark.LogLevel.INFO,
        )

    def start(self):
        self._client = self.build_client()

        def run():
            try:
                self._client.start()
            except Exception as error:
                if self.logger:
                    self.logger.exception("bridge: websocket stopped: %s", error)

        self._thread = threading.Thread(target=run, name="feishu-ws", daemon=True)
        self._thread.start()
        return self._thread

    def join(self):
        if self._thread is not None:
            self._thread.join()
