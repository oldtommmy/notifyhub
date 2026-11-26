import hashlib
import http.client
import json
from pathlib import Path
import uuid
import urllib.error
import urllib.parse
import urllib.request

from ..render import build_card, render_text
from ..runtime import launcher_path
from ..state import state_lock

TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_PATH = "/open-apis/im/v1/messages"
RECEIVE_ID_TYPES = {"email", "open_id", "user_id", "union_id", "chat_id"}


class FeishuAppError(RuntimeError):
    pass


class AmbiguousSendError(FeishuAppError):
    """Message submission may have succeeded; automatic resend is unsafe."""


class TransportError(FeishuAppError):
    pass


def build_content(notification, config):
    if config.get("message_type", "card") == "text":
        return "text", {"text": render_text(notification)}
    return "interactive", build_card(notification, at_all=config.get("at_all", False))


def build_message_payload(notification, config):
    receive_id_type = config.get("receive_id_type", "email")
    if receive_id_type not in RECEIVE_ID_TYPES:
        raise FeishuAppError(f"unsupported receive_id_type: {receive_id_type}")
    msg_type, content = build_content(notification, config)
    payload = {
        "receive_id": config.get("receive_id", ""),
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }
    if notification.dedupe_key:
        identity = json.dumps([config.get("app_id", ""), receive_id_type,
                               config.get("receive_id", ""), notification.dedupe_key])
        payload["uuid"] = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
    return payload


def _post_json(url, payload, headers, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8", **headers},
        method="POST",
    )
    return _request_json(request, timeout)


def _request_json(request, timeout):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise FeishuAppError(f"HTTP {error.code}: {detail[:300]}") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise TransportError(f"request failed: {error}") from error

    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise TransportError(f"invalid response: {raw[:300]}") from error

    if not isinstance(parsed, dict):
        raise TransportError("invalid response: expected a JSON object")

    code = parsed.get("code", parsed.get("StatusCode", 0))
    if code not in (0, None):
        raise FeishuAppError(f"code={code} msg={parsed.get('msg', '')}")
    return parsed


def get_tenant_token(app_id, app_secret, timeout, base_url="https://open.feishu.cn"):
    if not app_id or not app_secret:
        raise FeishuAppError("feishu_app.app_id / app_secret is not configured")
    parsed = _post_json(
        base_url.rstrip("/") + TOKEN_PATH,
        {"app_id": app_id, "app_secret": app_secret},
        {},
        timeout,
    )
    token = parsed.get("tenant_access_token")
    if not token:
        raise FeishuAppError("tenant_access_token missing in response")
    return token


def send(notification, config, logger=None):
    receive_id = config.get("receive_id", "")
    if not receive_id:
        raise FeishuAppError("feishu_app.receive_id is not configured")

    timeout = float(config.get("timeout_seconds", 5))
    base_url = config.get("base_url", "https://open.feishu.cn")
    token = get_tenant_token(
        config.get("app_id", ""),
        config.get("app_secret", ""),
        timeout,
        base_url,
    )
    receive_id_type = config.get("receive_id_type", "email")
    url = f"{base_url.rstrip('/')}{MESSAGE_PATH}?receive_id_type={urllib.parse.quote(receive_id_type)}"
    payload = build_message_payload(notification, config)

    try:
        return _post_json(
            url, payload, {"Authorization": f"Bearer {token}"}, timeout,
        )
    except TransportError as error:
        # Token acquisition errors happen before this block and are safe to retry.
        raise AmbiguousSendError(str(error)) from error


def _submit_json(url, payload, headers, timeout):
    try:
        return _post_json(url, payload, headers, timeout)
    except TransportError as error:
        raise AmbiguousSendError(str(error)) from error


# --------------------------------------------------------------- bridge 回复

REPLY_PATH = "/open-apis/im/v1/messages/{message_id}/reply"


def _token_for(config):
    timeout = float(config.get("timeout_seconds", 5))
    base_url = config.get("base_url", "https://open.feishu.cn")
    # Token acquisition cannot send a message. Retrying it is safe even after
    # a timeout, while an ambiguous message submission must stay single-shot.
    for attempt in range(3):
        try:
            token = get_tenant_token(
                config.get("app_id", ""), config.get("app_secret", ""), timeout, base_url,
            )
            break
        except TransportError:
            if attempt == 2:
                raise
    return token, base_url.rstrip("/"), timeout


def _upload_card_image(image, config):
    timeout = float(config.get("timeout_seconds", 5))
    base_url = config.get("base_url", "https://open.feishu.cn").rstrip("/")
    token = get_tenant_token(config.get("app_id"), config.get("app_secret"), timeout, base_url)
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="notifyhub.png"\r\n'
        f'Content-Type: image/png\r\n\r\n'
    ).encode("ascii") + image + f"\r\n--{boundary}--\r\n".encode("ascii")
    request = urllib.request.Request(base_url + "/open-apis/im/v1/images", data=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}",
    }, method="POST")
    data = _request_json(request, timeout).get("data")
    key = data.get("image_key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key:
        raise FeishuAppError("image_key missing in response")
    return key


def help_logo_key(config, cache_path=None):
    image = (launcher_path().parents[1] / "assets/logo-light.png").read_bytes()
    source = [config.get("base_url", "https://open.feishu.cn").rstrip("/"),
              config.get("app_id"), hashlib.sha256(image).hexdigest()]
    path = Path(cache_path) if cache_path else Path.home() / ".local/state/notifyhub/help-logo.json"
    with state_lock(path):
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        if isinstance(cached, dict) and cached.get("source") == source:
            key = cached.get("image_key")
            if isinstance(key, str) and key:
                return key
        key = _upload_card_image(image, config)
        try:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"source": source, "image_key": key}), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass
        return key


def reply_text(message_id, text, config, dedupe_key=None):
    """Reply to a specific Feishu message (keeps the phone-side thread)."""
    if not message_id:
        raise FeishuAppError("message_id is required to reply")
    token, base_url, timeout = _token_for(config)
    payload = {"msg_type": "text", "content": json.dumps({"text": str(text)}, ensure_ascii=False)}
    if dedupe_key is not None:
        payload["uuid"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"notifyhub-native:{message_id}:{dedupe_key}"))
    return _submit_json(
        base_url + REPLY_PATH.format(message_id=urllib.parse.quote(str(message_id))),
        payload,
        {"Authorization": f"Bearer {token}"},
        timeout,
    )


def send_card_to_chat(chat_id, card, config):
    """Send a card into a chat_id (used for streaming agent output)."""
    if not chat_id:
        raise FeishuAppError("chat_id is required")
    token, base_url, timeout = _token_for(config)
    url = f"{base_url}{MESSAGE_PATH}?receive_id_type=chat_id"
    return _submit_json(
        url,
        {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        {"Authorization": f"Bearer {token}"},
        timeout,
    )


def reply_card(message_id, card, config, dedupe_key='reply'):
    """Reply with a rich card using the same request identity as text replies."""
    if not message_id:
        raise FeishuAppError("message_id is required to reply")
    token, base_url, timeout = _token_for(config)
    payload = {"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False),
               "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"notifyhub-native:{message_id}:{dedupe_key}"))}
    return _submit_json(base_url + REPLY_PATH.format(message_id=urllib.parse.quote(str(message_id))),
                      payload, {"Authorization": f"Bearer {token}"}, timeout)
