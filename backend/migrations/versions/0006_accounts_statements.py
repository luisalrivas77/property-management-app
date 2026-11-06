"""Phase 2 step 3: bank accounts, statement imports, transactions and possible duplicates.

Spec: docs/phases/phase-2.md §3.2, §7 (D21, D22). Follows the 0001 conventions: workspace_id on
every table, UNIQUE (workspace_id, id) and composite FKs, text + CHECK for closed value sets,
forced workspace_isolation RLS, explicit hausbuch_app grants.

- account holds the balance anchor (opening_balance on opening_balance_on, D21); the IBAN is
  stored masked plus its keyed HMAC, never in plain text, and is unique per workspace.
- transaction is immutable as imported: no DELETE (not granted, and a trigger), no TRUNCATE,
  and the one UPDATE allowed is setting excluded_as_duplicate_of once, from null, when a
  cross-format duplicate is resolved as merged (trigger transaction_exclude_once, and a
  column grant). (workspace_id, account_id, line_hash) is unique: re-imports add nothing.
  A transaction's import and the line it duplicates are on the same account (composite FKs).
- statement_import CHECKs: a reconciled import's computed closing equals its declared one; an
  import has a declared closing balance exactly when its balance source is not 'none'.

Revision ID: 0006_accounts_statements
Revises: 0005_jobs_ai_proposals
Create Date: 2026-11-06
"""

from alembic import op

revision = "0006_accounts_statements"
down_revision = "0005_jobs_ai_proposals"
branch_labels = None
depends_on = None

# Parents before children. Reversed for downgrade.
TABLES = ["account", "statement_import", "transaction", "possible_duplicate"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    "account": "SELECT, INSERT, UPDATE",
    "statement_import": "SELECT, INSERT, UPDATE",
    # No table-wide UPDATE and never DELETE: see APP_COLUMN_GRANTS.
    "transaction": "SELECT, INSERT",
    "possible_duplicate": "SELECT, INSERT, UPDATE",
}
APP_COLUMN_GRANTS = {"transaction": "UPDATE (excluded_as_duplicate_of)"}

IBAN_MASKED = r"'^[A-Z]{2}[0-9]{2} \*{4} [A-Z0-9]{4}$'"

FUNCTIONS = """
CREATE FUNCTION transaction_exclude_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Immutable as imported. The one change: excluded_as_duplicate_of set once, from null.
    IF (to_jsonb(NEW) - 'excluded_as_duplicate_of')
           IS DISTINCT FROM (to_jsonb(OLD) - 'excluded_as_duplicate_of')
       OR OLD.excluded_as_duplicate_of IS NOT NULL
       OR NEW.excluded_as_duplicate_of IS NULL THEN
        RAISE EXCEPTION 'UPDATE on transaction is not allowed: rows are append-only'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only excluded_as_duplicate_of may be set, once, from null.';
    END IF;
    RETURN NEW;
END
$$;
"""

DDL = rf"""
CREATE TABLE account (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid NOT NULL REFERENCES workspace (id),
    label               text NOT NULL CHECK (length(label) BETWEEN 1 AND 100),
    bank                text NOT NULL CHECK (length(bank) > 0),
    iban_masked         text NOT NULL CHECK (iban_masked ~ {IBAN_MASKED}),
    iban_hmac           bytea NOT NULL CHECK (octet_length(iban_hmac) = 32),
    source              text NOT NULL DEFAULT 'upload'
                        CHECK (source IN ('upload', 'fints', 'aggregator')),
    opening_balance     numeric(14,2) NOT NULL,
    opening_balance_on  date NOT NULL,
    is_owner_account    boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, iban_hmac)
);
COMMENT ON TABLE account IS
    'Bank accounts. The rental account holds the transactions; owner accounts '
    '(is_owner_account) are only recognized by IBAN for owner transfers and hold none.';
COMMENT ON COLUMN account.opening_balance IS
    'Balance anchor (D21): the balance at the end of opening_balance_on.';

CREATE TABLE statement_import (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspace (id),
    account_id        uuid NOT NULL,
    document_id       uuid NOT NULL,
    format            text NOT NULL CHECK (format IN ('csv_camt', 'camt053', 'pdf')),
    lines_total       integer NOT NULL DEFAULT 0 CHECK (lines_total >= 0),
    lines_new         integer NOT NULL DEFAULT 0 CHECK (lines_new >= 0),
    lines_skipped     integer NOT NULL DEFAULT 0 CHECK (lines_skipped >= 0),
    first_booking_on  date,
    last_booking_on   date,
    declared_opening  numeric(14,2),
    declared_closing  numeric(14,2),
    balance_source    text NOT NULL DEFAULT 'none'
                      CHECK (balance_source IN ('file', 'user', 'none')),
    computed_closing  numeric(14,2),
    status            text NOT NULL DEFAULT 'imported' CHECK (status IN (
                          'imported', 'reconciled', 'unreconciled', 'failed')),
    error_code        text,
    ai_call_id        uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    FOREIGN KEY (workspace_id, ai_call_id) REFERENCES ai_call (workspace_id, id),
    CHECK (lines_new + lines_skipped <= lines_total),
    CHECK ((first_booking_on IS NULL) = (last_booking_on IS NULL)),
    CHECK (last_booking_on >= first_booking_on),
    CHECK ((balance_source = 'none') = (declared_closing IS NULL)),
    CHECK (declared_opening IS NULL OR declared_closing IS NOT NULL),
    CHECK (status <> 'reconciled'
           OR (declared_closing IS NOT NULL AND computed_closing = declared_closing)),
    CHECK ((error_code IS NOT NULL) = (status IN ('unreconciled', 'failed'))),
    CHECK (ai_call_id IS NULL OR format = 'pdf')
);
CREATE INDEX statement_import_account_idx ON statement_import (workspace_id, account_id);
COMMENT ON TABLE statement_import IS
    'One uploaded statement file (a document of type statement). Status imported = no closing '
    'balance declared yet; none of an unreconciled import''s lines reach matching (D21).';

CREATE TABLE transaction (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id              uuid NOT NULL REFERENCES workspace (id),
    account_id                uuid NOT NULL,
    import_id                 uuid NOT NULL,
    line_hash                 bytea NOT NULL CHECK (octet_length(line_hash) = 32),
    booking_date              date NOT NULL,
    value_date                date,
    amount                    numeric(14,2) NOT NULL,
    currency                  text NOT NULL DEFAULT 'EUR' CHECK (currency IN ('EUR')),
    counterparty_name         text,
    counterparty_iban_masked  text CHECK (counterparty_iban_masked ~ {IBAN_MASKED}),
    counterparty_iban_hmac    bytea CHECK (octet_length(counterparty_iban_hmac) = 32),
    purpose                   text NOT NULL DEFAULT '',
    booking_text              text,
    bank_reference            text,
    end_to_end_ref            text,
    mandate_ref               text,
    raw                       jsonb NOT NULL DEFAULT '{{}}' CHECK (jsonb_typeof(raw) = 'object'),
    is_pending                boolean NOT NULL DEFAULT false,
    excluded_as_duplicate_of  uuid,
    created_at                timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    UNIQUE (workspace_id, account_id, line_hash),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id, import_id)
        REFERENCES statement_import (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id, excluded_as_duplicate_of)
        REFERENCES transaction (workspace_id, account_id, id),
    CHECK ((counterparty_iban_masked IS NULL) = (counterparty_iban_hmac IS NULL)),
    CHECK (excluded_as_duplicate_of IS NULL OR excluded_as_duplicate_of <> id)
);
CREATE INDEX transaction_booking_idx ON transaction (workspace_id, account_id, booking_date);
COMMENT ON TABLE transaction IS
    'Bank lines, immutable as imported (10-year tax records; erasure does not scrub them). '
    'An excluded line (excluded_as_duplicate_of set) counts for no balance and gets no '
    'allocations.';
COMMENT ON COLUMN transaction.line_hash IS
    'SHA-256 of the bank reference or of the canonical line plus its ordinal (D22).';
COMMENT ON COLUMN transaction.raw IS
    'The line as parsed, without the counterparty IBAN (stored masked + HMAC only).';

CREATE TABLE possible_duplicate (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          uuid NOT NULL REFERENCES workspace (id),
    transaction_id        uuid NOT NULL,
    other_transaction_id  uuid NOT NULL,
    status                text NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'kept_both', 'merged')),
    decided_at            timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (transaction_id, other_transaction_id),
    FOREIGN KEY (workspace_id, transaction_id) REFERENCES transaction (workspace_id, id),
    FOREIGN KEY (workspace_id, other_transaction_id) REFERENCES transaction (workspace_id, id),
    CHECK (transaction_id <> other_transaction_id),
    CHECK ((status = 'open') = (decided_at IS NULL))
);
COMMENT ON TABLE possible_duplicate IS
    'A likely cross-format duplicate (D22), for review. transaction_id was stored first; '
    'merged excludes other_transaction_id (the later-imported line).';

CREATE TRIGGER transaction_immutable BEFORE UPDATE ON transaction
    FOR EACH ROW EXECUTE FUNCTION transaction_exclude_once();
CREATE TRIGGER transaction_no_delete BEFORE DELETE ON transaction
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER transaction_no_truncate BEFORE TRUNCATE ON transaction
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
"""


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
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
    op.execute(FUNCTIONS)
    op.execute(DDL)
    for table in TABLES:
        op.execute(_rls(table))
    grants = APP_GRANTS.items() | APP_COLUMN_GRANTS.items()
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in sorted(grants)]
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION transaction_exclude_once()")
