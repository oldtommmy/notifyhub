# DeepSeek Harness (dsh)

`notifyhub` ships a bundle plugin at `integrations/dsh/`. It runs inside the
DeepSeek Harness host, listens to the persisted `session/event` stream, and on
`turn/end` calls the bundled notifyhub CLI with a normalized payload. The same
Feishu configuration and card renderer used by Codex are reused.

## Install

With `dsh` on `PATH`:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile web
```

Or manually:

```bash
dsh plugin --profile web add link:"$SKILL_ROOT/integrations/dsh"
```

Restart `dsh web` after installing a bundle. For one-off/headless runs, install
the plugin into the headless profile as well:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile headless
```

If `dsh` is not installed, the command prints the equivalent
`npx @deepseek-ai/dsh plugin ...` command.

## What the plugin sends

On `turn/end`, the host plugin reads:

- `session.id`
- `session.header.cwd`
- `session.header.parentSession` / `delegationDepth` (subagent detection)
- the derived messages (first user message as the session title, last user
  message as "最近提问", last assistant message as the markdown body)
- the latest `request/header` model when available
- `turn/end`'s `kind`: `completed`, `error`, `aborted`, `blocked`,
  `max-tokens`, or `interrupted`

It skips subagent sessions by default. The notifyhub adapter maps the status to
a card title and the `dsh` section of the config controls the prompt/message
excerpts.

## Configuration

The plugin uses the same `~/.config/notifyhub/config.json` as Codex. Relevant
section:

```json
{
  "dsh": {
    "enabled": true,
    "include_last_message": true,
    "include_recent_prompt": true,
    "include_subagents": false,
    "include_session_id": false,
    "max_message_chars": 600,
    "max_prompt_chars": 160
  }
}
```

To override the launcher (for example, when the plugin is installed outside the
skill), add a config block to the patch row:

```yaml
- insert:
    - id: dsh-notifyhub
      name: dsh-notifyhub
      config:
        command: ["python3", "/absolute/path/to/notifyhub/scripts/notifyhub.py", "hook", "dsh"]
```

## Verify

```bash
# plugin mounted?
dsh web --dump-config | grep -B1 -A2 dsh-notifyhub

# local plugin payload test
node "$SKILL_ROOT/integrations/dsh/test.mjs"

# normalized adapter preview
printf '%s' '{"session_id":"s1","cwd":"/tmp/demo","title":"帮我跑测试","model":"deepseek-chat","recent_prompt":"再检查一下","last_assistant_message":"**已检查**","status":"completed","seq":5,"origin":"main"}' \
  | python3 "$SKILL_ROOT/scripts/notifyhub.py" hook dsh --dry-run
```

Then run one `dsh web` session. A Feishu card should arrive when the turn ends.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No card after a dsh turn | The bundle needs a restart: stop and start `dsh web`. |
| Plugin not in `--dump-config` | The `link:` path is wrong, or the bundle was not mounted. Re-run `install-dsh` and check `dsh web --dump-config`. |
| Plugin loads but no card | Check the notifyhub launcher path, Python version, and `~/.local/state/notifyhub/notifyhub.log`. |
| Subagent cards | Set `"include_subagents": true` in the `dsh` config, or leave off to ignore subagents. |
| Duplicate card | The plugin dedupes by `session.id + seq`; check for multiple installed copies in the profile. |
| Old card layout | Restart the dsh host; the plugin calls the current notifyhub code. |

## Remove

```bash
dsh plugin --profile web remove dsh-notifyhub
```

The dsh docs note that `remove` does not rewrite the patch layer. If the
`dsh-notifyhub` insert row remains in `$DSH_HOME/profiles/web/cordis.patch.yml`,
remove it manually and restart `dsh web`.

## Security

The plugin is plain ESM with Node built-ins only. It spawns the local notifyhub
launcher and passes session metadata on stdin. It does not read the App Secret
directly, does not open a network port, and does not import React or any WebGL
code. Review `integrations/dsh/lib/index.js` before installing.
