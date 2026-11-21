# Native Feishu continuation (experimental)

Runs **inside the desktop dsh host**, using `ctx.agents.get(id).followup()`.
The Python child only handles Feishu WebSocket I/O; it never runs headless,
resumes a competing writer, changes providers, or copies conversation history.
The desktop host must remain running and the Mac must stay awake and online.

Verified against the bundled service source of dsh **0.2.2-rc.5**. Real-host
compatibility and end-to-end phone testing are separate from the mock tests.

## Installation

1. Install Python 3.12 and `lark-oapi>=1.8` in a dedicated virtual environment.
2. Copy `scripts/` and this plugin to a stable runtime directory. Do not point
   the installed plugin at a checkout that switches branches.
3. Prepare a separate notifyhub config with `bridge.enabled=true`, a verified
   owner `open_id` in `bridge.allow_open_ids`, `bridge.default_agent="dsh"`, and
   the existing Feishu app credentials. Keep it mode 600. Configure the Feishu
   application to subscribe to `im.message.receive_v1`, with single-chat read
   permission, and publish the application version.
4. Install this bundle with `dsh plugin --profile desktop add link:/absolute/plugin`.
   In the desktop profile patch, add:

```yaml
- id: notifyhub-native-bridge
  config:
    enabled: true
    command:
      - /absolute/runtime/.venv/bin/python
      - /absolute/runtime/scripts/notifyhub.py
      - --config
      - /absolute/native-bridge.json
      - bridge
      - native-transport
```

5. Update the ordinary `dsh-notifyhub/lib/index.js` with the native-turn exclusion
   in this branch. Existing notification Python code can stay on the stable
   notification release. Restart dsh when its current work has finished.

The transport uses the same singleton lock as the old bridge. Do not run both.
The bundle defaults to **disabled**; installing it alone does not enable remote
execution. Credentials stay in Python config, never in the host protocol.

## Phone flow

- `会话` / `切换会话` / `/sessions`: shows a formatted session picker; reply with a number within five minutes to switch.
- `/sessions`: lists **loaded main sessions in this host**, with title and cwd.
- `/attach 2`: binds this Feishu chat to the selected original session.
- Plain text: submits a normal user message to that same agent's next-turn inbox.
- `/status`: shows the binding and whether the host still has that agent loaded.

If the desktop is running a turn, the phone message waits in dsh's native inbox.
The first version allows **one outstanding phone request per original session**;
a second is explicitly rejected until completion. Different live sessions can
run independently. No `/new`, `/fork`, topics, offline resume, or handoff yet.
Permissions remain those of the original session. If dsh requires approval,
use the desktop approval UI; phone-side approval is not implemented.

## Reliability boundaries

- The durable ledger records the Feishu id and generated dsh input id **before**
  calling `followup`. A crash at this boundary is uncertain; no automatic rerun.
- `agent/inbox/claimed` associates the input id with its actual turn. Only that
  turn's `turn/end` returns a result. Desktop turns and older answers are excluded.
- The ordinary notifier suppresses only turns identified as phone-owned, using
  a shared in-process turn registry. Standalone native-plugin installation does
  not replace the ordinary notifier; update both as described above.
- Results reply to the original Feishu message with a stable per-response UUID.
  Network ambiguity is recorded; there is no blind resend or legacy outbox.
- Ledger and bindings survive reload. Uncertain/pending requests are retained
  and never automatically executed again. Recovery of completed-but-unsent
  results, automatic transport restart, and an operator recovery command are
  future work. A stuck request currently needs manual ledger inspection after
  stopping the plugin; do not erase the ledger to blindly rerun a task.
- Startup requires the owner allowlist and the existing singleton lock. SDK
  callbacks enqueue replies rather than waiting on network I/O.

## Tests

```sh
node integrations/dsh-native-bridge/test.mjs
PYTHONPATH=scripts python3.12 -m unittest discover -s tests
```

Before treating the feature as stable, test on the phone: real-session writeback,
desktop-busy queueing, exactly one result, failed model request, permission wait,
WebSocket disconnect, restart, and repeated Feishu message delivery.

## Local acceptance — 2026-11-21

On dsh Desktop 0.2.2-rc.5, the user verified `/sessions` listed the opened test
session, `/attach` bound it, and a phone prompt produced an answer in both
Feishu and the same desktop conversation. This establishes basic writeback,
not recovery under every failure condition. Busy-desktop attribution, replay,
restart and empty-answer behavior have mock coverage; approval waits and
network/crash recovery still need real-host acceptance.

## Rich replies

Session cards show the conversation name, project, model, live status and the
current binding. Binding/status details include cwd and session id in a subdued
footer. Result cards preserve Markdown and show the session title, outcome, turn
and elapsed time. Selection uses numbered replies and requires no additional
Feishu callback subscription. Buttons can be added once `card.action.trigger` is
configured; this version intentionally emits no callback-dependent controls.
