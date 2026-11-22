# NotifyHub for DeepSeek Harness

One plugin for Feishu completion cards and optional continuation of dsh and
Codex desktop chats. The plugin sends title, model, prompt and answer in
completion cards. Continuation is opt-in and requires dsh Desktop.

## Install and configure

Search for **NotifyHub** in DSH Market or install the
[release package](https://github.com/oldtommmy/notifyhub/releases/latest):

```sh
dsh plugin --profile desktop add https://github.com/oldtommmy/notifyhub/releases/latest/download/dsh-notifyhub.tgz
```

For notifications only in dsh web, use the `web` profile. Open the NotifyHub
GUI from the checkout or the installed bundle:

```sh
python3 scripts/notifyhub.py gui
# If installed in the default dsh Desktop profile:
python3 ~/.dsh/profiles/desktop/node_modules/dsh-notifyhub/scripts/notifyhub.py gui
```

In the Feishu developer console, import the complete
[bot-config.json](https://github.com/oldtommmy/notifyhub/blob/main/bot-config.json)
and save the bot credentials in the local GUI. For continuation, subscribe to
the message event and optionally the card-action callback over long connection.
Use the GUI's **飞书续聊** card to set the verified `open_id` allowlist and
select dsh, Codex, or both. Apply and restart dsh Desktop. This also migrates
older two-plugin desktop installations.

Start in Feishu with **DSH会话** or **Codex会话**, choose a session and send
your next message. Keep dsh Desktop running. For Codex, also open the target
chat in Codex Desktop and keep a compatible local Codex CLI installed.

See [the full setup guide](https://github.com/oldtommmy/notifyhub#quick-start)
for Feishu setup, commands and troubleshooting. Configuration and conversation
state are stored locally. For a custom Python command, set `NOTIFYHUB_PYTHON`
or override `config.command` in the `dsh-notifyhub` profile row.

## Package development

Run `python3 scripts/build_npm.py` from the repository root. Pack the output
directory so the release includes the Python launcher and the continuation
transport. A local checkout can use `link:/absolute/notifyhub/integrations/dsh`.

Author: oldtommmy. Contributor: OpenAI Codex.
