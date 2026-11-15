"""Phase 6: a bank login's PIN typed in the app and its login name, both encrypted (D196).

Luis, 15 Nov 2026: „Is there any way to input the information in the app without adding the
variable to Scaleway. The app never shows the password.“ — „Encrypted in database“; then
„Maybe the username as well?“

- `bank_credential`: one row per workspace and credential slot (the slot's pattern CHECK of
  0025's bank_connection.credential_slot), holding the slot's online-banking PIN as AES-256-GCM
  ciphertext with its 96-bit nonce and the key version (1: HKDF-SHA256 from the session key,
  info `hausbuch/fints-pin/v1`; the key exists only in the API container, never here). The
  AAD (workspace id and slot) is not stored: it is rebuilt from the row's own columns, so a
  ciphertext copied to another row or workspace does not decrypt. A PIN is a secret, not
  history: setting it again UPDATEs the row and „PIN löschen“ DELETEs it (no append-only
  trigger); the event log records the act without any value (workspace.bank_pin_set and
  workspace.bank_pin_deleted). The app role gets SELECT, INSERT, UPDATE and DELETE.
- `bank_connection` gains `login_nonce`, `login_ciphertext` and `login_key_version` (the
  login name under its own key, info `hausbuch/fints-login/v1`, AAD workspace id and
  connection id), and `login_name` becomes nullable: the migration cannot encrypt (the key is
  not in the database), so the API encrypts every readable login at its start-up and at the
  first sync or change of the connection, nulling the readable column in the same UPDATE. A
  CHECK keeps exactly one of the two forms per row. The app role's table-level UPDATE (0019)
  covers the new columns.

The downgrade is refused while it would drop data or leave a connection without a login: any
bank_credential row, or a connection whose login is stored encrypted only (the database cannot
decrypt it). It lifts FORCE RLS for each check, since the owner has no workspace set, and
restores it (an exception rolls the whole statement back, FORCE included).

Phase 7's spec had moved its step 0 to 0039 (0038's docstring); it moves to 0040.

Revision ID: 0039_bank_credential
Revises: 0038_no_pay_marker
Create Date: 2026-11-15
"""

from alembic import op

revision = "0039_bank_credential"
down_revision = "0038_no_pay_marker"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"

DDL = r"""
CREATE TABLE bank_credential (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    credential_slot  text NOT NULL CHECK (credential_slot ~ '^[a-z][a-z0-9_]{0,31}$'),
    nonce            bytea NOT NULL CHECK (octet_length(nonce) = 12),
    -- AES-GCM: the PIN's bytes plus the 16-byte tag; a PIN is 5 to 64 characters (D196).
    ciphertext       bytea NOT NULL CHECK (octet_length(ciphertext) BETWEEN 17 AND 1024),
    key_version      smallint NOT NULL CHECK (key_version >= 1),
    set_by           uuid,  -- the person who typed it (no FK, as bank_sync.created_by)
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, credential_slot)
);
COMMENT ON TABLE bank_credential IS
    'D196: a credential slot''s online-banking PIN typed in the app, AES-256-GCM under a key '
    'derived from the session key (never stored here); AAD = workspace id + slot. Replaced in '
    'place and deleted by a person: a secret, not history.';

ALTER TABLE bank_credential ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_credential FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON bank_credential
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE bank_connection ALTER COLUMN login_name DROP NOT NULL;
ALTER TABLE bank_connection
    ADD COLUMN login_nonce bytea CHECK (octet_length(login_nonce) = 12),
    ADD COLUMN login_ciphertext bytea
        CHECK (octet_length(login_ciphertext) BETWEEN 17 AND 1024),
    ADD COLUMN login_key_version smallint CHECK (login_key_version >= 1),
    ADD CONSTRAINT bank_connection_login_check CHECK (
        (login_nonce IS NULL) = (login_ciphertext IS NULL)
        AND (login_nonce IS NULL) = (login_key_version IS NULL)
        AND (login_name IS NULL) <> (login_ciphertext IS NULL)
    );
COMMENT ON TABLE bank_connection IS
    'D119, D196: one FinTS connection per account. The PIN is a container secret or a '
    'bank_credential row; the login name is stored encrypted (login_ciphertext, AAD = '
    'workspace id + connection id), login_name only until the API encrypts it; client_data is '
    'deconstruct(including_private=False), MAC''d.';
"""

GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON bank_credential TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""

_DATA_CHECKS = (
    ("bank_credential", "SELECT 1 FROM bank_credential", "a PIN is stored in the app"),
    (
        "bank_connection",
        "SELECT 1 FROM bank_connection WHERE login_name IS NULL",
        "a connection login is stored encrypted only",
    ),
)

REFUSE_IF_DATA = (
    "DO $$\nBEGIN"
    + "".join(
        f"""
    ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
    IF EXISTS ({query}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: {what}'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"""
        for table, query, what in _DATA_CHECKS
    )
    + "\nEND\n$$;"
)

DOWNGRADE_DDL = """
ALTER TABLE bank_connection DROP CONSTRAINT bank_connection_login_check;
ALTER TABLE bank_connection
    DROP COLUMN login_nonce,
    DROP COLUMN login_ciphertext,
    DROP COLUMN login_key_version;
ALTER TABLE bank_connection ALTER COLUMN login_name SET NOT NULL;
COMMENT ON TABLE bank_connection IS
    'D119: one FinTS connection per account. No secret column: the PIN and the product id are '
    'container secrets; client_data is deconstruct(including_private=False), MAC''d.';
DROP TABLE bank_credential;
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(DOWNGRADE_DDL)
