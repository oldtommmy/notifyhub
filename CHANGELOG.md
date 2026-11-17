# Changelog

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
- MIT license and a CI workflow for Ubuntu, macOS, and Windows (Python 3.10 and
  3.13, Node 22).

### Fixed

- DSH subagent filtering is controlled by `dsh.include_subagents`; the plugin no
  longer drops subagent turns before the adapter sees them.
- DSH cards no longer send model reasoning as the body, and the recent prompt no
  longer picks up runtime/time/model notices.
- The Codex hook command uses `list2cmdline` on Windows while POSIX output stays
  unchanged.
- A missing or unwritable log file falls back to stderr instead of crashing the
  CLI.