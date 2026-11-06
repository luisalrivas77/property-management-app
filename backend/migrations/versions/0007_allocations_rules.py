"""Phase 2 step 4: allocations and learned matching rules.

Spec: docs/phases/phase-2.md §3.3, §8 (D23, D24, D25). Follows the 0001 conventions:
workspace_id on every table, UNIQUE (workspace_id, id) and composite FKs, text + CHECK for
closed value sets, forced workspace_isolation RLS, explicit hausbuch_app grants.

- allocation rows are append-only (D23): no DELETE or TRUNCATE, and the one UPDATE allowed
  moves status forward (proposed -> confirmed, proposed -> superseded, confirmed ->
  superseded), stamping confirmed_at / confirmed_by on confirmation (trigger
  allocation_status_forward, and a column grant on those three columns). A correction is a
  new row set whose rows name the row they supersede, on the same transaction (composite
  FK). Σ amounts per transaction is the service's check (spec §3.4); no CHECK spans rows.
- matching_rule stores a matcher and a split method, never an amount (D25): the CHECK on
  matcher allows only its four keys (an IBAN hash, two patterns, an amount sign), each a
  JSON string when present, and the table has no money column.
- ai_call.transaction_id gets the FK 0005 left out.
- The downgrade refuses while any of this step's tables holds a row (it would drop
  ledger data); it lifts FORCE RLS first, since the owner has no workspace set.

Revision ID: 0007_allocations_rules
Revises: 0006_accounts_statements
Create Date: 2026-11-06
"""

from alembic import op

revision = "0007_allocations_rules"
down_revision = "0006_accounts_statements"
branch_labels = None
depends_on = None

# Parents before children. Reversed for downgrade (after the cross FKs are dropped).
TABLES = ["allocation", "matching_rule"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    # No table-wide UPDATE and never DELETE: see APP_COLUMN_GRANTS.
    "allocation": "SELECT, INSERT",
    "matching_rule": "SELECT, INSERT, UPDATE",
}
APP_COLUMN_GRANTS = {"allocation": "UPDATE (status, confirmed_at, confirmed_by)"}

CATEGORIES = (
    "'rent_cold', 'operating_costs_prepayment', 'operating_costs_settlement', 'deposit_in', "
    "'deposit_out', 'owner_transfer_in', 'owner_transfer_out', 'loan_interest', "
    "'loan_principal', 'loan_fees', 'hausgeld_umlagefaehig', 'hausgeld_verwaltung', "
    "'hausgeld_ruecklage', 'grundsteuer', 'insurance', 'repairs', 'renovation', "
    "'acquisition_cost', 'professional_fees', 'bank_fees', 'other_income', 'other_expense', "
    "'uncategorized'"
)
METHODS = (
    "'rule', 'matcher_rent', 'matcher_loan', 'matcher_owner', 'matcher_hausgeld', "
    "'document_match', 'llm', 'user'"
)
SPLIT_METHODS = (
    "'single_category', 'by_rent_schedule', 'by_loan_schedule', 'by_wirtschaftsplan', "
    "'owner_transfer'"
)
MATCHER_KEYS = (
    "'counterparty_iban_hmac', 'counterparty_name_pattern', 'purpose_pattern', 'amount_sign'"
)
# Every matcher value is a JSON string when present (null counts as absent): ->> would turn
# an object or a number into text that passes the other checks.
MATCHER_TYPED = "\n                                    AND ".join(
    f"(matcher ->> {key} IS NULL OR jsonb_typeof(matcher -> {key}) = 'string')"
    for key in MATCHER_KEYS.split(", ")
)

FUNCTIONS = """
CREATE FUNCTION allocation_status_forward() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Append-only rows (D23): only status moves, forward, and a confirmation is stamped.
    IF (to_jsonb(NEW) - 'status' - 'confirmed_at' - 'confirmed_by')
           IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'confirmed_at' - 'confirmed_by')
       OR NOT ((OLD.status = 'proposed' AND NEW.status IN ('confirmed', 'superseded'))
               OR (OLD.status = 'confirmed' AND NEW.status = 'superseded'))
       OR (NEW.status = 'superseded'
           AND (NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
                OR NEW.confirmed_by IS DISTINCT FROM OLD.confirmed_by)) THEN
        RAISE EXCEPTION 'UPDATE on allocation is not allowed: rows are append-only'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only status moves forward (proposed -> confirmed -> superseded); '
                         'record a correction as a new row set.';
    END IF;
    RETURN NEW;
END
$$;
"""

DDL = rf"""
CREATE TABLE allocation (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          uuid NOT NULL REFERENCES workspace (id),
    transaction_id        uuid NOT NULL,
    amount                numeric(14,2) NOT NULL,
    category              text NOT NULL CHECK (category IN ({CATEGORIES})),
    property_id           uuid,
    unit_id               uuid,
    tenancy_id            uuid,
    loan_id               uuid,
    tax_year              smallint NOT NULL CHECK (tax_year BETWEEN 2000 AND 2100),
    service_period_start  date,
    service_period_end    date,
    recurring             boolean NOT NULL DEFAULT false,
    method                text NOT NULL CHECK (method IN ({METHODS})),
    confidence            numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status                text NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed', 'confirmed', 'superseded')),
    supersedes_id         uuid,
    rule_id               uuid,  -- FK added after matching_rule (they reference each other)
    ai_call_id            uuid,
    document_id           uuid,
    reasoning             text,
    question              text,
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    confirmed_at          timestamptz,
    confirmed_by          uuid,
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, transaction_id, id),
    FOREIGN KEY (workspace_id, transaction_id) REFERENCES transaction (workspace_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, unit_id) REFERENCES unit (workspace_id, id),
    FOREIGN KEY (workspace_id, tenancy_id) REFERENCES tenancy (workspace_id, id),
    FOREIGN KEY (workspace_id, loan_id) REFERENCES loan (workspace_id, id),
    -- A correction supersedes a row of the same transaction.
    FOREIGN KEY (workspace_id, transaction_id, supersedes_id)
        REFERENCES allocation (workspace_id, transaction_id, id),
    FOREIGN KEY (workspace_id, ai_call_id) REFERENCES ai_call (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    FOREIGN KEY (workspace_id, confirmed_by) REFERENCES app_user (workspace_id, id),
    CHECK ((service_period_start IS NULL) = (service_period_end IS NULL)),
    CHECK (service_period_end >= service_period_start),
    CHECK (unit_id IS NULL OR property_id IS NOT NULL),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK ((method = 'rule') = (rule_id IS NOT NULL)),
    CHECK (ai_call_id IS NULL OR method = 'llm'),
    CHECK (status <> 'confirmed' OR confirmed_at IS NOT NULL),
    CHECK (status <> 'proposed' OR confirmed_at IS NULL),
    CHECK (confirmed_by IS NULL OR confirmed_at IS NOT NULL)
);
CREATE INDEX allocation_transaction_idx ON allocation (workspace_id, transaction_id);
CREATE INDEX allocation_confirmed_year_idx ON allocation (workspace_id, property_id, tax_year)
    WHERE status = 'confirmed';
COMMENT ON TABLE allocation IS
    'Splits of a transaction (D23): append-only rows, status forward only. Tax and '
    'performance figures sum confirmed rows; the service keeps the sum of a transaction''s '
    'non-superseded rows equal to its amount.';
COMMENT ON COLUMN allocation.confirmed_by IS
    'The user who confirmed it; null with confirmed_at set = auto-confirmed (rule, matcher).';

CREATE TABLE matching_rule (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id                uuid NOT NULL REFERENCES workspace (id),
    matcher                     jsonb NOT NULL CHECK (
                                    jsonb_typeof(matcher) = 'object'
                                    AND matcher - ARRAY[{MATCHER_KEYS}] = '{{}}'::jsonb
                                    AND {MATCHER_TYPED}
                                    AND num_nonnulls(matcher ->> 'counterparty_iban_hmac',
                                                     matcher ->> 'counterparty_name_pattern',
                                                     matcher ->> 'purpose_pattern') >= 1
                                    AND (matcher ->> 'amount_sign' IS NULL
                                         OR matcher ->> 'amount_sign' IN ('credit', 'debit'))
                                    AND (matcher ->> 'counterparty_iban_hmac' IS NULL
                                         OR matcher ->> 'counterparty_iban_hmac'
                                            ~ '^[0-9a-f]{{64}}$')),
    split_method                text NOT NULL CHECK (split_method IN ({SPLIT_METHODS})),
    category                    text CHECK (category IN ({CATEGORIES})),
    property_id                 uuid,
    tenancy_id                  uuid,
    loan_id                     uuid,
    recurring                   boolean NOT NULL DEFAULT false,
    created_from_allocation_id  uuid,
    enabled                     boolean NOT NULL DEFAULT true,
    hit_count                   integer NOT NULL DEFAULT 0 CHECK (hit_count >= 0),
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, tenancy_id) REFERENCES tenancy (workspace_id, id),
    FOREIGN KEY (workspace_id, loan_id) REFERENCES loan (workspace_id, id),
    FOREIGN KEY (workspace_id, created_from_allocation_id)
        REFERENCES allocation (workspace_id, id),
    CHECK ((split_method = 'single_category') = (category IS NOT NULL)),
    CHECK (split_method <> 'by_rent_schedule' OR tenancy_id IS NOT NULL),
    CHECK (split_method <> 'by_loan_schedule' OR loan_id IS NOT NULL),
    CHECK (split_method <> 'by_wirtschaftsplan' OR property_id IS NOT NULL)
);
COMMENT ON TABLE matching_rule IS
    'Learned rules (D25): a matcher and a split method, never an amount, so a new '
    'Wirtschaftsplan or rate change cannot be mis-split by an old rule.';
COMMENT ON COLUMN matching_rule.recurring IS
    'Copied from the confirmed answer; a single_category hit gets it (the schedule-based '
    'split methods set recurring themselves).';

ALTER TABLE allocation ADD CONSTRAINT allocation_rule_fk
    FOREIGN KEY (workspace_id, rule_id) REFERENCES matching_rule (workspace_id, id);
ALTER TABLE ai_call ADD CONSTRAINT ai_call_transaction_fk
    FOREIGN KEY (workspace_id, transaction_id) REFERENCES transaction (workspace_id, id);

CREATE TRIGGER allocation_append_only BEFORE UPDATE ON allocation
    FOR EACH ROW EXECUTE FUNCTION allocation_status_forward();
CREATE TRIGGER allocation_no_delete BEFORE DELETE ON allocation
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER allocation_no_truncate BEFORE TRUNCATE ON allocation
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


def _refuse_if_rows(tables: list[str]) -> str:
    """Downgrade guard: refuse to drop a table that holds rows. The downgrade runs as the
    owner with no app.workspace_id, and forced RLS would hide every row, so each table first
    stops forcing RLS (the owner then sees all rows). A refusal rolls the ALTERs back."""
    checks = "".join(
        f"""
    ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM {table}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: table {table} holds rows'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the rows first; a downgrade never drops data.';
    END IF;"""
        for table in tables
    )
    return f"DO $$\nBEGIN{checks}\nEND\n$$;"


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
    op.execute(_refuse_if_rows(list(reversed(TABLES))))
    op.execute("ALTER TABLE ai_call DROP CONSTRAINT ai_call_transaction_fk")
    op.execute("ALTER TABLE allocation DROP CONSTRAINT allocation_rule_fk")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION allocation_status_forward()")
