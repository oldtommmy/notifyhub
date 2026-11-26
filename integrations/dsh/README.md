# NotifyHub for DeepSeek Harness

Get DeepSeek Harness completion notifications in Feishu and optionally continue the original desktop session. The adapter is available through [DSH Market](https://awesome-dsh-plugin.com/zh/) and is also included in the [NotifyHub Skill](https://github.com/oldtommmy/notifyhub#quick-start).

```sh
dsh plugin --profile desktop add https://github.com/oldtommmy/notifyhub/releases/latest/download/dsh-notifyhub.tgz
python3 "${DSH_HOME:-$HOME/.dsh}/profiles/desktop/node_modules/dsh-notifyhub/scripts/notifyhub.py" gui
```

Use Python 3.10 or later. For Web notifications, use the `web` profile in both commands. Import the complete [bot-config.json](https://github.com/oldtommmy/notifyhub/blob/main/bot-config.json) in the Feishu developer console and save your credentials in the GUI. Restart DeepSeek Harness after applying the settings.

- [Install, update and resolve CLI detection](https://github.com/oldtommmy/notifyhub/blob/main/references/dsh.md#market-plugin)
- [Set up continuation and card buttons](https://github.com/oldtommmy/notifyhub#native-bridge)

Author: oldtommmy. Contributors: OpenAI Codex and Google Antigravity.
