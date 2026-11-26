# dsh adapter

This local Cordis adapter ships inside the [NotifyHub Skill](../../README.md#quick-start). The Skill's GUI and installer use this directory for dsh notifications and access to the original desktop session. The package keeps its historical name, `dsh-notifyhub`, so existing profiles can reuse the same identity.

- User setup and CLI detection are documented in the [dsh guide](../../references/dsh.md).
- Continuation uses the [NotifyHub gateway](../../README.md#gateway) and the host session APIs. The [continuation reference](../dsh-native-bridge/README.md) describes migration and reliability.
- Run `node integrations/dsh/test.mjs` from the repository root for adapter tests.

Author: oldtommmy. Contributors: OpenAI Codex and Google Antigravity.
