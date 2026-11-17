# dsh-notifyhub

DeepSeek Harness (`dsh`) bundle plugin for notifyhub. It listens to the persisted
`session/event` stream, and on `turn/end` sends the session metadata, recent user
prompt, and last assistant message to the notifyhub CLI (`notifyhub hook dsh`).

The payload contains the session title, the last human user message, the last
assistant `text` block, and the model. Reasoning blocks, tool calls, and synthetic
context messages (`runtime-context`, `time-context`, `skill-catalog`,
`model-selection`, `user-approval`) are not included.

## Install

```bash
dsh plugin --profile web add link:/absolute/path/to/notifyhub/integrations/dsh
```

Or from the notifyhub skill:

```bash
python3 /absolute/path/to/notifyhub/scripts/notifyhub.py install-dsh --profile web
```

Restart `dsh web` after installing a bundle. For headless tasks, install into the
`headless` profile as well.

## Configuration

The plugin defaults to the notifyhub launcher next to this package. To override it,
add a config block to the patch row:

```yaml
- insert:
    - id: dsh-notifyhub
      name: dsh-notifyhub
      config:
        command: ["python3", "/absolute/path/to/notifyhub/scripts/notifyhub.py", "hook", "dsh"]
```

Subagent sessions are filtered by notifyhub, not by this plugin. The plugin
always forwards `origin`, and the adapter drops subagent turns unless
`dsh.include_subagents` is `true` in `~/.config/notifyhub/config.json`.
