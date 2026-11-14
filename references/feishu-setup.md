# Feishu setup

## 1. Create a self-built app

1. Open the Feishu Open Platform and create a **self-built app**.
2. Enable **Bot** capability.
3. Add the permission `im:message:send_as_bot`.
   - If you need to read the app owner's identity, `application:application:readonly` is also useful.
4. Publish the app to the tenant. A tenant admin may need to approve it.
5. Add the bot to the user's Feishu contacts, or have the user start a chat with it.

No user OAuth flow is required. `notifyhub` uses the app's `tenant_access_token`.

## 2. Credentials

You need:

- `app_id` — starts with `cli_`.
- `app_secret` — treat it as a password.

Keep the secret out of chat and shell history:

```bash
export NOTIFYHUB_FEISHU_APP_SECRET='...'
```

The config file is written with mode `600`.

## 3. Receive targets

`receive_id_type` can be:

| Type | Value | Notes |
|---|---|---|
| `open_id` | `ou_...` | Recommended. Stable per app/user. |
| `chat_id` | `oc_...` | A group chat that contains the bot. |
| `email` | user's tenant email | May fail if the address is not a Feishu tenant user. |
| `user_id` | tenant user id | Depends on tenant configuration. |
| `union_id` | `on_...` | Usually not needed. |

### Find the owner open_id

If the user only provides `app_id` and `app_secret`, try:

```bash
python3 "$SKILL_ROOT/scripts/feishu_probe.py" --app-id cli_xxxxxxxx --owner
```

This calls the app-info endpoint and prints `data.app.owner.owner_id`. It does not send a message. It requires `application:application:readonly`; if permission is missing, use one of the alternatives below.

### Alternatives

- Feishu developer console → app → member/owner info.
- Feishu admin console → user profile → open_id.
- Add the bot to a small group, then use the group `chat_id` (`oc_...`).
- Send the bot a direct message, then resolve the p2p `chat_id` with the messaging API.

Do not guess an open_id. An invalid target returns code `230001`.

## 4. Send a test

```bash
python3 "$SKILL_ROOT/scripts/feishu_probe.py" \
  --app-id cli_xxxxxxxx \
  --send-to ou_xxxxxxxx \
  --receive-id-type open_id
```

Expected output:

```text
sent: message_id=om_... chat_id=oc_...
```

If the user receives it, configure notifyhub:

```bash
python3 "$SKILL_ROOT/scripts/setup_config.py" \
  --app-id cli_xxxxxxxx \
  --app-secret "$NOTIFYHUB_FEISHU_APP_SECRET" \
  --receive-id ou_xxxxxxxx \
  --receive-id-type open_id
```

## 5. Common errors

| Code | Meaning | Fix |
|---|---|---|
| `99991672` | Missing permission | Add `im:message:send_as_bot`, republish the app. |
| `230001` | Invalid receive_id | Use a valid `open_id` or `chat_id`. |
| `99991663` | Invalid app_id/app_secret | Recheck credentials; do not reuse a custom-bot signing secret. |
| `99991661` | Token/request problem | Retry; confirm the tenant token endpoint is reachable. |
| `10003` / HTTP 400 | Invalid parameter | Check `receive_id_type`, JSON content, and `msg_type`. |
| App not visible | Not published / not approved | Publish the app and ask an admin to approve it. |
| Bot cannot DM | Bot capability disabled | Enable Bot capability and republish. |

The app-secret used for the Feishu **app bot** is not the same as the signing secret of a **custom bot webhook**. Keep them separate.
