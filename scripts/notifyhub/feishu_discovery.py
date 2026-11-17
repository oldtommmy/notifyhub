import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .channels.feishu_app import FeishuAppError, get_tenant_token

APP_PATH = "/open-apis/application/v6/applications/{app_id}?lang=zh_cn"
CHATS_PATH = "/open-apis/im/v1/chats?page_size=50"


def _get_json(url, token, timeout):
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise FeishuAppError(f"HTTP {error.code}: {detail[:300]}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise FeishuAppError(f"request failed: {error}") from error

    try:
        data = json.loads(raw)
    except ValueError as error:
        raise FeishuAppError(f"invalid response: {raw[:300]}") from error

    if data.get("code") not in (0, None):
        raise FeishuAppError(f"code={data.get('code')} msg={data.get('msg', '')}")
    return data


def discover(app_id, app_secret, base_url="https://open.feishu.cn", timeout=8):
    token = get_tenant_token(app_id, app_secret, timeout, base_url)
    base = base_url.rstrip("/")
    candidates = []
    warnings = []
    app_name = ""

    try:
        data = _get_json(base + APP_PATH.format(app_id=quote(app_id)), token, timeout)
        app = (data.get("data") or {}).get("app") or {}
        app_name = app.get("app_name") or ""
        owner = app.get("owner") or {}
        owner_id = owner.get("owner_id") or app.get("creator_id")
        owner_name = owner.get("name") or app_name or "应用所有者"
        if owner_id:
            candidates.append(
                {
                    "type": "open_id",
                    "value": owner_id,
                    "label": owner_name,
                    "detail": "应用所有者",
                }
            )
        email = owner.get("email")
        if email:
            candidates.append(
                {
                    "type": "email",
                    "value": email,
                    "label": owner_name,
                    "detail": "应用所有者邮箱",
                }
            )
    except FeishuAppError as error:
        warnings.append(f"读取应用信息失败：{error}")

    try:
        data = _get_json(base + CHATS_PATH, token, timeout)
        for item in (data.get("data") or {}).get("items", []):
            chat_id = item.get("chat_id")
            if not chat_id:
                continue
            candidates.append(
                {
                    "type": "chat_id",
                    "value": chat_id,
                    "label": item.get("name") or "群聊",
                    "detail": "机器人所在群",
                }
            )
    except FeishuAppError as error:
        warnings.append(f"读取群列表失败：{error}")

    seen = set()
    unique = []
    for candidate in candidates:
        key = (candidate["type"], candidate["value"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return {
        "ok": bool(unique),
        "app_name": app_name,
        "candidates": unique,
        "warnings": warnings,
    }
