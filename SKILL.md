---
name: notifyhub
description: Set up and manage completion notifications for Codex, DeepSeek Harness (dsh), Claude Code, and Command Code through a Feishu app bot (private chat), including app credentials, owner open_id lookup, remaining-quota display, test sends, Stop hook installation for three agents, a dsh bundle plugin, and a local web GUI. Use when the user asks for Codex 完成通知、DeepSeek Harness 通知、Claude Code 通知、Command Code 通知、飞书通知、notifyhub、安装通知 hook、剩余额度通知, or wants a reusable completion-notification setup.
metadata:
  version: "0.1.0"
---

# notifyhub

Set up a local notification gateway that sends a Feishu private message when a Codex turn or goal round finishes, a DeepSeek Harness (`dsh`) session ends, or a Claude Code / Command Code turn ends. Everything ships in `scripts/` and `integrations/dsh/`: a Python CLI, a Feishu app-bot sender, Stop hook installers for three agents, a dsh bundle plugin, and a loopback-only web GUI. No background service is required.

`SKILL_ROOT` is the directory containing this file. Resolve it once and use the bundled launcher for every command:

```bash
SKILL_ROOT=/absolute/path/to/this/skill
python3 "$SKILL_ROOT/scripts/notifyhub.py" --help
```

## What it does

- Reads Codex `Stop` hook payloads from stdin.
- Reads Claude Code / Command Code `Stop` payloads and parses the JSONL transcript at `transcript_path` for the title, last human prompt, final `text` block, and model.
- Adds the session title, the most recent user prompt, model, goal status, and remaining Codex quota.
- Sends a Feishu card through a self-built Feishu app bot.
- Caches quota for 10 minutes so most turns do not start an app-server.
- Merges `~/.codex/hooks.json`, `~/.claude/settings.json`, or `~/.commandcode/settings.json` without touching other hooks; every write is backed up.
- Ships a `dsh-notifyhub` bundle plugin that listens to `session/event` `turn/end` in DeepSeek Harness.
- Auto-discovers the receiver from App ID/Secret: app owner `open_id` first, then owner email or a bot chat.
- Provides a local GUI for credentials, four notification switches, quota, preview, and DSH install.

## Prerequisites

- macOS (the GUI workflow assumes it); Codex CLI/desktop, Claude Code, or Command Code installed.
- Python 3.10 or newer.
- Optional: DeepSeek Harness (`dsh`) installed via `npx @deepseek-ai/dsh web` or a global `dsh` binary.
- A Feishu self-built app with:
  - Bot capability enabled.
  - Permission `im:message:send_as_bot`.
  - Optional `application:application:readonly` for owner `open_id` auto-discovery.
  - Optional `im:chat:readonly` for listing bot chats.
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

Stop if Python is older than 3.10. Codex is only required for the Codex target; Claude Code and Command Code have their own hook targets.

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

In the GUI, you do not need to choose the receiver type manually: fill App ID and App Secret, then click 「自动获取」. It fills the app owner `open_id` (you) by default, and the type menu explains where each option sends: `open_id` → app owner/you, `email` → tenant user, `chat_id` → bot group, `user_id`/`union_id` → tenant/cross-app user ids. If discovery is blocked by permissions, the fields stay editable for a manual fallback.

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

### 6. Verify Codex end to end

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" doctor
```

Then have the user run one Codex turn. A card should arrive with project, session, model, goal status, remaining quota, and the last assistant message.

### 7. DeepSeek Harness (optional)

Check whether `dsh` is available:

```bash
command -v dsh && dsh --version
```

DSH Desktop and `dsh web` use different profiles. Install into the profile the host actually uses:

```bash
# DeepSeek Harness.app (desktop app)
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile desktop

# dsh web / headless
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile web
```

Restart the matching host: quit and reopen DeepSeek Harness.app, or restart `dsh web`. The plugin listens to the persisted `session/event` stream, and on `turn/end` sends the session title, the last human user prompt, the last assistant `text` block (reasoning and tool calls excluded), and the model to the same notifyhub configuration. Subagent sessions are filtered by notifyhub (`dsh.include_subagents`, off by default).

The desktop app's profile is managed by the app. Its official install path is **Settings → Plugins**; `install-dsh --profile desktop` writes the profile directly and works after a restart, but an app update may reset it. On Windows, use `python` instead of `python3`; if the plugin cannot find Python, set the plugin row's `config.command` to `["py", "-3", "C:/.../notifyhub.py", "hook", "dsh"]`.

If `dsh` is missing, the command prints the equivalent `npx @deepseek-ai/dsh plugin ...` command. See `references/dsh.md`.

Plugin diagnostics are appended to `~/.local/state/notifyhub/dsh-plugin.log` (`loaded`, `turn/end`, `send`, spawn errors).

### 8. Claude Code / Command Code (optional)

Install the Stop hook into the agent the user actually runs:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target claude
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target commandcode
```

Restart the host and confirm the hook in `/hooks`. The installer backs up the existing settings and only touches notifyhub handlers. The adapter reads the JSONL transcript at `transcript_path`: the title comes from Claude's `ai-title`, the recent prompt from `last-prompt` or the last real user message, the body from assistant `text` blocks only (`thinking` and tool calls are excluded), and the model from the transcript or `~/.commandcode/config.json`. Remove with `uninstall-hooks --target ...`.

### 9. Optional GUI

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui
```

The GUI binds to `127.0.0.1`, generates a random access token, and exposes only:

- Feishu app credentials and receive target, with automatic owner/chat discovery.
- Goal-round notifications, recent-prompt excerpt, last-message excerpt, remaining-quota display.
- Current quota with refresh.
- Test send, Codex hook install, and DSH plugin install.
- Feishu card preview.

## Decision rules

- Default to the Feishu app-bot private chat.
- If the user already has a working custom-bot webhook and prefers it, use `channels.feishu` from `references/config.md`; do not force a migration.
- If the user has no Feishu app, guide them through creating one. Do not create apps through an API.
- Feishu error `99991672` means the app lacks `im:message:send_as_bot` or an equivalent permission. Tell the user to add it and republish.
- Feishu error `230001` means an invalid `receive_id`. Switch to `open_id` or `chat_id`; email may not resolve.
- If quota lookup fails, notifications still work. The quota line is best-effort.
- For Claude Code use `install-hooks --target claude`; for Command Code use `--target commandcode`. Use `uninstall-hooks --target ...` to remove only notifyhub handlers.
- Never edit `~/.claude/settings.json` or `~/.commandcode/settings.json` by hand when the CLI can merge them; it backs up the file first.
- Never print, echo, or log the App Secret. Avoid putting it in a shell command that will be shown in the transcript; use an environment variable or the GUI.

## Files

- `scripts/notifyhub.py` — CLI launcher.
- `scripts/notifyhub/` — Python package (adapter, Feishu channels, GUI, quota).
- `scripts/setup_config.py` — safe config writer.
- `scripts/feishu_probe.py` — credential validation, owner lookup, test send.
- `scripts/selftest.sh` — bundled Python and dsh plugin tests.
- `integrations/dsh/` — DeepSeek Harness bundle plugin.
- `references/feishu-setup.md` — app creation, permissions, receive targets, error codes.
- `references/config.md` — full config schema and advanced channels.
- `references/dsh.md` — DeepSeek Harness plugin install, events, and troubleshooting.
- `references/troubleshooting.md` — hook trust, logs, quota, common failures.
- `assets/gui-preview.png` — GUI preview.

## Self-test

```bash
sh "$SKILL_ROOT/scripts/selftest.sh"
```

Consumers should review the bundled Python code before trusting the skill: it executes local commands and writes `~/.codex/hooks.json`.
