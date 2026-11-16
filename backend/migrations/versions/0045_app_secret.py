"""Phase 6 D260: a third-party provider key or URL typed in the app, stored encrypted.

Luis, 16 Nov 2026: „I want to be able to add the secrets in the app not through scaleway.
Like the api keys etc...“

- `app_secret`: one row per workspace and allowlisted name (the CHECK below: the Anthropic API
  key, the Etherscan key, the Solana and BNB Smart Chain RPC URLs, the Helius key and the
  CoinGecko key; never the database URL, the session key, the IBAN key, the owner URL, the
  FinTS product id or the mail credentials), holding the value as AES-256-GCM ciphertext
  with its 96-bit nonce and the key version (1: HKDF-SHA256 from the session key, info
  `hausbuch/app-secret/v1`; the key exists only in the API container, never here). The AAD
  (workspace id and name) is not stored: it is rebuilt from the row's own columns, so a
  ciphertext copied to another row or workspace does not decrypt. A value is printable ASCII
  of at most 512 characters, so the ciphertext is at most 512 + 16 bytes. A secret, not
  history (as D196's bank_credential): setting it again UPDATEs the row and „Löschen“ DELETEs
  it; the event log records the act without any value (workspace.app_secret_set and
  workspace.app_secret_deleted). The app role gets SELECT, INSERT, UPDATE and DELETE.

The downgrade is refused while a row exists (it would drop a key the app uses); it lifts FORCE
RLS for the check, since the owner has no workspace set, and restores it.

Phase 8's and phase 9's specs had reserved 0045 and 0046; they move on (the orchestrator
renumbers those docs).

Revision ID: 0045_app_secret
Revises: 0044_sync_original_for_questions
Create Date: 2026-11-16
"""

from alembic import op

revision = "0045_app_secret"
down_revision = "0044_sync_original_for_questions"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"

DDL = r"""
CREATE TABLE app_secret (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    name             text NOT NULL CHECK (name IN (
                         'anthropic_api_key',
                         'etherscan_key',
                         'solana_rpc_url',
                         'bsc_rpc_url',
                         'helius_key',
                         'coingecko_key'
                     )),
    nonce            bytea NOT NULL CHECK (octet_length(nonce) = 12),
    -- AES-GCM: the value's bytes (1 to 512 printable ASCII characters) plus the 16-byte tag.
    ciphertext       bytea NOT NULL CHECK (octet_length(ciphertext) BETWEEN 17 AND 528),
    key_version      smallint NOT NULL CHECK (key_version >= 1),
    set_by           uuid,  -- the person who typed it (no FK, as bank_credential.set_by)
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, name)
);
COMMENT ON TABLE app_secret IS
    'D260: a provider key or URL typed in the app, AES-256-GCM under a key derived from the '
    'session key (never stored here); AAD = workspace id + name. Replaced in place and '
    'deleted by a person: a secret, not history.';

ALTER TABLE app_secret ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_secret FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON app_secret
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
"""

GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON app_secret TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""

REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE app_secret NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM app_secret) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a key is stored in the app'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Delete the keys under Einstellungen first; a downgrade never drops data.';
    END IF;
    ALTER TABLE app_secret FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute("DROP TABLE app_secret;")
