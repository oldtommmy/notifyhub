---
name: notifyhub
description: Configure and troubleshoot Feishu completion notifications for Codex, DeepSeek Harness (dsh), Claude Code, Command Code and Google Antigravity, or native Feishu continuation of dsh and Codex conversations. Use for notifyhub、完成通知、飞书机器人配置、通知 hook、剩余额度通知、飞书续聊 dsh、飞书续聊 Codex、会话卡片和按钮切换. Supports local configuration, permission presets, hook/plugin installation and end-to-end checks.
metadata:
  version: "0.9.2"
---

# notifyhub

Resolve `SKILL_ROOT` to this directory and use its bundled scripts. Respect the user's selected scope: the unified dsh plugin provides completion notifications and optional desktop continuation; other agents install their own notification hooks. Read the existing config and installed versions before changing a working setup; do not expose credentials when inspecting them.

## Choose the route

| Request | Route | Read when needed |
|---|---|---|
| Completion notifications | Python CLI + corresponding hook or notification plugin | [Feishu setup](references/feishu-setup.md), [configuration](references/config.md) |
| Feishu continues a desktop conversation | Independent NotifyHub gateway with agent adapters, or an existing dsh-hosted installation | [Native bridge installation and limits](integrations/dsh-native-bridge/README.md) |
| Delivery failure or duplicates | Identify the actual launcher/version, state file and sender before changing retry behavior | [Troubleshooting](references/troubleshooting.md) |

Default to Feishu app-bot private messages. Preserve a working webhook or other channel when that is the user's preference. Native continuation is experimental; the desktop host must remain running and the Mac awake/online. It is separate from the legacy `bridge start` headless implementation.

## Completion notification workflow

1. Check Python (3.10+, recommend 3.12), the requested agent and existing hooks. Codex is required only for its own integration.
2. Open the local GUI first (`python3 "$SKILL_ROOT/scripts/notifyhub.py" gui`); keep it running while the user creates the Feishu app and returns to fill credentials. The GUI does not establish the native bridge WebSocket.
3. Guide Feishu setup using [references/feishu-setup.md](references/feishu-setup.md). Default to importing the complete [bot-config.json](bot-config.json) in the Feishu console in one batch, as requested by the maintainer; do not make users search permissions one by one. Keep the smaller assets presets as an optional alternative only when the user requests reduced scope. The full preset includes app and user scopes; importing it does not itself initiate user OAuth.
4. Let the user fill App ID/Secret in the local GUI or environment. Never ask them to paste App Secret into chat, print it, or put its literal value in a displayed command.
5. Use `open_id` for new setups. Once App ID and App Secret are filled, the GUI automatically looks up the app owner when the recipient field is empty. The user can retry with 获取接收人 or enter a verified recipient. Optional app-info/chat-list permissions support discovery; a verified manual recipient is also valid. Never guess an `open_id` or reuse one from another app without verifying it.
6. Save config, test delivery, install only the requested hook/plugin, and run `doctor`. Config/hook writers back up their previous files.

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

If the user selected dsh and CLI detection fails, read [CLI installation guidance](references/dsh.md#missing-cli) and resolve it before continuing: check the desktop-bundled executable/PATH first; otherwise check Node.js/npm requirements, install the official `@deepseek-ai/dsh` CLI, verify `dsh --version`, then install the requested plugin. If Node.js is missing, guide its installation too. Use npx when global installation is unavailable, and distinguish that from having a CLI on PATH. Do not stop with only “dsh not found”, install dsh for unrelated agents, or switch a desktop native workflow to headless.

DSH Desktop uses the `desktop` profile; `dsh web` uses `web`. Install into the actual profile and restart that host when its current work is finished. Desktop updates may reset a directly configured profile; see [DSH reference](references/dsh.md). Keep installed paths stable because hooks/plugins reference absolute launchers.

Quota is best-effort and cached. Notification delivery must not depend on a successful quota lookup. Adapters exclude reasoning/tool calls and filter dsh subagents by default.

## Native desktop continuation workflow

Read [integrations/dsh-native-bridge/README.md](integrations/dsh-native-bridge/README.md) before installation. It documents the actual configuration and reliability boundaries.

1. Verify the user's dsh version and service interfaces. The implementation was checked against dsh 0.2.2-rc.5. Use `sessionQuery` for the historical main-session catalog and `sessionController.resolveAgent` for cold restoration; send input through the owning agent's native inbox. Do not spawn headless to compete with the desktop writer.
2. Guide the user to import the complete [bot-config.json](bot-config.json), the same default used for notifications. Permission import, message event subscription and card callback subscription are separate settings. Subscribe to `im.message.receive_v1` using the outbound long connection. Enable buttons only after `card.action.trigger` is configured for long-connection callbacks.
3. Prefer the **飞书续聊** card in the GUI: save credentials, enter verified allowlist IDs, select dsh, Codex or both after subscribing to messages, and enable card buttons after subscribing to card callbacks, then install/apply. The GUI installs one `dsh-notifyhub` plugin or preserves an existing unified Market installation, prepares its stable Python runtime and separate config, and migrates an older two-plugin desktop installation. Leaving both agents unchecked keeps remote continuation disabled. With the independent gateway installed, Feishu I/O is owned by NotifyHub. Codex connects directly, Antigravity uses its Sidecar, and dsh connects through a local Unix socket adapter. For manual installation, prepare a dedicated stable runtime directory with `scripts/`, the unified plugin and a Python environment containing `lark-oapi>=1.8`. Use a separate config such as `~/.config/notifyhub/native-bridge.json`; back up existing files and preserve notification settings. Avoid linking the live runtime to a checkout that changes branches.
4. Set `bridge.enabled=true`, `bridge.default_agent="dsh"`, and the verified owner's `open_id` in `bridge.allow_open_ids`. Empty allowlists reject everyone. `bridge.card_actions` defaults false; keep numeric selection usable without callbacks.
5. Use the unified `dsh-notifyhub` package from DSH Market, GitHub Releases or the GUI. `install-dsh` installs the bundle with notifications on by default. To enable continuation, apply the GUI settings or configure `continuation` in the desktop profile as documented. Its child command ends with `bridge native-transport`.
6. Keep the legacy headless bridge stopped while the native transport is active. The unified plugin suppresses ordinary dsh notifications for phone-owned turns.
7. Restart dsh when its current task is finished. Verify: 会话 → select/bind a session → send a small prompt → one Feishu result and the same turn in the original desktop history. Check historical restoration and paging separately; report what has actually passed.

The native bridge preserves the original session's permissions, model and cwd. Approvals still require the desktop UI; do not globally disable approval to make phone interaction appear unattended. One outstanding phone request per session is currently allowed. Buttons expire after 15 minutes or a transport restart; request a new list. Unknown submission/delivery outcomes must not trigger a blind rerun. Preserve the durable ledger when diagnosing stuck requests.

For Codex, check that the local CLI supports the queue and turn history APIs (verified on 0.155.1). Keep dsh Desktop running and the target chat open in Codex Desktop. The bridge matches the submitted message to its exact turn and waits for a recorded completion time before sending the result.

## Feishu commands

These are messages sent to the bot in Feishu, not terminal commands. Explain the flow as **list → select → send a prompt**.

- **帮助**, **命令**, **菜单**, `help`, `command`, `/help` or `/command` opens the usage menu. When card callbacks are enabled, the menu has buttons for available engines and the current binding. Use **使用帮助** on a session or result card to reopen it.
- **会话** or `/sessions` lists sessions for the selected agent. **DSH会话** switches to dsh. **Codex会话** switches to Codex when the experimental Codex GUI option is enabled.
- Click **继续此会话**, reply with the row number within five minutes, or send `/attach 2` to select row 2 of the current page.
- **上一页** and **下一页** change pages. **状态** or `/status` shows the current binding.
- Send ordinary text after binding. It goes to the selected agent's original conversation. Use **切回此会话** on an answer card to return to its conversation.

## Antigravity integration

Use `notifyhub.py install-antigravity` to install the Stop hook. With Feishu credentials and a verified allowlist present, add `--continuation` to configure the independent Antigravity-managed sidecar. The independent gateway owns Feishu I/O and uses `gateway.json`. Install its macOS login service with `--config ~/.config/notifyhub/gateway.json install-gateway`. The Antigravity Sidecar only submits input through agentapi. A bot has one active receiver. During migration, set `bridge.gateway_enabled=true` in the dsh adapter config, reload its plugin and stop its old Feishu receiver before starting the gateway. In Antigravity Desktop, complete a turn in the target conversation so it appears in the list. Send **Antigravity会话** or select **Antigravity 会话** in the **帮助** menu. Match lifecycle results using the transcript position before submission and the exact input text. See the [Antigravity section](README.md#google-antigravity实验性).

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
