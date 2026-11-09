"""Phase 3: the tax year. Three columns and two tables.

Spec: docs/phases/phase-3.md §6 (D58, D65, D67, D71, D72).
- `tenancy.rent_due_day` (D67): the day of the month the rent is due, 1–28, default 3
  (§556b Abs. 1 BGB). The §11 ten-day rule needs it (D54).
- `tenancy.operating_costs_terms` (D72): prepayment | flat_rate | included, default
  prepayment. Mirrors hausbuch.schemas.enums.OperatingCostsTerms.
- `property.living_area_m2` (D71): the flat's Wohnfläche for Z11 and the Z13 row; nullable.
- `tax_input` (D58): the user's tax decisions and verified certificate figures, append-only
  (forbid_mutation on UPDATE, DELETE and TRUNCATE, like event and rent_schedule). A
  correction is a new row whose supersedes_id names a row of the same property and kind (the
  composite FK says so); withdrawn = true ends a decision. Per-kind scope CHECKs mirror
  hausbuch.schemas.tax.TaxInputCreate. `value` is typed per kind by the schema; the table
  only requires a JSON object (no amount column is summed by SQL; the rules read it).
- `tax_export` (D65): a snapshot of what was handed over: the draft JSON and the
  content-addressed file. Immutable (forbid_mutation on UPDATE); the app role may DELETE, for
  the retention sweeper (10 years after tax_year, with the year's tax records).
Both tables carry workspace_id, row-level security keyed on it, and composite FKs, as every
table since 0001. Grants: tax_input SELECT, INSERT; tax_export SELECT, INSERT, DELETE.

The downgrade drops both tables and the three columns. It is refused while it would drop
data (as 0006 to 0012): any tax_input or tax_export row, a property with a living area, or a
tenancy whose due day or cost terms differ from the defaults. It lifts FORCE RLS for the
check, since the owner has no workspace set.

Revision ID: 0013_tax_year
Revises: 0012_mgmt_category_last_split
Create Date: 2026-11-09
"""

from alembic import op

revision = "0013_tax_year"
down_revision = "0012_mgmt_category_last_split"
branch_labels = None
depends_on = None

# Parents before children. Reversed for downgrade.
TABLES = ["tax_input", "tax_export"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    "tax_input": "SELECT, INSERT",  # append-only
    "tax_export": "SELECT, INSERT, DELETE",  # immutable; deleted by the retention sweeper
}

OPERATING_COSTS_TERMS = "'prepayment', 'flat_rate', 'included'"
TAX_INPUT_KINDS = (
    "'spread_82b', 'renovation_treatment', 'exclude_from_15pct', 'market_rent', "
    "'ruecklage_spent', 'interest_certified', 'pre_handover_income'"
)
ALLOCATION_SCOPED = "'spread_82b', 'renovation_treatment', 'exclude_from_15pct'"
YEAR_SCOPED = "'ruecklage_spent', 'interest_certified'"

COLUMNS = f"""
ALTER TABLE tenancy ADD COLUMN rent_due_day smallint NOT NULL DEFAULT 3
    CHECK (rent_due_day BETWEEN 1 AND 28);
ALTER TABLE tenancy ADD COLUMN operating_costs_terms text NOT NULL DEFAULT 'prepayment'
    CHECK (operating_costs_terms IN ({OPERATING_COSTS_TERMS}));
ALTER TABLE property ADD COLUMN living_area_m2 numeric(7,2)
    CHECK (living_area_m2 > 0);
COMMENT ON COLUMN tenancy.rent_due_day IS 'D67: day of the month the rent is due (the
§11 ten-day rule reads it); 1-28, default 3 (§556b Abs. 1 BGB).';
COMMENT ON COLUMN tenancy.operating_costs_terms IS 'D72: prepayment (Vorauszahlung with a
settlement), flat_rate (Pauschale) or included (one all-in rent, Nebenkosten not agreed).';
COMMENT ON COLUMN property.living_area_m2 IS 'D71: the flat''s Wohnfläche for Anlage V Z11
and its Z13 row; null = the sum of the units'' m², with a warning.';
"""

DDL = f"""
CREATE TABLE tax_input (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    property_id     uuid NOT NULL,
    kind            text NOT NULL CHECK (kind IN ({TAX_INPUT_KINDS})),
    tax_year        smallint CHECK (tax_year >= 2000),
    unit_id         uuid,
    loan_id         uuid,
    allocation_id   uuid,
    document_id     uuid,
    value           jsonb NOT NULL CHECK (jsonb_typeof(value) = 'object'),
    note            text CHECK (char_length(note) <= 500),
    supersedes_id   uuid UNIQUE,
    withdrawn       boolean NOT NULL DEFAULT false,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    recorded_by     uuid,
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, property_id, kind, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, unit_id) REFERENCES unit (workspace_id, id),
    FOREIGN KEY (workspace_id, loan_id) REFERENCES loan (workspace_id, id),
    FOREIGN KEY (workspace_id, allocation_id) REFERENCES allocation (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    FOREIGN KEY (workspace_id, recorded_by) REFERENCES app_user (workspace_id, id),
    -- A correction supersedes a row of the same property and kind.
    FOREIGN KEY (workspace_id, property_id, kind, supersedes_id)
        REFERENCES tax_input (workspace_id, property_id, kind, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (NOT withdrawn OR supersedes_id IS NOT NULL),
    -- Scope per kind (hausbuch.schemas.tax.TaxInputCreate says the same).
    CHECK ((kind IN ({ALLOCATION_SCOPED})) = (allocation_id IS NOT NULL)),
    CHECK (unit_id IS NULL OR kind = 'market_rent'),
    CHECK ((kind = 'interest_certified') = (loan_id IS NOT NULL)),
    CHECK ((kind IN ({YEAR_SCOPED})) = (tax_year IS NOT NULL)),
    CHECK (kind NOT IN ({YEAR_SCOPED}) OR document_id IS NOT NULL)
);
CREATE INDEX tax_input_property_kind ON tax_input (workspace_id, property_id, kind);

CREATE TABLE tax_export (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspace (id),
    property_id       uuid NOT NULL,
    tax_year          smallint NOT NULL CHECK (tax_year >= 2000),
    kind              text NOT NULL CHECK (kind IN ('field_sheet', 'archive')),
    rule_set_version  text NOT NULL CHECK (rule_set_version <> ''),
    form_year         smallint NOT NULL CHECK (form_year >= 2000 AND form_year <= tax_year),
    form_status       text NOT NULL CHECK (form_status IN ('published', 'provisional')),
    draft             jsonb NOT NULL CHECK (jsonb_typeof(draft) = 'object'),
    file_sha256       bytea NOT NULL CHECK (octet_length(file_sha256) = 32),
    size_bytes        bigint NOT NULL CHECK (size_bytes > 0),
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        uuid,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, file_sha256) REFERENCES document_file (workspace_id, sha256),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id),
    CHECK ((form_status = 'published') = (form_year = tax_year))
);
CREATE INDEX tax_export_property_year ON tax_export (workspace_id, property_id, tax_year);

CREATE TRIGGER tax_input_append_only BEFORE UPDATE OR DELETE ON tax_input
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER tax_input_no_truncate BEFORE TRUNCATE ON tax_input
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER tax_export_immutable BEFORE UPDATE ON tax_export
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
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
    ("tax_input", "SELECT 1 FROM tax_input", "table tax_input holds rows"),
    ("tax_export", "SELECT 1 FROM tax_export", "table tax_export holds rows"),
    (
        "property",
        "SELECT 1 FROM property WHERE living_area_m2 IS NOT NULL",
        "a property has a living area",
    ),
    (
        "tenancy",
        "SELECT 1 FROM tenancy WHERE rent_due_day <> 3 OR operating_costs_terms <> 'prepayment'",
        "a tenancy has a due day or cost terms other than the default",
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
    for table in TABLES:
        op.execute(_rls(table))
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE property DROP COLUMN living_area_m2")
    op.execute("ALTER TABLE tenancy DROP COLUMN operating_costs_terms")
    op.execute("ALTER TABLE tenancy DROP COLUMN rent_due_day")
