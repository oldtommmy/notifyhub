#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notifyhub.channels.feishu_app import RECEIVE_ID_TYPES, get_tenant_token

TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
APP_PATH = "/open-apis/application/v6/applications/{app_id}?lang=zh_cn"
MESSAGE_PATH = "/open-apis/im/v1/messages"


class ProbeError(RuntimeError):
    pass


def get_json(url, token, timeout):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise ProbeError(f"HTTP {error.code}: {detail[:400]}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProbeError(f"request failed: {error}") from error

    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ProbeError(f"invalid response: {raw[:300]}") from error

    if data.get("code") not in (0, None):
        raise ProbeError(f"code={data.get('code')} msg={data.get('msg', '')}")
    return data


def send_text(base_url, token, receive_id, receive_id_type, text, timeout):
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    url = f"{base_url.rstrip('/')}{MESSAGE_PATH}?receive_id_type={urllib.parse.quote(receive_id_type)}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise ProbeError(f"HTTP {error.code}: {detail[:400]}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProbeError(f"request failed: {error}") from error

    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ProbeError(f"invalid response: {raw[:300]}") from error

    if data.get("code") not in (0, None):
        raise ProbeError(f"code={data.get('code')} msg={data.get('msg', '')}")
    return data


def main():
    parser = argparse.ArgumentParser(description="Validate Feishu app credentials and find a receive target")
    parser.add_argument("--app-id", required=True)
    parser.add_argument(
        "--app-secret",
        default=os.environ.get("NOTIFYHUB_FEISHU_APP_SECRET", ""),
        help="defaults to NOTIFYHUB_FEISHU_APP_SECRET",
    )
    parser.add_argument("--base-url", default="https://open.feishu.cn")
    parser.add_argument("--owner", action="store_true", help="print the app owner/creator open_id")
    parser.add_argument("--send-to", help="send a test message to this receive_id")
    parser.add_argument("--receive-id-type", default="open_id", choices=sorted(RECEIVE_ID_TYPES))
    parser.add_argument("--message", default="notifyhub 测试：应用机器人已经可以给你发消息。")
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()

    if not args.app_secret:
        parser.error("App Secret is required; pass --app-secret or set NOTIFYHUB_FEISHU_APP_SECRET")

    if not args.owner and not args.send_to:
        args.owner = True

    try:
        token = get_tenant_token(args.app_id, args.app_secret, args.timeout, args.base_url)

        if args.owner:
            data = get_json(
                args.base_url.rstrip("/") + APP_PATH.format(app_id=urllib.parse.quote(args.app_id)),
                token,
                args.timeout,
            )
            app = (data.get("data") or {}).get("app") or {}
            owner = (app.get("owner") or {}).get("owner_id") or app.get("creator_id")
            if not owner:
                raise ProbeError("owner open_id not present in the app info response")
            print(f"app_name: {app.get('app_name', '')}")
            print(f"open_id: {owner}")

        if args.send_to:
            data = send_text(
                args.base_url,
                token,
                args.send_to,
                args.receive_id_type,
                args.message,
                args.timeout,
            )
            result = data.get("data") or {}
            print(f"sent: message_id={result.get('message_id', '')} chat_id={result.get('chat_id', '')}")

    except ProbeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
