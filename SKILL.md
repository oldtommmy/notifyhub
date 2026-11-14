---
name: notifyhub
description: Set up and manage Codex turn-completion notifications through a Feishu app bot (private chat), including app credentials, owner open_id lookup, remaining-quota display, test sends, Codex Stop hook installation, and a local web GUI. Use when the user asks for Codex 完成通知、飞书通知、notifyhub、安装通知 hook、剩余额度通知, or wants a reusable completion-notification setup for Codex.
metadata:
  version: "0.1.0"
---

# notifyhub

Set up a local notification gateway that sends a Feishu private message when a Codex turn or goal round finishes. Everything ships in `scripts/`: a Python CLI, a Feishu app-bot sender, a Codex `Stop` hook installer, and a loopback-only web GUI. No background service is required.

`SKILL_ROOT` is the directory containing this file. Resolve it once and use the bundled launcher for every command:

```bash
SKILL_ROOT=/absolute/path/to/this/skill
python3 "$SKILL_ROOT/scripts/notifyhub.py" --help
```

## What it does

- Reads Codex `Stop` hook payloads from stdin.
- Adds the session title, the most recent user prompt, model, goal status, and remaining Codex quota.
- Sends a Feishu card through a self-built Feishu app bot.
- Caches quota for 10 minutes so most turns do not start an app-server.
- Merges `~/.codex/hooks.json` without touching the existing `notify` config.
- Provides a local GUI for credentials, four notification switches, quota, and preview.

## Prerequisites

- macOS, with Codex CLI or the desktop app signed in.
- Python 3.10 or newer.
- A Feishu self-built app with:
  - Bot capability enabled.
  - Permission `im:message:send_as_bot`.
  - The app published to the tenant.
- One receive target: the user's `open_id` (recommended), a `chat_id`, or an email that resolves to a tenant user.

Do not ask the user to paste an App Secret into chat. Prefer the environment variable `NOTIFYHUB_FEISHU_APP_SECRET` or the GUI password field.

## Workflow

### 1. Inspect the environment

```bash
uname -s
python3 --version
command -v codex && codex --version
ls -la ~/.codex/hooks.json 2>/dev/null || true
```

Stop if Python is older than 3.10 or Codex is missing. The notification core is portable, but the hook and GUI workflow assumes macOS.

### 2. Validate credentials and find the owner open_id

Ask for the App ID and App Secret. Put the secret in the environment for the rest of the session:

```bash
export NOTIFYHUB_FEISHU_APP_SECRET='...'
python3 "$SKILL_ROOT/scripts/feishu_probe.py" \
  --app-id cli_xxxxxxxx \
  --owner
```

`--owner` reads the app info and prints the owner/creator `open_id`. It does not send a message. If it fails with a permission error, ask the user for one of:

- Their Feishu `open_id` from the admin or developer console.
- A `chat_id` of a group that contains the bot.
- An email that belongs to the tenant.

Never guess or fabricate an `open_id`. Do not print the App Secret.

Verify delivery before configuring:

```bash
python3 "$SKILL_ROOT/scripts/feishu_probe.py" \
  --app-id cli_xxxxxxxx \
  --send-to ou_xxxxxxxx \
  --receive-id-type open_id
```

### 3. Configure

```bash
python3 "$SKILL_ROOT/scripts/setup_config.py" \
  --app-id cli_xxxxxxxx \
  --app-secret "$NOTIFYHUB_FEISHU_APP_SECRET" \
  --receive-id ou_xxxxxxxx \
  --receive-id-type open_id
```

The script:

- Creates or updates `~/.config/notifyhub/config.json`.
- Backs up an existing file.
- Preserves advanced fields.
- Disables the custom-webhook and iMessage channels so only the app bot sends.
- Enables goal-round notifications, the last-message excerpt, and remaining-quota display.
- Sets the config file to mode `600`.

Use `--keep-other-channels` if the user explicitly wants to keep another channel active.

Alternatively, let the user fill the form in the GUI:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui
```

### 4. Test the channel

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" test --channel feishu_app
```

Expected: `feishu_app: ok` and one card in the user's Feishu private chat. If it fails, read `references/troubleshooting.md`.

### 5. Install the Codex Stop hook

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks
```

Then tell the user to trust it:

- CLI: run `/hooks`, select the `notifyhub` Stop hook, and trust it.
- Desktop app: Settings → Hooks, or the composer's Review hooks action.

If the hook was trusted before and its definition changed, trust it again. The hook command embeds the absolute path to `scripts/notifyhub.py`; rerun `install-hooks` if the skill moves.

### 6. Verify end to end

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" doctor
```

Then have the user run one Codex turn. A card should arrive with project, session, model, goal status, remaining quota, and the last assistant message.

### 7. Optional GUI

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui
```

The GUI binds to `127.0.0.1`, generates a random access token, and exposes only:

- Feishu app credentials and receive target.
- Goal-round notifications, recent-prompt excerpt, last-message excerpt, remaining-quota display.
- Current quota with refresh.
- Test send and hook install.
- Feishu card preview.

## Decision rules

- Default to the Feishu app-bot private chat.
- If the user already has a working custom-bot webhook and prefers it, use `channels.feishu` from `references/config.md`; do not force a migration.
- If the user has no Feishu app, guide them through creating one. Do not create apps through an API.
- Feishu error `99991672` means the app lacks `im:message:send_as_bot` or an equivalent permission. Tell the user to add it and republish.
- Feishu error `230001` means an invalid `receive_id`. Switch to `open_id` or `chat_id`; email may not resolve.
- If quota lookup fails, notifications still work. The quota line is best-effort.
- Never print, echo, or log the App Secret. Avoid putting it in a shell command that will be shown in the transcript; use an environment variable or the GUI.

## Files

- `scripts/notifyhub.py` — CLI launcher.
- `scripts/notifyhub/` — Python package (adapter, Feishu channels, GUI, quota).
- `scripts/setup_config.py` — safe config writer.
- `scripts/feishu_probe.py` — credential validation, owner lookup, test send.
- `scripts/selftest.sh` — bundled test suite.
- `references/feishu-setup.md` — app creation, permissions, receive targets, error codes.
- `references/config.md` — full config schema and advanced channels.
- `references/troubleshooting.md` — hook trust, logs, quota, common failures.
- `assets/gui-preview.png` — GUI preview.

## Self-test

```bash
sh "$SKILL_ROOT/scripts/selftest.sh"
```

Consumers should review the bundled Python code before trusting the skill: it executes local commands and writes `~/.codex/hooks.json`.
