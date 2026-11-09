"""Phase 4: performance. Two owner columns, two tables, and the typed rate carried over.

Spec: docs/phases/phase-4.md §6 (D83, D87, D90).
- `owner.church_tax` (D90): boolean, default false. `owner.tax_state`: the Bundesland code
  whose church tax rate applies, required when church_tax. Mirrors
  hausbuch.schemas.enums.StateCode. The spec writes char(2); it is text with a CHECK on the 16
  codes, as every closed value set since 0001 (tests/db/test_schema_consistency.py reads the
  lists as 'x'::text, and a char(2) would compare as bpchar).
- `income_input` (D83): the household's income facts per tax year (expected gross pay and
  deductions per owner; the Bescheid's zvE and a rate override per household). Append-only
  (forbid_mutation on UPDATE, DELETE and TRUNCATE, like tax_input). A correction is a new row
  whose supersedes_id names a row of the same kind and year (the composite FK says so; the
  owner match is a service rule, since owner_id is null for the household kinds and a
  composite FK with a null column is not checked). Per-kind scope CHECKs mirror
  hausbuch.schemas.performance.IncomeInputCreate. `value` is typed per kind by the schema.
- `projection_scenario` (D87): the levers of one property's projection. Append-only; a
  correction supersedes a scenario of the same property. "At most one default in force per
  property" is a service rule (a partial unique index cannot see supersession).
- The carry-over (D90): one `marginal_rate_override` row for tax year 2026 per workspace whose
  owners hold exactly one distinct non-null marginal_tax_rate, note "übernommen aus
  Eigentümer"; a workspace whose owners disagree gets no row and a NOTICE. It reads owner with
  FORCE RLS lifted for its duration (the owner has no workspace set), and runs before
  income_input gets RLS. No event is written: the old value stays in the owner.created /
  owner.updated payloads. Then owner.marginal_tax_rate is dropped.
Both tables carry workspace_id, row-level security keyed on it, and composite FKs, as every
table since 0001. Grants: SELECT, INSERT on both.

The downgrade is refused while it would drop data (as 0013): any income_input or
projection_scenario row (including a carried-over rate), or an owner with church_tax or a
tax_state set. It then drops both tables and the two columns and restores
owner.marginal_tax_rate empty, with its 0001 CHECK (at the end of the table, not where 0001
put it).

Revision ID: 0016_performance
Revises: 0015_movable_asset
Create Date: 2026-11-09
"""

from alembic import op

revision = "0016_performance"
down_revision = "0015_movable_asset"
branch_labels = None
depends_on = None

# Parents before children. Reversed for downgrade.
TABLES = ["income_input", "projection_scenario"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    "income_input": "SELECT, INSERT",  # append-only
    "projection_scenario": "SELECT, INSERT",  # append-only
}

STATE_CODES = (
    "'BW', 'BY', 'BE', 'BB', 'HB', 'HH', 'HE', 'MV', 'NI', 'NW', 'RP', 'SL', 'SN', 'ST', 'SH', 'TH'"
)
INCOME_INPUT_KINDS = (
    "'expected_gross_pay', 'deductions_override', 'bescheid_zve', 'marginal_rate_override'"
)
OWNER_SCOPED = "'expected_gross_pay', 'deductions_override'"
CARRY_OVER_YEAR = 2026
CARRY_OVER_NOTE = "übernommen aus Eigentümer"

COLUMNS = f"""
ALTER TABLE owner ADD COLUMN church_tax boolean NOT NULL DEFAULT false;
ALTER TABLE owner ADD COLUMN tax_state text CHECK (tax_state IN ({STATE_CODES}));
ALTER TABLE owner ADD CONSTRAINT owner_church_tax_state_check
    CHECK (NOT church_tax OR tax_state IS NOT NULL);
COMMENT ON COLUMN owner.church_tax IS 'D90: the owner pays church tax (rate by tax_state).';
COMMENT ON COLUMN owner.tax_state IS 'D90: Bundesland code for the church tax rate; required
when church_tax.';
"""

DDL = f"""
CREATE TABLE income_input (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    owner_id        uuid,
    kind            text NOT NULL CHECK (kind IN ({INCOME_INPUT_KINDS})),
    tax_year        smallint NOT NULL CHECK (tax_year >= 2000),
    document_id     uuid,
    value           jsonb NOT NULL CHECK (jsonb_typeof(value) = 'object'),
    note            text CHECK (char_length(note) <= 500),
    supersedes_id   uuid UNIQUE,
    withdrawn       boolean NOT NULL DEFAULT false,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    recorded_by     uuid,
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, kind, tax_year, id),
    FOREIGN KEY (workspace_id, owner_id) REFERENCES owner (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    FOREIGN KEY (workspace_id, recorded_by) REFERENCES app_user (workspace_id, id),
    -- A correction supersedes a row of the same kind and year.
    FOREIGN KEY (workspace_id, kind, tax_year, supersedes_id)
        REFERENCES income_input (workspace_id, kind, tax_year, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (NOT withdrawn OR supersedes_id IS NOT NULL),
    -- Scope per kind (hausbuch.schemas.performance.IncomeInputCreate says the same).
    CHECK ((kind IN ({OWNER_SCOPED})) = (owner_id IS NOT NULL)),
    CHECK (kind <> 'bescheid_zve' OR document_id IS NOT NULL)
);
CREATE INDEX income_input_kind_year ON income_input (workspace_id, kind, tax_year);

CREATE TABLE projection_scenario (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    property_id     uuid NOT NULL,
    name            text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    is_default      boolean NOT NULL DEFAULT false,
    levers          jsonb NOT NULL CHECK (jsonb_typeof(levers) = 'object'),
    supersedes_id   uuid UNIQUE,
    withdrawn       boolean NOT NULL DEFAULT false,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    recorded_by     uuid,
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, property_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, recorded_by) REFERENCES app_user (workspace_id, id),
    -- A correction supersedes a scenario of the same property.
    FOREIGN KEY (workspace_id, property_id, supersedes_id)
        REFERENCES projection_scenario (workspace_id, property_id, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (NOT withdrawn OR supersedes_id IS NOT NULL)
);
CREATE INDEX projection_scenario_property ON projection_scenario (workspace_id, property_id);

CREATE TRIGGER income_input_append_only BEFORE UPDATE OR DELETE ON income_input
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER income_input_no_truncate BEFORE TRUNCATE ON income_input
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER projection_scenario_append_only BEFORE UPDATE OR DELETE ON projection_scenario
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER projection_scenario_no_truncate BEFORE TRUNCATE ON projection_scenario
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
"""

# D90. income_input has no RLS yet; owner's FORCE is lifted for the read and restored.
CARRY_OVER = f"""
DO $$
DECLARE
    w record;
BEGIN
    ALTER TABLE owner NO FORCE ROW LEVEL SECURITY;
    FOR w IN
        SELECT workspace_id, count(DISTINCT marginal_tax_rate) AS rates,
               min(marginal_tax_rate) AS rate
        FROM owner
        WHERE marginal_tax_rate IS NOT NULL
        GROUP BY workspace_id
    LOOP
        IF w.rates = 1 THEN
            INSERT INTO income_input (workspace_id, kind, tax_year, value, note)
            VALUES (w.workspace_id, 'marginal_rate_override', {CARRY_OVER_YEAR},
                    jsonb_build_object('rate', w.rate::text), '{CARRY_OVER_NOTE}');
        ELSE
            RAISE NOTICE 'workspace %: % different owner rates, none carried over',
                w.workspace_id, w.rates;
        END IF;
    END LOOP;
    ALTER TABLE owner FORCE ROW LEVEL SECURITY;
END
$$;
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


# What the downgrade would lose: rows, or a value other than the column's default.
_DATA_CHECKS = (
    ("income_input", "SELECT 1 FROM income_input", "table income_input holds rows"),
    (
        "projection_scenario",
        "SELECT 1 FROM projection_scenario",
        "table projection_scenario holds rows",
    ),
    (
        "owner",
        "SELECT 1 FROM owner WHERE church_tax OR tax_state IS NOT NULL",
        "an owner has church tax or a Bundesland",
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
    op.execute(COLUMNS)
    op.execute(DDL)
    op.execute(CARRY_OVER)
    op.execute("ALTER TABLE owner DROP COLUMN marginal_tax_rate")
    for table in TABLES:
        op.execute(_rls(table))
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE owner DROP CONSTRAINT owner_church_tax_state_check")
    op.execute("ALTER TABLE owner DROP COLUMN tax_state")
    op.execute("ALTER TABLE owner DROP COLUMN church_tax")
    op.execute(
        "ALTER TABLE owner ADD COLUMN marginal_tax_rate numeric(5,4)"
        " CHECK (marginal_tax_rate >= 0 AND marginal_tax_rate < 1)"
    )
