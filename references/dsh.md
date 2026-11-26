# DeepSeek Harness

Install the full NotifyHub Skill for dsh, then use the local GUI to configure it. The Skill includes the dsh adapter required to receive desktop lifecycle events and continue the owning session. It uses the same notification settings, state, and release as the other agents.

```bash
python3 scripts/notifyhub.py install-skill --host dsh
python3 ~/.dsh/skills/notifyhub/scripts/notifyhub.py gui
```

The installer prints the resolved location, including when `DSH_HOME` is set. dsh also discovers the shared `~/.agents/skills` directory. [DeepSeek Harness Skill discovery](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.md#local-discovery-priority)

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

If an older Market install is present, apply the adapter from the current Skill to switch its source to the local bundled copy. This keeps the dsh package identity and sessions. For an old dsh-hosted Feishu receiver, use the [gateway migration steps](../integrations/dsh-native-bridge/README.md#migrate-gateway).

For Windows notifications, use `python` instead of `python3`. If only the Windows Python launcher is available, set `NOTIFYHUB_PYTHON` to the actual Python interpreter path so the adapter can spawn it directly.

## Notification content

The adapter reads dsh's persisted `session/event` stream and forwards `turn/end` to the Skill's Python hook. The configuration under `dsh` controls the content and optionally includes subagent turns. The adapter takes the title from the session title or first user message, and filters synthetic messages, reasoning and tool calls. [dsh configuration](config.md#deepseek-harness-notifications)

## Verify

- After restarting the desktop app, inspect `~/.local/state/notifyhub/dsh-plugin.log` for the adapter load event and activity during a turn. For Web, use `dsh --profile web --dump-config` to check the mounted adapter.
- Test the requested notification channel in the GUI. Run a dsh turn and check that exactly one completion notification arrives.
- For continuation, send **DSH会话** in Feishu, bind a session, and send a short prompt. Verify the reply in Feishu and the turn in the same desktop session.
- If an app update resets its profile, reapply the local adapter from the Skill. [Further troubleshooting](troubleshooting.md)
