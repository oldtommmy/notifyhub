# DeepSeek Harness (dsh)

`notifyhub` ships a bundle plugin at `integrations/dsh/`. It runs inside the
DeepSeek Harness host, listens to the persisted `session/event` stream, and on
`turn/end` calls the bundled notifyhub CLI with a normalized payload. The same
Feishu configuration and card renderer used by Codex are reused.

## Install

DSH Desktop and `dsh web` use different profiles. Install into the one the host
actually uses.

### Desktop app

The DeepSeek Harness Electron app runs the `desktop` profile. Install there:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile desktop
```

Then quit and reopen **DeepSeek Harness.app**. The plugin row is carried by the
bundle patch; `dsh --profile desktop --dump-config` deliberately refuses with
"profile desktop is managed exclusively by the Electron application" — that is
expected, not a failure. Check `~/.local/state/notifyhub/dsh-plugin.log` for
`loaded` after restart.

The app's official install path is **Settings → Plugins** (the app's plugin
manager / `dshmarket`). The CLI `install-dsh --profile desktop` writes the
profile directly and works after a restart, but the app owns that profile and a
major app update may reset it. Prefer the app panel when it accepts the plugin;
use `install-dsh` as the local/development path.

### CLI / web

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

### Windows

The same commands work with `python` instead of `python3`:

```powershell
python "$env:SKILL_ROOT\scripts\notifyhub.py" install-dsh --profile desktop
python "$env:SKILL_ROOT\scripts\notifyhub.py" install-dsh --profile web
```

The plugin defaults to `python` on Windows. If Python is not on `PATH` (or only
the `py` launcher is available), set the plugin row's `config.command` to:

```yaml
command: ["py", "-3", "C:/path/to/notifyhub/scripts/notifyhub.py", "hook", "dsh"]
```

Use forward slashes in the launcher path. The plugin's default launcher
resolution and `spawn` are cross-platform; `windowsHide` is set so no console
window flashes.

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

On Windows use `["py", "-3", "C:/.../notifyhub.py", "hook", "dsh"]` if only the
launcher is available.

## Verify

```bash
# web profile: is the plugin mounted?
dsh --profile web --dump-config | grep -B1 -A2 dsh-notifyhub

# desktop app: --dump-config refuses by design; check the load marker after restart
cat ~/.local/state/notifyhub/dsh-plugin.log

# local plugin payload test
node "$SKILL_ROOT/integrations/dsh/test.mjs"

# normalized adapter preview
printf '%s' '{"session_id":"s1","cwd":"/tmp/demo","title":"帮我跑测试","model":"deepseek-chat","recent_prompt":"再检查一下","last_assistant_message":"**已检查**","status":"completed","seq":5,"origin":"main"}' \
  | python3 "$SKILL_ROOT/scripts/notifyhub.py" hook dsh --dry-run
```

Then run one session in the host. A Feishu card should arrive when the turn ends.
For the desktop app, the plugin log should show `loaded`, then `turn/end`, then
`send`; notifyhub's log should show `feishu_app delivered`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Desktop app no card | It uses the `desktop` profile, not `web`. Install `--profile desktop`, quit and reopen the app. |
| `dsh --profile desktop --dump-config` errors | Expected: the desktop profile is managed by the Electron app. Verify via the app restart + `dsh-plugin.log`. |
| No card after a dsh turn | The bundle needs a restart: stop and start `dsh web` / the desktop app. |
| Plugin not in `--dump-config` (web) | The `link:` path is wrong, or the bundle was not mounted. Re-run `install-dsh` and check `dsh --profile web --dump-config`. |
| `loaded` but no `turn/end` | The host version changed its session event. Check `~/.dsh/sessions/**/session*.jsonl.zstd` for `turn/end` and report the dsh version. |
| `turn/end` but no `send` | The session was treated as a subagent. Set `"include_subagents": true` to include it. |
| `send` but no Feishu card | Check `~/.local/state/notifyhub/notifyhub.log` and the `feishu_app` credentials. |
| Windows spawn failed / Python not found | Set the plugin `config.command` to `["py", "-3", "C:/.../notifyhub.py", "hook", "dsh"]`, or put Python on `PATH`. |
| Duplicate card | The plugin dedupes by `session.id + seq`; check for multiple installed copies in the profile. |
| Old card layout | Restart the dsh host; the plugin calls the current notifyhub code. |
| App update reset the plugin | Reinstall with `install-dsh --profile desktop`; prefer the app's Settings → Plugins when it accepts the plugin. |

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
