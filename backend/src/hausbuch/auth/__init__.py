"""Passkey sign-in, sessions, enrollment invites and provisioning (phase 1 step 3, §6, §7).

- lookup: the only place the pre-auth `app.auth_lookup` flag is set (read-only transactions).
- ceremony: the signed, short-lived challenge cookie.
- webauthn: registration and authentication ceremonies over py_webauthn.
- sessions: opaque server-side sessions and the SessionResolver the API uses.
- passkeys, invites: the rows behind them.
- provisioning: workspace + first user as the schema owner (the CLI's path, never the API's).

Every write happens in a workspace_transaction, after the lookup has told us the workspace.
"""
