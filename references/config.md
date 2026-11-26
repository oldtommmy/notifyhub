# Configuration

Default path: `~/.config/notifyhub/config.json`.
Override with `--config PATH` or `NOTIFYHUB_CONFIG`.

The GUI and `scripts/setup_config.py` preserve unknown keys and back up an existing file before writing.

<a id="gui-coverage"></a>
## Local GUI coverage

Open with `python3 scripts/notifyhub.py gui`. Use the same `--config PATH` when checking the GUI and CLI for a specific installation.

| Area | Local GUI | Config or CLI |
|---|---|---|
| 通知渠道 | App bot credentials, recipient discovery, group webhook signing, iMessage recipient, channel switches and tests. Expand **接收类型与消息格式** or **群消息选项** for format settings | Base URLs, timeouts and [advanced channel settings](#advanced-channels) |
| Agent 接入 | **总览** shows installed connections and saved notification switches. Each Agent tab offers hook or adapter setup and common content settings. **高级通知设置** includes excerpts, Codex events and dsh subagents | Other events and launcher overrides through the CLI or configuration |
| 飞书续聊 | Engine switches, allowlist, card actions, and gateway service controls on macOS. Use **凭据与适配器** for dsh and Antigravity adapter setup | [Foreground gateway and migration](../README.md#gateway) |
| 运行与更新 | Outbox counts, version checks and verified Skill updates. Use **配置与日志路径** to find local files | Use `flush --status` for entries. Configure [project filters and quiet hours](#filters-and-quiet-hours) in JSON |

The Feishu app notification switch is independent of credential storage and Feishu continuation. Saving a GUI form preserves the app's configured card or text format.

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

## Claude Code notifications

```json
{
  "claude": {
    "enabled": true,
    "events": ["Stop"],
    "include_last_message": true,
    "include_recent_prompt": true,
    "include_session_id": false,
    "max_message_chars": 600,
    "max_prompt_chars": 160
  }
}
```

Install with `python3 scripts/notifyhub.py install-hooks --target claude`. The adapter reads the JSONL file at `transcript_path`: the title comes from `ai-title`, the recent prompt from `last-prompt` or the last real user message, the body from assistant `text` blocks only (`thinking` and tool calls are excluded), and the model from the transcript. The settings file is backed up before every write.

## Command Code notifications

```json
{
  "commandcode": {
    "enabled": true,
    "events": ["Stop"],
    "include_last_message": true,
    "include_recent_prompt": true,
    "include_session_id": false,
    "max_message_chars": 600,
    "max_prompt_chars": 160
  }
}
```

Install with `--target commandcode`; it writes `~/.commandcode/settings.json`. Parsing is the same as Claude Code; if the transcript has no model, the adapter falls back to `~/.commandcode/config.json`.

## Hook targets

`install-hooks --target codex|claude|commandcode` merges the Stop handler into the matching settings file and backs it up. `uninstall-hooks --target ...` removes only notifyhub handlers. Codex keeps using `~/.codex/hooks.json`; the other targets use their own settings files.

## Receiver discovery

`channels.feishu_app.receive_id_type` / `receive_id` can be filled automatically when the app credentials are entered. **获取接收人** looks up the app owner or candidates matching the selected type:

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
- Outbox (failed deliveries): `~/.local/state/notifyhub/outbox.json`
- Codex hooks: `~/.codex/hooks.json`

## Outbox

When every enabled channel fails, the notification is stored so it can be
replayed instead of being lost.

```json
{
  "outbox": {
    "enabled": true,
    "path": "~/.local/state/notifyhub/outbox.json",
    "max_items": 200,
    "max_age_hours": 72,
    "max_attempts": 8
  }
}
```

- `enabled` — `false` disables persistence entirely; failures are only logged.
- `max_items` — newest N entries kept; older ones are dropped with a warning.
- `max_age_hours` — entries older than this are pruned on the next write.
- `max_attempts` — after this many transient failures the entry is marked dead.

`notifyhub flush` retries due entries. A successful hook run also drains up to three. Use the GUI for counts or `flush --status` to inspect individual entries. Replay checks `dedupe_key` against the notification's delivery records.

Permanent errors are never retried: `99991663` (bad credentials), `99991672`
(missing scope), `99991661`, `230001`, `230002`, `230006`, plus configuration
errors such as a missing `receive_id`. Fix the cause, then use
`flush --retry-dead`.

## Bridge (remote continue)

See [bridge.md](bridge.md) for the full guide. Summary of the `bridge` block:

```json
{
  "bridge": {
    "enabled": false,
    "allow_open_ids": [],
    "default_agent": "dsh",
    "workspace": "~",
    "approval_policy": "never",
    "permission_mode": "workspace-write",
    "timeout_seconds": 1800,
    "max_message_chars": 3000,
    "reject_when_busy": true,
    "sessions_path": "~/.local/state/notifyhub/bridge_sessions.json",
    "reply": {"ack": false, "on_finish": true},
    "prefixes": {"dsh": "", "codex": ""}
  }
}
```

- `enabled` — off by default; `bridge start` also refuses unless `--force`.
- `allow_open_ids` — **deny by default**. Empty denies everyone.
- `default_agent` — `dsh` or `codex`; used for messages with no `@agent` prefix.
- `prefixes` — optional custom prefixes per agent, e.g. `"!": "dsh"`.
- `workspace` — the agent's cwd. Point it at a dedicated directory.
- `approval_policy` / `permission_mode` — unattended runs cannot prompt, so
  `never` plus `workspace-write` keeps the blast radius inside `workspace`.
- `reply.ack` — send the "收到，已交给 X 处理" line before the run starts.
  **Off by default**: a bare message already answers with a result card, so the
  ack only doubles the traffic. Turn it on if you want confirmation that the
  message was picked up (useful when a run takes minutes).
  `reply.on_finish` sends the result card. Commands (`/attach`, `/topic`,
  `/new`, `/handoff`) always reply regardless of these flags — there is no
  result card that would tell you whether they worked.

The bridge reuses `channels.feishu_app` (app_id / app_secret / base_url) for both
the long connection and the replies.

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
