# Native Feishu continuation (experimental)

Runs **inside the desktop dsh host**, using `ctx.agents.get(id).followup()`.
The Python child handles Feishu WebSocket I/O and can queue prompts to an open
Codex desktop conversation through its local App Server. The dsh continuation
path sends input to the owning desktop agent.
The desktop host must remain running and the Mac must stay awake and online.

Verified against the bundled service source of dsh **0.2.2-rc.5**. Real-host
compatibility and end-to-end phone testing are separate from the mock tests.

## Installation

Preferred: open `python3 scripts/notifyhub.py gui`, save the Feishu credentials,
then use the **飞书续聊** card. Import the complete `bot-config.json`
in the Feishu console. Enter verified allowed open_ids and explicitly enable
continuation after subscribing to messages; enable buttons after adding card
callbacks. Click install/apply, then restart dsh when its current work finishes.
The GUI prepares the Python runtime and configures the unified
`dsh-notifyhub` bundle. Leaving both engine checkboxes unchecked keeps
continuation disabled. Apply the same settings to migrate an older two-plugin
installation while preserving the configs, bindings and ledgers.

### Manual installation

1. Install Python 3.12 and `lark-oapi>=1.8` in a dedicated virtual environment.
2. Prepare a separate config with the Feishu app credentials, an allowed
   verified `open_id`, and the selected engines under `bridge`. Use
   `bridge.enabled=true` and set `bridge.dsh_enabled` /
   `bridge.codex_enabled` independently. Keep the config mode 600.
3. Install the unified [dsh-notifyhub bundle](../dsh/README.md) in the desktop
   profile. Enable its continuation transport in the profile patch:

```yaml
- id: dsh-notifyhub
  config:
    continuation:
      enabled: true
      command:
        - /absolute/runtime/.venv/bin/python
        - /absolute/runtime/scripts/notifyhub.py
        - --config
        - /absolute/native-bridge.json
        - bridge
        - native-transport
```

4. Restart dsh Desktop. For Codex continuation, keep the target Codex Desktop
   chat open and use a local CLI with queue and turn history support
   (0.155.1 verified). Finish approvals in the owning desktop app.

Continuation is disabled by default. Store credentials in the Python config.
Stop the legacy headless bridge before starting the native transport because
they share the singleton lock.

## Phone flow

- `会话` / `切换会话` / `/sessions`: shows a formatted session picker; reply with a number within five minutes to switch.
- `/sessions`: lists live and persisted main sessions through the host session query service, with title and cwd.
- `/attach 2`: binds this Feishu chat to the selected original session.
- Plain text: submits a normal user message to that same agent's next-turn inbox.
- `/status`: shows the binding and whether the host still has that agent loaded.

Historical sessions are restored through the same `sessionController.resolveAgent`
used by the desktop API. Session id, header/cwd, recorded model state and preset
restoration remain host-owned. Listings exclude subagents, sort by last event
time and paginate ten sessions per page. Page-local numbered selection and
exact-id signed buttons both work. A failed restore leaves the binding unchanged.

If the desktop is running a turn, the phone message waits in dsh's native inbox.
The current version allows **one outstanding phone request per original session**;
a second is explicitly rejected until completion. Different live sessions can
run independently. No `/new`, `/fork`, topics, independent-host execution, or handoff yet.
Permissions remain those of the original session. If dsh requires approval,
use the desktop approval UI; phone-side approval is not implemented.

## Reliability boundaries

- The durable ledger records the Feishu id and generated dsh input id **before**
  calling `followup`. A crash at this boundary is uncertain; no automatic rerun.
- `agent/inbox/claimed` associates the input id with its actual turn. Only that
  turn's `turn/end` returns a result. Desktop turns and older answers are excluded.
- The ordinary notifier suppresses only turns identified as phone-owned, using
  a shared in-process turn registry in the unified bundle.
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
Feishu callback subscription. Buttons are available after subscribing to `card.action.trigger` and setting
`bridge.card_actions=true` in the separate native bridge config. The default
remains false so unconfigured applications never show dead controls.

## Optional card buttons

In the Feishu application console, configure callback subscriptions to use the
existing long connection, then add **card.action.trigger** (card interaction).
Save the configuration and publish if the console requires it. Set
`bridge.card_actions=true` in the native bridge's separate config and reload the
host/transport. No inbound HTTP server or public endpoint is required.

Cards show **继续此会话**, **刷新列表**, and **当前会话**. Results additionally
allow returning to the conversation that produced that answer. A callback only
selects/displays sessions; it cannot submit an agent task. The handler verifies
the owner allowlist plus a chat-bound HMAC capability. Buttons expire after
15 minutes or a transport restart; request a fresh session list in that case.
Repeated delivery of one button capability is ignored. The callback immediately
acknowledges receipt; a separate rich reply confirms the actual session switch.
Numbered replies and text commands continue to work when callbacks are disabled.

Button acceptance on 2026-11-21: after subscribing to `card.action.trigger`,
enabling the feature and restarting dsh, the user confirmed that clicking
**继续此会话** returned the named session-switch confirmation card.

### History listing change

Historical discovery now uses `ctx.sessionQuery.listSessions()` and
`inspectSession()` instead of `ctx.agents.list()` alone. Restoration uses
`ctx.sessionController.resolveAgent(id)` rather than a new headless process.
`history.test.mjs` covers main-session filtering, recent-activity ordering,
pagination, cold restoration, live reuse and unchanged binding on restore failure.

History-list/restore changes are deployed locally but **real-host acceptance is
pending**. Later test: restart dsh; send 会话; confirm multiple main sessions and
loaded/history labels; select an unloaded session; send a small continuation
prompt; confirm the answer appears in Feishu and the original desktop history.
If there are more than ten main sessions, test 下一页 and choose an item there.
