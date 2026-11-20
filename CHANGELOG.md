# Changelog

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