# Native desktop continuation

The NotifyHub Skill configures a single Feishu gateway and connects to the selected agent's existing desktop session. Codex uses App Server, dsh uses its host-owned adapter, and Antigravity uses a managed Sidecar.

## Installation

Use the **飞书续聊** tab. Save the allowed open IDs and selected agents, then start the independent gateway. Configure the dsh or Antigravity adapter when selected. Use the [setup steps](../../README.md#gateway) and [Feishu console walkthrough](../../references/feishu-setup.md#message-events).

Keep the selected desktop application running and the computer online. The owning session controls the model, workspace and permissions. Complete approval requests in its desktop UI.

<a id="migrate-gateway"></a>
## Migrate a dsh-hosted receiver

1. Install the current NotifyHub Skill and identify the config used by the old receiver. Close dsh after its current work finishes so the old Feishu connection stops.
2. If an independent `gateway.json` already exists, keep its settings. Otherwise copy the old bridge config into `gateway.json` next to `config.json`. For the default paths on macOS/Linux:

   ```bash
   cp -n ~/.config/notifyhub/native-bridge.json ~/.config/notifyhub/gateway.json
   ```

3. Reopen the Skill GUI. Check the Feishu bot, allowlist and active engines in **飞书续聊**. Save the settings and select **配置 dsh 适配器**. This changes the Desktop profile to use the gateway's local socket.
4. Start the independent gateway, reopen dsh and request a fresh session list. Use one Feishu receiver per bot. The local desktop adapter connects to the gateway and uses the existing session library and ledger.

A manually maintained dsh profile can instead point its continuation command to the current Skill:

```yaml
- id: dsh-notifyhub
  config:
    continuation:
      enabled: true
      command:
        - /absolute/venv/bin/python
        - /absolute/notifyhub/scripts/notifyhub.py
        - --config
        - /absolute/gateway.json
        - bridge
        - dsh-adapter
```

For a legacy dsh-hosted setup, the GUI still recognizes its own native configuration and can apply the dsh/Codex switches, white list and card buttons. After applying settings, restart dsh. The legacy transport command ends in `bridge native-transport`.

## Phone interaction

Send **帮助** for usage or **会话** for a session list. Use **DSH会话**, **Codex会话** or **Antigravity会话** to choose an engine. Click **继续此会话**, reply with a row number or use `/attach 2`. After binding, ordinary text goes to the original session.

**最新回答** reads the latest recorded answer in the bound session. **状态** shows observed runtime, queued messages or the most recent recorded turn, depending on what the host exposes. **待回复** shows available pending questions. Use **回复 原文** when replying with text that could be parsed as a command, such as a number or the word “状态”.

The picker shows names, active status, model and workspace where available. DeepSeek Harness lists and restores historical main sessions through `sessionQuery` and `sessionController.resolveAgent`. This was verified against DeepSeek Harness 0.2.2-rc.5. Its continued questions support option buttons and the **回答** syntax shown on the card. Handle foreground questions, Codex and Antigravity blocking choices, and approval requests in the owning desktop app. Codex requires CLI APIs for queueing input and reading turns. Antigravity lists sessions that have produced a Stop event.

For buttons, subscribe to `card.action.trigger` over the Feishu long connection, enable the GUI switch, and reload the receiver. Fetch a fresh menu or session card. A signed button expires after 15 minutes or a transport restart. [Button setup](../../references/feishu-setup.md#card-callbacks)

<a id="text-questions"></a>

## Codex 和 Antigravity 文字问答

在独立网关已运行的情况下，用启动 MCP 的 Python 环境安装 `mcp>=1.9,<2`。下面的路径替换为实际路径，并使用当前独立网关读取的那份 `gateway.json`。合并条目到现有 MCP 配置后，让桌面端重新加载 MCP。

**Codex** 在 `~/.codex/config.toml` 加入以下条目。 [Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp)

```toml
[mcp_servers.notifyhub_questions]
command = "/absolute/venv/bin/python"
args = ["/absolute/notifyhub/scripts/notifyhub.py", "--config", "/absolute/gateway.json", "mcp-questions", "codex"]
tool_timeout_sec = 330
```

**Antigravity** 在 `~/.gemini/config/mcp_config.json` 的 `mcpServers` 内加入 `notifyhub_questions`。 [Antigravity MCP 配置](https://antigravity.google/docs/mcp/)

```json
{
  "mcpServers": {
    "notifyhub_questions": {
      "command": "/absolute/venv/bin/python",
      "args": ["/absolute/notifyhub/scripts/notifyhub.py", "--config", "/absolute/gateway.json", "mcp-questions", "antigravity"],
      "timeoutSeconds": 330,
      "tools": {"ask_feishu": {"eager": true, "background": "off"}}
    }
  }
}
```

在飞书私聊绑定会话后，发 **文字问答 请先问我选择 A、B 还是 C，等我回答后复述所选选项**。题目出现后直接回复 **A**，也可以发 **待回复**重看选项。实际任务同样可用 **文字问答 任务内容**。每道题等待五分钟。超时、切换会话或网关离线时，Agent 可以把选项写在回答中，然后等下一条消息。

此入口适用于由 `ask_feishu` 发出的普通选择题。已弹出的应用原生选择卡和权限审批由原应用处理。

## Delivery behavior

- Each original session accepts one outstanding phone request. The ledger reserves its identity before submitting. A desktop turn is matched to the originating phone input, and an identified phone turn uses the continuation reply path.
- A result replies to the original Feishu message with a stable response ID. Review the ledger and desktop history when the outcome of submission or a delivery attempt is uncertain.
- Antigravity can recover an unattempted result after the gateway restarts for the same bot and a still-authorized sender. Ordinary notification retries use the separate outbox.
- dsh may queue input if its original session is busy. Use the original desktop UI for approvals and check its response history when diagnosing a stalled request.

Run `sh scripts/selftest.sh` from the Skill for automated regression checks. Verify the live chain by binding a fresh test session and confirming the same turn on desktop and the answer in Feishu.
