"""Phase 1 step 3: passkeys, sessions and enrollment invites.

Spec: docs/phases/phase-1-step-3.md §6.2. Follows the 0001 conventions: workspace_id on every
table, UNIQUE (workspace_id, id) and composite FKs (D2), text + CHECK for closed value sets
(D3), forced workspace_isolation RLS.

The one exception to "no workspace set, no rows visible": a SELECT-only policy auth_lookup on
these three tables, true while the transaction-local setting app.auth_lookup = 'on'. Sign-in,
session resolution and invite redemption find the workspace by this lookup, so it cannot be
their input. Policies are permissive (OR'd), so the flag makes every row of these tables
readable, and nothing else: it grants no INSERT, UPDATE or DELETE (those still need
workspace_isolation, which is false while no workspace is set), and no other table has the
policy. Only hausbuch.auth.lookup sets the flag. Public keys and token hashes are not secrets;
raw tokens are never stored (token_hash = SHA-256 of 32 random bytes).

Grants: the app role may write all three tables, and gains INSERT on app_user (an invite
creates the invited person's user row). workspace stays SELECT, UPDATE.

Revision ID: 0003_auth_passkeys
Revises: 0002_cost_basis_evidence
Create Date: 2026-11-05
"""

from alembic import op

revision = "0003_auth_passkeys"
down_revision = "0002_cost_basis_evidence"
branch_labels = None
depends_on = None

# Parents before children. Reversed for downgrade.
TABLES = ["auth_passkey", "auth_session", "auth_invite"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    "auth_passkey": "SELECT, INSERT, UPDATE, DELETE",
    "auth_session": "SELECT, INSERT, UPDATE, DELETE",
    "auth_invite": "SELECT, INSERT, UPDATE, DELETE",
}
# Added to 0001's grant on app_user (SELECT). Never UPDATE or DELETE.
APP_USER_GRANT = "INSERT"

DDL = r"""
CREATE TABLE auth_passkey (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    user_id          uuid NOT NULL,
    -- Global, not per workspace: sign-in looks the credential up before the workspace is known.
    credential_id    bytea NOT NULL UNIQUE
                     CHECK (octet_length(credential_id) BETWEEN 1 AND 1023),
    public_key       bytea NOT NULL CHECK (octet_length(public_key) > 0),
    sign_count       bigint NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
    aaguid           uuid,
    transports       jsonb CHECK (transports IS NULL OR (
                         jsonb_typeof(transports) = 'array'
                         AND NOT jsonb_path_exists(transports, '$[*] ? (@.type() != "string")'))),
    backup_eligible  boolean NOT NULL,
    backed_up        boolean NOT NULL,
    label            text NOT NULL CHECK (length(label) BETWEEN 1 AND 100),
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_used_at     timestamptz,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, user_id) REFERENCES app_user (workspace_id, id)
);
COMMENT ON TABLE auth_passkey IS
    'WebAuthn credentials (passkeys). public_key is the COSE key as returned by py_webauthn.';

CREATE TABLE auth_session (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    user_id          uuid NOT NULL,
    passkey_id       uuid,
    token_hash       bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    idle_expires_at  timestamptz NOT NULL,
    revoked_at       timestamptz,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, user_id) REFERENCES app_user (workspace_id, id),
    FOREIGN KEY (workspace_id, passkey_id) REFERENCES auth_passkey (workspace_id, id)
);
COMMENT ON TABLE auth_session IS
    'Server-side sessions. token_hash = SHA-256 of the cookie token; the token is never stored. '
    'Logout sets revoked_at; expired rows are deleted on the user''s next sign-in.';

CREATE TABLE auth_invite (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    user_id       uuid NOT NULL,
    token_hash    bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    purpose       text NOT NULL DEFAULT 'enrollment' CHECK (purpose IN ('enrollment')),
    created_by    uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, user_id) REFERENCES app_user (workspace_id, id),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id)
);
COMMENT ON TABLE auth_invite IS
    'One-time enrollment links (first user, household member, recovery). token_hash = SHA-256 '
    'of the URL token. created_by NULL = created by the CLI.';
"""


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE POLICY auth_lookup ON {table} FOR SELECT
    USING (current_setting('app.auth_lookup', true) = 'on');
"""


def _if_app_role(body: str) -> str:
    return f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        {body}
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""


def upgrade() -> None:
    assert set(APP_GRANTS) == set(TABLES)
    op.execute(DDL)
    for table in TABLES:
        op.execute(_rls(table))
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    lines.append(f"GRANT {APP_USER_GRANT} ON app_user TO {APP_ROLE};")
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    op.execute(_if_app_role(f"REVOKE {APP_USER_GRANT} ON app_user FROM {APP_ROLE};"))
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
