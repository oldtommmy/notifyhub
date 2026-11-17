# Configuration

Default path: `~/.config/notifyhub/config.json`.
Override with `--config PATH` or `NOTIFYHUB_CONFIG`.

The GUI and `scripts/setup_config.py` preserve unknown keys and back up an existing file before writing.

## Primary channel: Feishu app bot

```json
{
  "channels": {
    "feishu_app": {
      "enabled": true,
      "base_url": "https://open.feishu.cn",
      "app_id": "cli_xxxxxxxx",
      "app_secret": "...",
      "receive_id_type": "open_id",
      "receive_id": "ou_xxxxxxxx",
      "message_type": "card",
      "at_all": false,
      "timeout_seconds": 5
    }
  }
}
```

Use `https://open.larksuite.com` for Lark international.

Cards use Feishu Card JSON 2.0 (`schema: "2.0"`) with the `markdown` component. Feishu client 7.20 or newer is required. The header shows the status and uses the session title as the subtitle. Metadata is a field grid: recent user prompt, model / remaining quota, then goal. The last assistant message is rendered as markdown.

## Codex notifications

```json
{
  "codex": {
    "enabled": true,
    "events": ["Stop"],
    "notify_goal_rounds": true,
    "include_last_message": true,
    "include_remaining_quota": true,
    "max_message_chars": 600,
    "include_session_id": false
  }
}
```

- `events` — hook events to install. `Stop` is the primary event. Other options: `Interrupt`, `SubagentStop`, `SessionEnd`. `SessionEnd` waits for close or 30 minutes of idleness.
- `notify_goal_rounds` — send a notification for every goal continuation turn.
- `include_last_message` — include the last assistant message, truncated by `max_message_chars`.
- `include_remaining_quota` — add the remaining Codex quota.
- `include_recent_prompt` — include the most recent user prompt, truncated by `max_prompt_chars`.
- `max_prompt_chars` — default 160.
- `include_session_id` — advanced; append the session id.

The recent prompt is read from `~/.codex/history.jsonl` by `session_id`. If history is unavailable, it falls back to the thread `preview` / first user message.

Quota comes from Codex app-server `account/rateLimits/read`. It is cached at `~/.local/state/notifyhub/quota.json` for 10 minutes.

## DeepSeek Harness notifications

```json
{
  "dsh": {
    "enabled": true,
    "include_last_message": true,
    "include_recent_prompt": true,
    "include_subagents": false,
    "include_session_id": false,
    "max_message_chars": 600,
    "max_prompt_chars": 160
  }
}
```

- `include_subagents` — notify sessions spawned by subagents. Off by default. This is the only switch: the dsh plugin forwards every `turn/end` with `origin`, and the notifyhub adapter drops subagent sessions here unless enabled.
- `include_last_message` / `include_recent_prompt` — same meaning as the Codex fields.
- `max_*_chars` — truncation limits for the notification body.

The dsh plugin is installed separately; see `references/dsh.md`.

## Receiver discovery

`channels.feishu_app.receive_id_type` / `receive_id` are normally filled by the GUI's 「自动获取」 action. It resolves the app owner's `open_id` (you), with the owner email and bot chats as fallbacks. The type menu explains each target:

- `open_id` — app owner / the user themselves.
- `email` — tenant user by email.
- `chat_id` — group chat containing the bot.
- `user_id` / `union_id` — tenant / cross-app user ids.

Auto-discovery needs `application:application:readonly`; chat discovery additionally needs `im:chat:readonly`. If either is missing, the GUI shows a warning and the fields remain editable.

## Advanced channels

### Custom bot webhook

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/...",
      "secret": "optional signing secret",
      "message_type": "card",
      "at_all": false
    }
  }
}
```

The signing secret is **not** an app secret. The custom bot can only post to its own group.

### iMessage

```json
{
  "channels": {
    "imessage": {
      "enabled": true,
      "to": "phone number or Apple ID",
      "max_chars": 3000,
      "timeout_seconds": 15
    }
  }
}
```

Requires a logged-in GUI session and macOS Automation permission. Not reliable from SSH or background jobs.

## Filters and quiet hours

```json
{
  "filters": {
    "projects_allow": [],
    "projects_deny": ["secret-*"],
    "quiet_hours": {"enabled": true, "start": "23:00", "end": "08:00"}
  }
}
```

Project matching uses glob patterns against the full `cwd` and the project basename.

## Paths

- State and dedupe: `~/.local/state/notifyhub/state.json`
- Log: `~/.local/state/notifyhub/notifyhub.log`
- Quota cache: `~/.local/state/notifyhub/quota.json`
- Codex hooks: `~/.codex/hooks.json`

## Environment variables

- `NOTIFYHUB_CONFIG` — config path.
- `NOTIFYHUB_FEISHU_APP_ID`
- `NOTIFYHUB_FEISHU_APP_SECRET`
- `NOTIFYHUB_FEISHU_RECEIVE_ID`
- `NOTIFYHUB_FEISHU_RECEIVE_ID_TYPE`
- `NOTIFYHUB_FEISHU_WEBHOOK`
- `NOTIFYHUB_FEISHU_SECRET`
- `NOTIFYHUB_QUOTA_CACHE` — override the quota cache path.

Environment overrides take precedence over the file at runtime. The GUI saves raw placeholders, so a `${ENV_VAR}` in the file is not replaced with the expanded secret.

## Security

- `save_config` sets the file to mode `600`.
- Do not commit `~/.config/notifyhub/config.json`.
- Notification content leaves the machine and is visible to the Feishu tenant.
- Use `projects_deny`, `include_last_message: false`, or a smaller `max_message_chars` for sensitive work.
