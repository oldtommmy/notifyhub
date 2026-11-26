# DeepSeek Harness

Install the NotifyHub Skill for dsh, or use the standalone plugin in DSH Market. Both routes use the same dsh adapter, local settings and notification state. Use the Skill to configure an independent gateway shared by multiple agents.

```bash
python3 scripts/notifyhub.py install-skill --host dsh
python3 ~/.dsh/skills/notifyhub/scripts/notifyhub.py gui
```

The installer prints the resolved location, including when `DSH_HOME` is set. dsh also discovers the shared `~/.agents/skills` directory. [DeepSeek Harness Skill discovery](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.md#local-discovery-priority)

<a id="market-plugin"></a>
## Install or update the Market plugin

Search for **NotifyHub** in [DSH Market](https://awesome-dsh-plugin.com/zh/), or install the latest release directly:

```bash
dsh plugin --profile desktop add https://github.com/oldtommmy/notifyhub/releases/latest/download/dsh-notifyhub.tgz
python3 "${DSH_HOME:-$HOME/.dsh}/profiles/desktop/node_modules/dsh-notifyhub/scripts/notifyhub.py" gui
```

For Web completion notifications, replace `desktop` with `web` in both commands. Configure and test the channel in **通知渠道**, then restart the selected DeepSeek Harness host. For desktop continuation, follow the [independent gateway setup](../README.md#gateway) from the Skill so the gateway launcher stays at a stable path across plugin upgrades.

To update, add the new release's version-specific tarball URL in the same profile, then restart that host. Copy the asset link from [GitHub Releases](https://github.com/oldtommmy/notifyhub/releases). For example:

```bash
dsh plugin --profile desktop add https://github.com/oldtommmy/notifyhub/releases/download/v0.9.3/dsh-notifyhub.tgz
```

<a id="missing-cli"></a>
## Resolve a missing dsh CLI

The installer can discover the CLI bundled with the desktop app. It checks system and user Applications on macOS. Set `NOTIFYHUB_DSH_BIN` to use a custom executable and reopen the GUI after changing the environment.

1. Check `command -v dsh` (Windows: `where dsh`) and `dsh --version`.
2. If dsh Desktop is installed on macOS, verify the bundled CLI:

   ```bash
   "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" --version
   ```

3. If no CLI is available, check Node.js and npm, then install and verify the official CLI. [Node.js downloads](https://nodejs.org/en/download) · [Official dsh CLI package](https://www.npmjs.com/package/@deepseek-ai/dsh)

   ```bash
   node --version
   npm view @deepseek-ai/dsh engines --json
   npm install -g @deepseek-ai/dsh
   dsh --version
   ```

4. When global npm installation is unavailable, `npx @deepseek-ai/dsh --version` verifies the npm entry point. Add an accessible CLI or set `NOTIFYHUB_DSH_BIN` before using the GUI installer.

## Set up the local adapter

In **Agent 接入**, select **dsh** and **桌面版** or **Web**, then install the adapter and restart that host. Use the profile where the user actually runs dsh.

If dsh reports that the desktop profile needs initialization, open the desktop app once, quit it fully, and retry the installer.

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile desktop
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile web
```

For Feishu continuation with the independent gateway, select **DeepSeek Harness** in **飞书续聊** and save. Open **凭据与适配器** and click **配置 dsh 适配器**. Restart dsh Desktop when its current task finishes. The CLI equivalent is:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile desktop --continuation
```

To switch a Market install to the Skill, apply the adapter from the Skill. This keeps the dsh package identity and sessions. For a dsh-hosted Feishu receiver, use the [gateway migration steps](../integrations/dsh-native-bridge/README.md#migrate-gateway).

For Windows notifications, use `python` instead of `python3`. If only the Windows Python launcher is available, set `NOTIFYHUB_PYTHON` to the actual Python interpreter path so the adapter can spawn it directly.

## Notification content

The adapter reads dsh's persisted `session/event` stream and forwards `turn/end` to the Skill's Python hook. The configuration under `dsh` controls the content and optionally includes subagent turns. The adapter takes the title from the session title or first user message, and filters synthetic messages, reasoning and tool calls. [dsh configuration](config.md#deepseek-harness-notifications)

## Verify

- After restarting the desktop app, inspect `~/.local/state/notifyhub/dsh-plugin.log` for the adapter load event and activity during a turn. For Web, use `dsh --profile web --dump-config` to check the mounted adapter.
- Test the requested notification channel in the GUI. Run a dsh turn and check that exactly one completion notification arrives.
- For continuation, send **DSH会话** in Feishu, bind a session, and send a short prompt. Verify the reply in Feishu and the turn in the same desktop session.
- If an app update resets its profile, reapply the local adapter from the Skill. [Further troubleshooting](troubleshooting.md)
