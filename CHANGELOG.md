# Changelog

## Unreleased

### Added

- Native dsh Feishu continuation, historical-session discovery/restoration,
  paginated rich cards and authenticated session-selection buttons.
- Separate notification/continuation permission presets and rewritten onboarding
  documentation and Skill instructions. The native bridge remains experimental.

### Fixed

- GUI/CLI desktop plugin installation discovers the desktop-bundled dsh CLI
  even when it is not on PATH. Desktop installs prefer that CLI over a separate
  npm installation; `NOTIFYHUB_DSH_BIN` supports custom locations.

- Outbox replay now respects handled events and does not reset retry counts by
  re-adding a failed replay. Ambiguous outcomes are not automatically requeued.

- Serialize notification dispatch across processes so concurrent hooks cannot
  send the same event twice or overwrite each other's deduplication records.
- Add stable Feishu request UUIDs per event and receiver. Preserve the event key
  when message submission times out or returns an unreadable response, so later
  event redelivery cannot automatically send a second copy. Token acquisition
  failures and explicit API rejections remain retryable.



## 0.9.2

### Documentation

- **`HANDOFF.md` rewritten as a full handover.** The first version was written
  before 0.9.0 removed `bridge.session_conflict` / `bridge.idle_grace_seconds`,
  so it described a conflict probe that no longer exists — a reader following it
  would have looked for settings that are gone. It now also separates the two
  layers (the notification gateway on `main`, the paused bridge on
  `feature/feishu-bridge`), documents that the headless profile needs its own
  provider or every run fails with `MISSING_CREDENTIAL`, and records the state
  this machine was left in after the reset.

## 0.9.1

## 0.9.1

### Documentation

- **`HANDOFF.md`**: the bridge is paused on `feature/feishu-bridge`. This records
  what it is, why each design decision was made (long connection instead of a
  webhook, why a session cannot be resumed concurrently, why an unanswered send
  is never retried), every trap already paid for, and what is still broken —
  chiefly that `codex exec` fails on this machine's `~/.codex/config.toml`.

## 0.9.0

## 0.8.1

### Fixed

- **Duplicate result cards after a network hiccup.** A read timeout, a reset
  connection, or an EOF mid-handshake means no answer came back — not that
  Feishu rejected the message. The send loop retried all of them, so a dropped
  proxy connection could post the same card twice. Such failures now raise
  `AmbiguousSendError` and are never retried; definite rejections (HTTP error
  or non-zero `code`) still are, because those provably did not land.
- **Fewer messages per turn.** `reply.ack` is off by default: a bare message
  already answers with a result card, so the "收到，已交给 dsh 处理" line just
  doubled the traffic. Turn it back on if a run takes long enough that you want
  confirmation it was picked up. Commands always reply. The undocumented
  `reply.on_start` knob (never read by any code) is gone.

## 0.8.0

## 0.9.0

### Fixed

- **A session that is merely open in the desktop app is now resumable.** The
  old conflict probe had two wrong assumptions. First, it treated the presence
  of `session.lock` as "busy" — but dsh never deletes that file on release (it
  keeps the inode stable for later lockers) and the lock itself is an flock
  the kernel drops when the holder's descriptor closes. Second, the default
  `idle` mode blocked whenever the transcript's mtime was within
  `idle_grace_seconds` (90s), which a desktop session's periodic flush keeps
  refreshing. Together they made the bridge refuse sessions that dsh would
  have happily written: dsh opens existing sessions with a *read* handle and
  only takes the write claim while it is actually appending. The probe now
  checks the flock alone, and `bridge.session_conflict` /
  `bridge.idle_grace_seconds` are gone.
- **A lost race now reads as a retry, not a crash.** If the desktop app grabs
  the write handle between the probe and dsh's write open, the resulting
  `SessionAlreadyOwnedError` is translated into a plain "App 正在写它，稍后再发
  或 /handoff" reply instead of surfacing dsh's internal error text.

### Added

- **`bridge service`: run the bridge under launchd so it outlives the app.**
  A foreground `bridge start` lives in the process group of whatever launched
  it, so quitting the desktop app (Cmd+Q) killed the bridge with it — the
  phone went silent exactly when "close the laptop and keep chatting" was
  supposed to work. `bridge service install` writes a launchd agent
  (`~/Library/LaunchAgents/com.notifyhub.bridge.plist`, mode 600) that starts
  at login, survives the app quitting, and is restarted within seconds after
  a crash (`KeepAlive: {SuccessfulExit: false}`, so an intentional
  `uninstall` is not fought). `bridge service status|restart|uninstall`
  manage it; `bridge logs [-n N] [-f]` reads the log without a terminal.
  launchd inherits no shell environment, so credentials either get frozen into
  the plist at install time (export first) or are read from the machine's own
  config/credential files at start-up.
- **`/handoff`: carry a desktop conversation to the phone without the lock.**
  dsh allows one write handle per session id, so two writers cannot append to
  the same log at once. `/handoff` reads the session's transcript instead — no
  write handle needed — and injects the most recent turns as read-only context
  into a fresh bridge session. Configurable with `bridge.handoff_turns`
  (default 6) and `bridge.handoff_max_chars` (default 12000). The trade-off is
  stated in the reply: what you type on the phone is not written back to the
  desktop session.

### Changed

- `/attach` marks sessions with 🔒 only while the desktop app is *actually
  writing* them, and explains both ways forward (wait a few seconds for a real
  resume, or `/handoff` for a carry-over).

## 0.7.0

## 0.5.0

### Added

- **`/attach`: continue a conversation that already exists on the machine.**
  The bridge used to know only about sessions it started, so a chat opened in
  the Mac app could not be resumed from the phone. `/attach` lists recent dsh
  and codex sessions (title, age, project) and `/attach <n>` binds one to the
  active topic, after which plain messages continue that conversation.
- Session discovery reads `~/.dsh/sessions` and `~/.codex/sessions`, honouring
  `DSH_HOME` / `CODEX_HOME`. Codex titles come from the first real user message,
  skipping injected environment context.

## 0.4.0

### Added

- **Topics: several independent conversations in one Feishu chat.** `/topic <name>`
  switches to a topic, creating it if new; `/topics` lists them; `/del <name>`
  removes one. Sessions are keyed by `(chat, topic, agent)`, so unrelated work no
  longer shares context and dsh/codex keep separate histories per topic.
- `/new` now clears only the active topic, leaving other topics untouched.
- Acknowledgements, result-card titles, and `/status` all name the active topic.
- Existing session files without a topic layer load as the `main` topic.

### Fixed

- The run lock was acquired with `(chat, topic, agent)` but released with
  `(chat, agent)`, so a topic stayed permanently "busy" after its first task.
- The session map could not read back its own new format, losing sessions on
  restart.

## 0.3.0

### Added

- **Feishu remote continue (`notifyhub bridge`).** Send a message to the Feishu
  bot from a phone and resume a local `dsh` or `codex` session; the answer comes
  back as the same Card 2.0 notification. It uses Feishu's **long connection**
  (an outbound WebSocket), so no public IP, port forwarding, tunnel, or VPS relay
  is needed — it works behind NAT and behind Tailscale.
- Commands: bare message uses `bridge.default_agent`; `@dsh` / `@codex` switch
  agent for one message; `/new` drops the context; `/status` reports the mapped
  session; `/help` prints usage.
- `notifyhub bridge check` runs a preflight, `bridge status` shows mapped
  sessions, `bridge owner` prints the `open_id` to allowlist.

### Security

- `bridge.allow_open_ids` is a **deny-by-default allowlist**. An empty list
  rejects everyone; it never means "allow all".
- Authorization is checked before any agent is invoked, and unauthorized senders
  get an explicit refusal (with their `open_id` in the log) so misconfiguration
  is diagnosable.
- The SDK handler runs on the WebSocket receive loop, so it only validates and
  enqueues; agent runs happen on a worker thread and never stall the connection.

### Fixed

- `/new` issued while a run is in flight no longer resurrects the old session
  when that run finishes: session writes carry a generation token.
- A dsh session already locked by the desktop app is refused with a clear message
  instead of risking a corrupted session file.

## 0.2.0

### Added

- **Persistent outbox.** When every enabled channel fails (dead proxy, flaky
  network), the notification is written to `~/.local/state/notifyhub/outbox.json`
  instead of being lost. `notifyhub flush` replays it; a successful hook run also
  drains up to three queued items, so recovery is automatic once connectivity
  returns.
- **Retry classification.** Network errors, timeouts, and 5xx/429 are retried with
  exponential backoff (60s → 1h, default 8 attempts). Permanent errors
  (`99991663` bad credentials, `99991672` missing scope, `230001` invalid
  `receive_id`, …) are marked dead immediately rather than burning the retry
  budget.
- New `outbox` config block (`enabled`, `path`, `max_items`, `max_age_hours`,
  `max_attempts`). Entries older than `max_age_hours` are pruned.
- `notifyhub doctor` now reports outbox backlog, and `flush` supports
  `--status`, `--clear`, `--retry-dead`, and `--limit`.

### Fixed

- An enabled channel with a missing required field (typically
  `channels.feishu.enabled: true` with an empty `webhook_url`) logged an `ERROR`
  on **every** notification, burying real failures. Such a channel is now skipped
  with a single `WARNING`; `doctor` still flags it with `✗`. `channels.feishu`
  now defaults to `enabled: false`.
- Queued notifications are keyed by `dedupe_key`, so replay cannot double-send the
  same turn.
- `events.py` used PEP 604 annotations at module scope, which raised `TypeError`
  on Python 3.9 and broke 11 tests despite the documented 3.10+ requirement.
  Added `from __future__ import annotations`.

## 0.1.0 — 2026-11-18

### Added

- Codex `Stop` hook with goal-round status, remaining quota, recent prompt, and
  the last assistant message.
- DeepSeek Harness `dsh-notifyhub` bundle plugin. It sends the session title, the
  last human prompt, the final assistant `text` block, and the model. Reasoning
  and tool-call blocks are excluded.
- Claude Code and Command Code `Stop` hook adapters. They read the JSONL
  transcript at `transcript_path` and reuse the same Feishu channels.
- Feishu Card 2.0 private-chat delivery, owner `open_id` auto-discovery, a
  loopback GUI, quota cache, dedupe, project filters, and quiet hours.
- An npm-packable `dsh-notifyhub` package with the Python launcher bundled under
  `scripts/`, and a local `install-dsh` for link installs.
- MIT license and a CI workflow for Ubuntu and Windows (Python 3.10 and 3.13,
  Node 22); macOS is covered by the local test run.

### Fixed

- DSH subagent filtering is controlled by `dsh.include_subagents`; the plugin no
  longer drops subagent turns before the adapter sees them.
- DSH cards no longer send model reasoning as the body, and the recent prompt no
  longer picks up runtime/time/model notices.
- The Codex hook command uses `list2cmdline` on Windows while POSIX output stays
  unchanged.
- A missing or unwritable log file falls back to stderr instead of crashing the
  CLI.