---
name: notifyhub
description: Install and use NotifyHub for agent completion notifications, Feishu desktop continuation, and the local settings GUI. Use for notifyhub、管理界面、飞书机器人配置、完成通知、通知 hook、发件箱、版本更新、剩余额度、飞书续聊、会话卡片和按钮切换.
metadata:
  version: "0.9.2"
---

# NotifyHub

Resolve `SKILL_ROOT` to the directory containing this file. Use its bundled scripts. One Skill provides installation, configuration, and updates for all hosts. For dsh, the Skill provisions the local adapter that receives desktop session events and submits to the owning conversation.

## Choose the operation

- For setup, settings or status, open the GUI first. Use **通知渠道**, **Agent 接入**, **飞书续聊** or **运行与更新** for the requested task. [GUI controls and advanced settings](references/config.md#gui-coverage)
- For **检查更新** or **更新 NotifyHub**, check the version of the installation the user actually runs, then follow [Update the Skill](#update-the-skill).
- For missing notifications or duplicates, identify the installed launcher, configuration and delivery record. [Troubleshooting](references/troubleshooting.md)
- For desktop continuation, choose the independent gateway. Detect an existing dsh-hosted receiver before migrating to keep one Feishu receiver per bot. [Native setup and migration](integrations/dsh-native-bridge/README.md#migrate-gateway)

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui
```

## First notification

1. Check Python 3.10+ and the requested Agent. Start the GUI while the user creates the Feishu application.
2. Guide the user to import the complete [bot-config.json](bot-config.json) in one batch. The preset includes app and user scopes. Keep the smaller presets available for users who request a reduced scope. [Feishu console walkthrough](references/feishu-setup.md)
3. Have the user enter the App Secret locally. With App ID and App Secret filled, the GUI discovers the application owner's `open_id` if the receiver is empty. Use a verified manual recipient if needed. Preserve an existing webhook or channel when the user prefers it.
4. In **通知渠道**, enable the selected channel, save and test it. The test sends a real message to this channel's configured recipient. Check the destination before running it for the user.
5. Use **Agent 接入 → 总览** to check each host's installed hook or adapter and saved notification switch. Open its **设置** or Agent tab to install the integration. Expand **高级通知设置** for character limits and host-specific events. Codex requires trust in `/hooks` or Desktop Settings → Hooks. Changed Codex hook definitions may need trust again. Restart hosts that load hooks at startup.

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target codex
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target claude
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target commandcode
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target antigravity
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile desktop
python3 "$SKILL_ROOT/scripts/notifyhub.py" doctor
```

Run the commands for the selected hosts. dsh Desktop uses the `desktop` profile and dsh Web uses `web`. If dsh detection fails, first check the desktop-bundled CLI and PATH. If missing, check Node.js/npm and help the user install the official `@deepseek-ai/dsh` CLI. Verify `dsh --version` before proceeding. [dsh CLI and migration](references/dsh.md#missing-cli)

## Feishu continuation

1. Use a Python environment containing `lark-oapi>=1.8,<2`. In **飞书续聊**, save the enabled engines and verified `open_id` allowlist, then start the independent gateway. Expand **凭据与适配器** to configure the relevant dsh or Antigravity adapter. For Codex, keep the target chat open and check CLI support for the queue and turn history APIs. Restart desktop adapters after owned work finishes.
2. Add **接收消息** `im.message.receive_v1` in **事件配置**, using the long connection. Permission import and event subscription are separate settings. Follow the console prompts for publishing. [Message event setup](references/feishu-setup.md#message-events)
3. Continuation buttons are on by default. Add **卡片回传交互** `card.action.trigger` in **回调配置**, also using the long connection. Check **在续聊卡片中显示操作按钮** in the GUI. If you change the switch, save and reload the gateway or restart the dsh host. Verify with a **new** Feishu **帮助** or **会话** card. [Callback setup and screenshots](references/feishu-setup.md#card-callbacks)
4. Bind a session and send one small test prompt to the original desktop chat. Confirm the expected one result in Feishu and the corresponding turn in the desktop. Existing permissions, approvals, model and workspace are owned by the original chat.

Buttons work on continuation menu, session and result cards. The user can always select a recently listed session by sending its row number or `/attach 2`. Requests with uncertain submission or delivery outcomes require checking the existing ledger and original chat before acting.

### Feishu commands

- **帮助**、**命令**、**菜单**、`help`、`command`、`/help`、`/command` open the menu.
- **会话** or `/sessions` lists sessions. Use **DSH会话**、**Codex会话** or **Antigravity会话** to switch engines.
- Click **继续此会话** or reply with a row number. Use **上一页**、**下一页** and **状态** to navigate.
- After binding, ordinary text goes to the original conversation. Use **切回此会话** on result cards. Refresh old signed buttons after a gateway restart.
- Desktop continuation accepts up to 5,000 characters per prompt by default. Click **显示完整回答** when the 3,000-character preview cuts off the answer. Long answers can be read page by page in the same card.

## Update the Skill

1. Resolve the path used by the current GUI, hook or gateway before reporting its version. The Skill and a separately installed gateway may run from different directories. Open **运行与更新 → 检查更新**, or run:

   ```bash
   python3 "$SKILL_ROOT/scripts/notifyhub.py" --version
   python3 "$SKILL_ROOT/scripts/notifyhub.py" update
   ```

2. Report the installed version and any newer installable Skill release with its release notes. A check-only request ends here. When GitHub is unavailable, report the check status and keep the existing installation.
3. When the user wants to update, choose the route for the active installation.
   - **Installed Skill**. Use **安装更新** in the GUI or run `python3 "$SKILL_ROOT/scripts/notifyhub.py" update --apply`. The updater verifies SHA-256 and reports the previous Skill's backup location.
   - **Git checkout**. Check the current branch and working tree. Update the requested branch through Git while preserving local work.
   - **Pre-updater installation**. From the desired release or checkout, run `install-skill --skills-dir` with the existing Skill directory's parent. This preserves the install path referenced by existing hooks.
4. Reopen the GUI from the updated path. For a running independent gateway, use that Skill's **飞书续聊 → 重载网关** or restart the foreground gateway from the same Python environment. Restart a desktop host after its active work finishes if it needs to load an updated adapter. Verify the launcher path and version again, especially when the gateway previously used a separate runtime. [User update instructions](README.md#updates)

## Verify and troubleshoot

- Confirm which launcher the host or launch service actually runs. Update the working installation the user selected.
- Check the outbox in **运行与更新** or use `flush --status` for individual completion notifications. Continuation keeps separate request and delivery ledgers.
- Logs live under `~/.local/state/notifyhub/` by default. Summarize diagnostics without exposing credentials or prompts. Preserve ledgers and trusted hook definitions when migrating.
- **99991672** points to missing Feishu API scopes. **230001** calls for verifying recipient ID and type. For absent buttons, open a fresh continuation card and verify the separate callback subscription and GUI switch.
- Run `sh "$SKILL_ROOT/scripts/selftest.sh"` for Python and Node regression checks where applicable.
