---
name: notifyhub
description: Configure and troubleshoot Feishu completion notifications for Codex, DeepSeek Harness (dsh), Claude Code and Command Code, or native Feishu continuation of dsh conversations. Use for notifyhub、完成通知、飞书机器人配置、通知 hook、剩余额度通知、飞书续聊 dsh、会话卡片和按钮切换. Supports local configuration, permission presets, hook/plugin installation and end-to-end checks.
metadata:
  version: "0.9.2"
---

# notifyhub

Resolve `SKILL_ROOT` to this directory and use its bundled scripts. Respect the user's selected scope: completion notifications and native dsh continuation have different installation paths. Read the existing config and installed versions before changing a working setup; do not expose credentials when inspecting them.

## Choose the route

| Request | Route | Read when needed |
|---|---|---|
| Completion notifications | Python CLI + corresponding hook or notification plugin | [Feishu setup](references/feishu-setup.md), [configuration](references/config.md) |
| Feishu continues a dsh conversation | Native plugin inside the desktop dsh host | [Native bridge installation and limits](integrations/dsh-native-bridge/README.md) |
| Delivery failure or duplicates | Identify the actual launcher/version, state file and sender before changing retry behavior | [Troubleshooting](references/troubleshooting.md) |

Default to Feishu app-bot private messages. Preserve a working webhook or other channel when that is the user's preference. Native continuation is experimental; the desktop host must remain running and the Mac awake/online. It is separate from the legacy `bridge start` headless implementation.

## Completion notification workflow

1. Check Python (3.10+, recommend 3.12), the requested agent and existing hooks. Codex is required only for its own integration.
2. Guide Feishu setup using [references/feishu-setup.md](references/feishu-setup.md). For notifications, use [assets/feishu-notify-scopes.json](assets/feishu-notify-scopes.json). The repository's `bot-config.json` is a broad reference export, not the required permission set; current flows use app identity and do not require user OAuth permissions.
3. Let the user fill App ID/Secret in the local GUI or environment. Never ask them to paste App Secret into chat, print it, or put its literal value in a displayed command.
4. Discover the recipient through the GUI or probe. Optional app-info/chat-list permissions support discovery; a verified manual recipient is also valid. Never guess an `open_id` or reuse one from another app without verifying it.
5. Save config, test delivery, install only the requested hook/plugin, and run `doctor`. Config/hook writers back up their previous files.

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui

# CLI alternative: the user has set NOTIFYHUB_FEISHU_APP_SECRET locally.
python3 "$SKILL_ROOT/scripts/feishu_probe.py" --app-id cli_xxxxxxxx --owner
python3 "$SKILL_ROOT/scripts/setup_config.py" \
  --app-id cli_xxxxxxxx --receive-id ou_xxxxxxxx --receive-id-type open_id
python3 "$SKILL_ROOT/scripts/notifyhub.py" test --channel feishu_app
```

Config defaults to `~/.config/notifyhub/config.json` (mode 600). Probe `--owner` reads identity without sending a message. `test` sends to the configured recipient; confirm its target before testing. A bare `ou_…` is an `open_id`, not a `chat_id`.

### Agent-specific integration

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target claude
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target commandcode
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile desktop
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile web
python3 "$SKILL_ROOT/scripts/notifyhub.py" doctor
```

Choose only the relevant commands. Codex hooks need trust in `/hooks` or Desktop Settings → Hooks. A changed hook definition may need trust again. Claude Code / Command Code need their host restarted and hook checked. The installers merge existing settings; use their CLI rather than replacing settings by hand. Uninstall with `uninstall-hooks --target …`.

DSH Desktop uses the `desktop` profile; `dsh web` uses `web`. Install into the actual profile and restart that host when its current work is finished. Desktop updates may reset a directly configured profile; see [DSH reference](references/dsh.md). Keep installed paths stable because hooks/plugins reference absolute launchers.

Quota is best-effort and cached. Notification delivery must not depend on a successful quota lookup. Adapters exclude reasoning/tool calls and filter dsh subagents by default.

## Native dsh continuation workflow

Read [integrations/dsh-native-bridge/README.md](integrations/dsh-native-bridge/README.md) before installation. It documents the actual configuration and reliability boundaries.

1. Verify the user's dsh version and service interfaces. The implementation was checked against dsh 0.2.2-rc.5. Use `sessionQuery` for the historical main-session catalog and `sessionController.resolveAgent` for cold restoration; send input through the owning agent's native inbox. Do not spawn headless to compete with the desktop writer.
2. Use [assets/feishu-native-scopes.json](assets/feishu-native-scopes.json). Permission import, message event subscription and card callback subscription are separate settings. Subscribe to `im.message.receive_v1` using the outbound long connection. Enable buttons only after `card.action.trigger` is configured for long-connection callbacks.
3. Prepare a dedicated stable runtime directory with `scripts/`, the native plugin and a Python environment containing `lark-oapi>=1.8`. Use a separate config such as `~/.config/notifyhub/native-bridge.json`; back up existing files and preserve notification settings. Avoid linking the live runtime to a checkout that changes branches.
4. Set `bridge.enabled=true`, `bridge.default_agent="dsh"`, and the verified owner's `open_id` in `bridge.allow_open_ids`. Empty allowlists reject everyone. `bridge.card_actions` defaults false; keep numeric selection usable without callbacks.
5. Install `dsh-notifyhub-native-bridge` into the desktop profile and apply its documented command array. `install-dsh` installs the ordinary notifier, not this separate native plugin. Its child command ends with `bridge native-transport` and uses the dedicated config.
6. Ensure the ordinary dsh notification plugin includes the phone-owned-turn exclusion from the same release. Do not run native and legacy bridges simultaneously; they share a singleton guard and otherwise compete for events.
7. Restart dsh when its current task is finished. Verify: 会话 → select/bind a session → send a small prompt → one Feishu result and the same turn in the original desktop history. Check historical restoration and paging separately; report what has actually passed.

The native bridge preserves the original session's permissions, model and cwd. Approvals still require the desktop UI; do not globally disable approval to make phone interaction appear unattended. One outstanding phone request per session is currently allowed. Buttons expire after 15 minutes or a transport restart; request a new list. Unknown submission/delivery outcomes must not trigger a blind rerun. Preserve the durable ledger when diagnosing stuck requests.

## Diagnostics and boundaries

- Identify whether the active path is a notification hook, ordinary dsh notifier, native continuation, or legacy headless bridge. A repository fix does not prove the installed copy has changed.
- Main notification log: `~/.local/state/notifyhub/notifyhub.log`; ordinary dsh plugin log: `dsh-plugin.log` in the same directory. Native ledger: `native-bridge.json` in that state directory. Inspect summaries rather than printing prompts, answers or credentials unnecessarily.
- A timeout after message submission means delivery may have succeeded. Use event deduplication and stable Feishu request UUIDs; do not bypass seen-state during outbox replay or requeue ambiguous submissions automatically.
- `99991672`: inspect missing API scopes and their publication status. `230001`: verify the recipient id and type. Card callback failures require checking the separate callback subscription, not merely message permissions.
- If the installed package is incomplete, update the matched scripts, plugin and referenced docs together. Preserve config, ledgers, trusted-hook definitions and other user settings. Do not stop an active dsh task merely to load an update.

## Verification

```bash
sh "$SKILL_ROOT/scripts/selftest.sh"
```

Use a supported Python interpreter in the active environment. The self-test includes Python and Node plugin checks. For an installation, verify the requested real delivery flow as well; distinguish automated checks from user-confirmed end-to-end acceptance.
