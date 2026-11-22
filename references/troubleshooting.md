# Troubleshooting

## The hook never fires

1. Check that the hook is installed:

   ```bash
   cat ~/.codex/hooks.json
   python3 "$SKILL_ROOT/scripts/notifyhub.py" doctor
   ```

2. Trust it in Codex:
   - CLI: `/hooks` → select `notifyhub` → trust.
   - Desktop: Settings → Hooks, or Review hooks in the composer.
3. If the hook definition changed, trust it again. Codex records trust against the definition hash.
4. Check the log:

   ```bash
   tail -n 50 ~/.local/state/notifyhub/notifyhub.log
   ```

5. Confirm the hook command points at an existing file. If the skill moved, rerun:

   ```bash
   python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks
   ```

6. `SessionEnd` is not real-time: it runs on close or after roughly 30 minutes of idleness. Use `Stop` for turn completion.

## Claude Code / Command Code hook never fires

1. Confirm the hook is in the right file: `~/.claude/settings.json` or `~/.commandcode/settings.json`. `install-hooks --target ...` prints the exact path it wrote.
2. Restart the host and run `/hooks` to confirm the notifyhub Stop hook.
3. Check `~/.local/state/notifyhub/notifyhub.log`.
4. If the card body is empty, confirm the payload contains `transcript_path` and that the JSONL file is readable. A missing transcript falls back to `last_assistant_message` / `prompt` in the payload.
5. Only assistant `text` blocks are used; `thinking` and tool calls are intentionally excluded.

## Feishu send fails

A failure on **every** enabled channel now lands in the outbox rather than being
dropped. Check it first:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" flush --status
```

Then retry once the network is back:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" flush
```

| Symptom | Likely cause | Fix |
|---|---|---|
| `feishu.webhook_url is not configured` | Custom-bot channel enabled without a URL | Disable `channels.feishu.enabled` or fill the webhook. As of 0.2.0 this logs one WARNING per send, not an ERROR. |
| `code=99991672` | Missing app permission | Add `im:message:send_as_bot`, republish. Marked dead — fix, then `flush --retry-dead`. |
| `code=230001` | Invalid receive_id | Use a valid `open_id` or `chat_id`; email may not resolve. |
| `code=99991663` | Wrong app_id/app_secret | Recheck; do not use a custom-bot signing secret. |
| `code=99991661` | `tenant_access_token` invalid | Confirm the app is published and not disabled. |
| HTTP 400 | Bad JSON or parameter | Check `receive_id_type`, `msg_type`, and `content`. |
| Nothing arrives | Bot not published, not approved, or chat not started | Publish/approve the app; open a chat with the bot. |
| Handshake timeout / connection refused | Proxy down or network flapping | Queued automatically; replay with `flush`. |

Retry policy: network errors, timeouts, and 5xx/429 back off exponentially (60s
→ 1h, up to `max_attempts`). Permanent errors are marked dead immediately so the
log stays readable. `doctor` shows the current backlog.

The card uses Feishu Card JSON 2.0. If markdown appears as raw text, or the card looks unstyled, confirm the Feishu client is version 7.20 or newer. The GUI preview is a local approximation of Card 2.0, not the Feishu renderer.

Test outside notifyhub first:

```bash
python3 "$SKILL_ROOT/scripts/feishu_probe.py" \
  --app-id cli_xxxxxxxx \
  --send-to ou_xxxxxxxx \
  --receive-id-type open_id
```

## Quota is missing or stale

- Quota is best-effort. Notifications still send if the query fails.
- The cache lives at `~/.local/state/notifyhub/quota.json` and expires after 10 minutes.
- The first refresh can take a few seconds because it starts `codex app-server`.
- Force a refresh in the GUI, or delete the cache and retry.
- If it consistently fails, confirm `codex app-server --listen stdio://` starts and `account/rateLimits/read` returns a `rateLimits` object.

The hook timeout is 20 seconds to cover the first quota refresh plus the Feishu send.

## GUI does not show the latest fields

The GUI reads its HTML at startup. Stop the process and restart:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui
```

The GUI binds to `127.0.0.1` and rejects requests without its random token. If port 8765 is busy, it automatically chooses another port.

## Notifications are duplicated across channels

Check which channels are enabled:

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" doctor
```

`setup_config.py` disables the custom-webhook and iMessage channels by default. Use `--keep-other-channels` only when intentional.

## Secrets and logs

- Do not paste an App Secret into chat or a shell command that appears in the transcript.
- Prefer `NOTIFYHUB_FEISHU_APP_SECRET` or the GUI password field.
- Config files are written with mode `600`.
- The log contains status text and errors, not the App Secret. Avoid enabling verbose secrets in custom hooks.

## Moving or sharing the skill

The installed hook embeds the absolute path to `scripts/notifyhub.py`. If the skill directory moves or is deleted:

```bash
python3 /new/path/scripts/notifyhub.py install-hooks
```

Then re-trust the changed hook in Codex.

### Delivery outcome unknown

If the log says `delivery outcome unknown; suppressing automatic resend`, the
message submission timed out or returned an unreadable response. Feishu may
already have delivered it. notifyhub keeps the event's deduplication key and
does not automatically send that event again, including after a restart. This
is an uncertain outcome, not a confirmed delivery: check the Feishu chat. A
message that never arrived may require manual recovery. Token acquisition
failures occur before message submission and do not suppress later attempts.

Hooks sharing a state file now serialize their read/check/send/save operation
with a file lock. Feishu requests also carry a stable UUID for the same event
and receiver (the API deduplicates matching UUIDs within one hour). The local
state retains its existing bounded window of 1,000 event keys.
