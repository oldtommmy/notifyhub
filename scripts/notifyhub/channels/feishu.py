import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

from ..render import build_card, render_text


class FeishuError(RuntimeError):
    pass


def sign(timestamp: str, secret: str) -> str:
    key = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(key, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_payload(notification, config):
    timestamp = str(int(time.time()))
    payload = {}

    if config.get("secret"):
        payload["timestamp"] = timestamp
        payload["sign"] = sign(timestamp, config["secret"])

    if config.get("message_type", "card") == "text":
        payload["msg_type"] = "text"
        payload["content"] = {"text": render_text(notification)}
    else:
        payload["msg_type"] = "interactive"
        payload["card"] = build_card(notification, at_all=config.get("at_all", False))

    return payload


def send(notification, config, logger=None):
    url = config.get("webhook_url", "")
    if not url:
        raise FeishuError("feishu.webhook_url is not configured")

    body = json.dumps(build_payload(notification, config), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    timeout = float(config.get("timeout_seconds", 5))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise FeishuError(f"HTTP {error.code}: {detail[:300]}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FeishuError(f"request failed: {error}") from error

    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise FeishuError(f"invalid response: {raw[:300]}") from error

    code = parsed.get("code", parsed.get("StatusCode", 0))
    if code not in (0, None):
        raise FeishuError(f"code={code} msg={parsed.get('msg', '')}")

    return parsed
