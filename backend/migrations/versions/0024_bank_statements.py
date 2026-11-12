"""Phase 5 step 2b: the bank's own PDF statements, fetched by number over FinTS.

Spec: docs/phases/phase-5.md D154 (5), (7).
- `bank_statement`: one row per statement the bank delivered, tying the stored PDF (a document,
  stored and typed like every original) to its account, its year and its number as the bank
  numbers them, and to the sync run that fetched it (same account: composite FK). Unique per
  account, year and number. Append-only: forbid_mutation on UPDATE, DELETE and TRUNCATE; the app
  role gets SELECT and INSERT only. No column holds the PDF, its base64 or the Quittungscode.
- `bank_connection.fetch_statements boolean NOT NULL DEFAULT false`: the switch „Kontoauszüge als
  PDF abrufen“; the app role's table-level UPDATE on bank_connection (0019) covers it.
The table carries workspace_id, forced row-level security keyed on it, and composite FKs.

The downgrade is refused while it would drop data (as 0019): any bank_statement row, or a
connection with fetch_statements on. It lifts FORCE RLS for each check, since the owner has no
workspace set, then drops the table and the column.

Revision ID: 0024_bank_statements
Revises: 0023_payment_proof
Create Date: 2026-11-12
"""

from alembic import op

revision = "0024_bank_statements"
down_revision = "0023_payment_proof"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"

DDL = """
CREATE TABLE bank_statement (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    account_id    uuid NOT NULL,
    year          smallint NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    number        integer NOT NULL CHECK (number BETWEEN 1 AND 99999),
    document_id   uuid NOT NULL,
    sync_id       uuid NOT NULL,
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    -- One statement per account, year and number, as the bank numbers them.
    UNIQUE (workspace_id, account_id, year, number),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    -- The run that fetched it is the same account's.
    FOREIGN KEY (workspace_id, account_id, sync_id)
        REFERENCES bank_sync (workspace_id, account_id, id)
);
COMMENT ON TABLE bank_statement IS
    'D154: a PDF statement the bank delivered for (year, number) over FinTS (HKEKP). Proof only, '
    'never a source of lines: the camt is. No column holds the PDF or the Quittungscode.';

CREATE TRIGGER bank_statement_append_only BEFORE UPDATE OR DELETE ON bank_statement
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER bank_statement_no_truncate BEFORE TRUNCATE ON bank_statement
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

ALTER TABLE bank_statement ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_statement FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON bank_statement
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE bank_connection ADD COLUMN fetch_statements boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN bank_connection.fetch_statements IS
    'D154 (7): every run of the account also fetches its PDF statements after the camt part. '
    'Changed with account.bank_connection_recorded.';
"""

GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT ON bank_statement TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""

_DATA_CHECKS = (
    ("bank_statement", "SELECT 1 FROM bank_statement", "table bank_statement holds rows"),
    (
        "bank_connection",
        "SELECT 1 FROM bank_connection WHERE fetch_statements",
        "a connection fetches PDF statements",
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


def upgrade() -> None:
    op.execute(DDL)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute("ALTER TABLE bank_connection DROP COLUMN fetch_statements")
    op.execute("DROP TABLE bank_statement")
