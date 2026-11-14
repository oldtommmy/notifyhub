#!/usr/bin/env python3
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notifyhub.channels.feishu_app import RECEIVE_ID_TYPES
from notifyhub.config import config_path, deep_merge, load_config, save_config
from notifyhub.gui import validate_config


def main():
    parser = argparse.ArgumentParser(description="Write the notifyhub Feishu app-bot config safely")
    parser.add_argument("--app-id", required=True)
    parser.add_argument(
        "--app-secret",
        default=os.environ.get("NOTIFYHUB_FEISHU_APP_SECRET", ""),
        help="defaults to NOTIFYHUB_FEISHU_APP_SECRET",
    )
    parser.add_argument("--receive-id", required=True)
    parser.add_argument("--receive-id-type", default="open_id", choices=sorted(RECEIVE_ID_TYPES))
    parser.add_argument("--base-url", default="https://open.feishu.cn")
    parser.add_argument("--config", help="config path; defaults to ~/.config/notifyhub/config.json")
    parser.add_argument(
        "--keep-other-channels",
        action="store_true",
        help="keep the custom-webhook and iMessage channels enabled",
    )
    args = parser.parse_args()

    if not args.app_secret:
        parser.error("App Secret is required; pass --app-secret or set NOTIFYHUB_FEISHU_APP_SECRET")

    target = Path(args.config).expanduser() if args.config else config_path()
    if target.exists():
        backup = target.with_name(target.name + f".bak-{int(time.time())}")
        shutil.copy2(target, backup)
        print(f"backup: {backup}")

    config = load_config(target, expand=False)
    config = deep_merge(
        config,
        {
            "channels": {
                "feishu_app": {
                    "enabled": True,
                    "base_url": args.base_url,
                    "app_id": args.app_id,
                    "app_secret": args.app_secret,
                    "receive_id_type": args.receive_id_type,
                    "receive_id": args.receive_id,
                    "message_type": "card",
                }
            },
            "codex": {
                "events": ["Stop"],
                "notify_goal_rounds": True,
                "include_last_message": True,
                "include_remaining_quota": True,
            },
        },
    )

    if not args.keep_other_channels:
        config["channels"]["feishu"]["enabled"] = False
        config["channels"]["imessage"]["enabled"] = False

    errors = validate_config(config)
    if errors:
        raise SystemExit("invalid config: " + "; ".join(errors))

    save_config(config, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    print(f"config: {target}")
    print(f"app_id: {args.app_id}")
    print(f"receive: {args.receive_id_type}:{args.receive_id}")
    print("channels: feishu_app" + (" + existing" if args.keep_other_channels else " only"))
    print("next: python3 scripts/notifyhub.py test --channel feishu_app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
