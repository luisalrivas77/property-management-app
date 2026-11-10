"""Phase 4b: the income tax return. Two tables, two document types, and three amendments.

Spec: docs/phases/phase-4b.md §6 (D96, D97, D104, D110, D111).
- `document.type` gains `lohnsteuerbescheinigung` and `gehaltsabrechnung` (D96), and the
  `document_extraction.kind` and `proposal.kind` CHECKs (0005) gain the same two kinds, as 0017
  widened the category CHECKs. 0001 and 0005 wrote these CHECKs inline, so Postgres named them
  document_type_check, document_extraction_kind_check and proposal_kind_check (0002 and 0004
  relied on the same naming for document_subtype_check). They are dropped by name without
  IF EXISTS, so a wrong name fails the migration loudly. Mirrors
  hausbuch.schemas.enums.DocumentType and ExtractionKind.
- `return_input` (D97): the household's employment and return facts per tax year (certificates,
  payslips, commute, home office, work room, equipment, telecom, dues, training, other costs,
  insurance, benefits, a Vorsorge override; household kinds: child, childcare, §35a services,
  donations). Append-only (forbid_mutation on UPDATE, DELETE and TRUNCATE, like income_input).
  A correction supersedes a row of the same kind and year (composite FK; the owner match is a
  service rule, since owner_id is null for the household kinds). carried_from_id (D110) names
  the previous year's row of the same kind this one was confirmed from; never on a document
  kind, a one-off kind or a child. Scope CHECKs mirror
  hausbuch.schemas.employment.ReturnInputCreate. `value` is typed per kind by the schema.
- `questionnaire_answer` (D110): one answer per question slot, append-only, no free-text column
  (`choice` is a coded option). The key's prefix decides which id is set.
- `tax_input` (D111, D110): kind `private_cost` (property- and year-scoped, document optional)
  and a self-FK `carried_from_id` of the same property and kind, for private_cost only. 0015
  named the kind and year CHECKs; they are recreated with the extended lists. The document
  CHECK (tax_input_year_document_check) is unchanged: a private cost's receipt is optional.
- `tax_export` (D104): `owner_id`, `property_id` nullable, exactly one of the two, kind
  `anlage_n_sheet` exactly for an owner's export.
Both new tables carry workspace_id, row-level security keyed on it, and composite FKs, as every
table since 0001. Grants: SELECT, INSERT on both.

The downgrade is refused while it would drop or orphan data (as 0013 to 0017): any return_input
or questionnaire_answer row, a private_cost tax input or one carried from another, a document
of the two new types, an extraction or proposal of the two kinds, or a tax_export of an owner.
It lifts FORCE RLS for each check, since the owner has no workspace set. It then drops the
tables and columns and restores the earlier CHECKs (under the names above) and
tax_export.property_id NOT NULL.

Revision ID: 0018_income_tax_return
Revises: 0017_electricity_category
Create Date: 2026-11-10
"""

from alembic import op

revision = "0018_income_tax_return"
down_revision = "0017_electricity_category"
branch_labels = None
depends_on = None

# Parents before children. Reversed for downgrade.
TABLES = ["return_input", "questionnaire_answer"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    "return_input": "SELECT, INSERT",  # append-only
    "questionnaire_answer": "SELECT, INSERT",  # append-only
}

NEW_TYPES = ("lohnsteuerbescheinigung", "gehaltsabrechnung")

# 0001's document.type list, in its order.
OLD_DOCUMENT_TYPES = (
    "statement",
    "invoice",
    "letter",
    "contract",
    "bescheid",
    "zinsbescheinigung",
    "wirtschaftsplan",
    "hausgeldabrechnung",
    "other",
)
# 0005's EXTRACTION_KINDS, in its order; proposal.kind is the same without 'manual'.
OLD_EXTRACTION_KINDS = (
    "manual",
    "purchase_contract",
    "loan_contract",
    "lease",
    "handover_protocol",
    "deposit_record",
    "invoice",
    "bescheid",
    "grundsteuerbescheid",
    "wirtschaftsplan",
    "hausgeldabrechnung",
    "zinsbescheinigung",
    "statement",
)
OLD_PROPOSAL_KINDS = tuple(k for k in OLD_EXTRACTION_KINDS if k != "manual")

# 0015's tax_input lists, in its order.
OLD_TAX_INPUT_KINDS = (
    "spread_82b",
    "renovation_treatment",
    "exclude_from_15pct",
    "market_rent",
    "ruecklage_spent",
    "interest_certified",
    "pre_handover_income",
    "useful_life",
    "movable_asset",
)
OLD_YEAR_SCOPED = ("ruecklage_spent", "interest_certified", "useful_life", "movable_asset")
PRIVATE_COST = "private_cost"

OLD_EXPORT_KINDS = ("field_sheet", "archive")
ANLAGE_N_SHEET = "anlage_n_sheet"

# hausbuch.schemas.enums.ReturnInputKind, HOUSEHOLD_KINDS and NOT_CARRIED_KINDS.
RETURN_INPUT_KINDS = (
    "wage_certificate",
    "payslip",
    "commute",
    "home_office",
    "work_room",
    "work_equipment",
    "telecom",
    "professional_association",
    "training",
    "other_expense",
    "other_insurance",
    "replacement_income",
    "vorsorge_override",
    "child",
    "childcare_cost",
    "household_services",
    "special_expense",
)
HOUSEHOLD_KINDS = ("child", "childcare_cost", "household_services", "special_expense")
DOCUMENT_KINDS = ("wage_certificate", "payslip")
# Never confirmed from last year's row: the year's documents, one-off items and a child (a
# spanning kind is read across years, never copied). A work_equipment subscription may be
# carried; the schema tells it from an item with a useful life.
NOT_CARRIED = ("wage_certificate", "payslip", "replacement_income", "training", "child")
ANSWERS = ("yes", "no", "not_applicable")


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


DDL = f"""
CREATE TABLE return_input (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    owner_id        uuid,
    kind            text NOT NULL CHECK (kind IN ({_sql_list(RETURN_INPUT_KINDS)})),
    tax_year        smallint NOT NULL CHECK (tax_year >= 2000),
    document_id     uuid,
    value           jsonb NOT NULL CHECK (jsonb_typeof(value) = 'object'),
    note            text CHECK (char_length(note) <= 500),
    supersedes_id   uuid UNIQUE,
    carried_from_id uuid,
    withdrawn       boolean NOT NULL DEFAULT false,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    recorded_by     uuid,
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, kind, id),
    UNIQUE (workspace_id, kind, tax_year, id),
    FOREIGN KEY (workspace_id, owner_id) REFERENCES owner (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    FOREIGN KEY (workspace_id, recorded_by) REFERENCES app_user (workspace_id, id),
    -- A correction supersedes a row of the same kind and year.
    FOREIGN KEY (workspace_id, kind, tax_year, supersedes_id)
        REFERENCES return_input (workspace_id, kind, tax_year, id),
    -- A row confirmed from last year's names a row of the same kind (D110).
    FOREIGN KEY (workspace_id, kind, carried_from_id)
        REFERENCES return_input (workspace_id, kind, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (carried_from_id IS NULL OR carried_from_id <> id),
    CHECK (NOT withdrawn OR supersedes_id IS NOT NULL),
    -- Scope per kind (hausbuch.schemas.employment.ReturnInputCreate says the same).
    CHECK ((owner_id IS NULL) = (kind IN ({_sql_list(HOUSEHOLD_KINDS)}))),
    CHECK (kind NOT IN ({_sql_list(DOCUMENT_KINDS)}) OR document_id IS NOT NULL),
    CHECK (kind NOT IN ({_sql_list(NOT_CARRIED)}) OR carried_from_id IS NULL)
);
CREATE INDEX return_input_owner_year ON return_input (workspace_id, owner_id, tax_year);

CREATE TABLE questionnaire_answer (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    question_key    text NOT NULL CHECK (char_length(question_key) <= 64
                        AND question_key ~ '^(owner|household|property)\\.[a-z0-9_]+$'),
    tax_year        smallint NOT NULL CHECK (tax_year >= 2000),
    owner_id        uuid,
    property_id     uuid,
    answer          text NOT NULL CHECK (answer IN ({_sql_list(ANSWERS)})),
    choice          text CHECK (choice ~ '^[a-z0-9_]{{1,32}}$'),
    supersedes_id   uuid UNIQUE,
    carried_from_id uuid,
    withdrawn       boolean NOT NULL DEFAULT false,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    recorded_by     uuid,
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, question_key, id),
    UNIQUE (workspace_id, question_key, tax_year, id),
    FOREIGN KEY (workspace_id, owner_id) REFERENCES owner (workspace_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, recorded_by) REFERENCES app_user (workspace_id, id),
    -- A correction supersedes an answer to the same question and year.
    FOREIGN KEY (workspace_id, question_key, tax_year, supersedes_id)
        REFERENCES questionnaire_answer (workspace_id, question_key, tax_year, id),
    -- An answer confirmed from last year's names an answer to the same question.
    FOREIGN KEY (workspace_id, question_key, carried_from_id)
        REFERENCES questionnaire_answer (workspace_id, question_key, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (carried_from_id IS NULL OR carried_from_id <> id),
    CHECK (NOT withdrawn OR supersedes_id IS NOT NULL),
    CHECK (NOT (owner_id IS NOT NULL AND property_id IS NOT NULL)),
    -- The key's scope decides the id (QuestionnaireAnswerCreate says the same).
    CHECK ((owner_id IS NOT NULL) = (question_key LIKE 'owner.%')),
    CHECK ((property_id IS NOT NULL) = (question_key LIKE 'property.%'))
);
CREATE INDEX questionnaire_answer_year_key
    ON questionnaire_answer (workspace_id, tax_year, question_key);

CREATE TRIGGER return_input_append_only BEFORE UPDATE OR DELETE ON return_input
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER return_input_no_truncate BEFORE TRUNCATE ON return_input
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER questionnaire_answer_append_only BEFORE UPDATE OR DELETE ON questionnaire_answer
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER questionnaire_answer_no_truncate BEFORE TRUNCATE ON questionnaire_answer
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
"""

TAX_INPUT_CARRIED = f"""
ALTER TABLE tax_input ADD COLUMN carried_from_id uuid;
ALTER TABLE tax_input ADD CONSTRAINT tax_input_carried_from_fkey
    FOREIGN KEY (workspace_id, property_id, kind, carried_from_id)
    REFERENCES tax_input (workspace_id, property_id, kind, id);
ALTER TABLE tax_input ADD CONSTRAINT tax_input_carried_from_check
    CHECK (carried_from_id IS NULL OR (kind = '{PRIVATE_COST}' AND carried_from_id <> id));
COMMENT ON COLUMN tax_input.carried_from_id IS 'Phase 4b D110: the previous year''s
private_cost row this one was confirmed from in the yearly questionnaire.';
"""

TAX_EXPORT_OWNER = f"""
ALTER TABLE tax_export ADD COLUMN owner_id uuid;
ALTER TABLE tax_export ADD CONSTRAINT tax_export_owner_fkey
    FOREIGN KEY (workspace_id, owner_id) REFERENCES owner (workspace_id, id);
ALTER TABLE tax_export ALTER COLUMN property_id DROP NOT NULL;
ALTER TABLE tax_export ADD CONSTRAINT tax_export_subject_check
    CHECK ((property_id IS NULL) <> (owner_id IS NULL));
ALTER TABLE tax_export ADD CONSTRAINT tax_export_owner_kind_check
    CHECK ((kind = '{ANLAGE_N_SHEET}') = (owner_id IS NOT NULL));
CREATE INDEX tax_export_owner_year ON tax_export (workspace_id, owner_id, tax_year);
COMMENT ON COLUMN tax_export.owner_id IS 'Phase 4b D104: the owner of an Anlage N sheet;
exactly one of property_id and owner_id is set.';
"""


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


def _set_tax_input_year_scope(year_scoped: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE tax_input DROP CONSTRAINT tax_input_year_scope_check")
    op.execute(
        "ALTER TABLE tax_input ADD CONSTRAINT tax_input_year_scope_check"
        f" CHECK ((kind IN ({_sql_list(year_scoped)})) = (tax_year IS NOT NULL))"
    )


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


_NEW = _sql_list(NEW_TYPES)
# What the downgrade would drop or orphan.
_DATA_CHECKS = (
    ("return_input", "SELECT 1 FROM return_input", "table return_input holds rows"),
    (
        "questionnaire_answer",
        "SELECT 1 FROM questionnaire_answer",
        "table questionnaire_answer holds rows",
    ),
    (
        "tax_input",
        f"SELECT 1 FROM tax_input WHERE kind = '{PRIVATE_COST}' OR carried_from_id IS NOT NULL",
        "a tax input is private_cost",
    ),
    (
        "document",
        f"SELECT 1 FROM document WHERE type IN ({_NEW})",
        "a document is a Lohnsteuerbescheinigung or Gehaltsabrechnung",
    ),
    (
        "document_extraction",
        f"SELECT 1 FROM document_extraction WHERE kind IN ({_NEW})",
        "an extraction is of a pay document",
    ),
    (
        "proposal",
        f"SELECT 1 FROM proposal WHERE kind IN ({_NEW})",
        "a proposal is of a pay document",
    ),
    (
        "tax_export",
        "SELECT 1 FROM tax_export WHERE owner_id IS NOT NULL",
        "a tax export is an Anlage N sheet of an owner",
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
    _set_value_list("document", "type", (*OLD_DOCUMENT_TYPES, *NEW_TYPES))
    _set_value_list("document_extraction", "kind", (*OLD_EXTRACTION_KINDS, *NEW_TYPES))
    _set_value_list("proposal", "kind", (*OLD_PROPOSAL_KINDS, *NEW_TYPES))
    op.execute(DDL)
    for table in TABLES:
        op.execute(_rls(table))
    _set_value_list("tax_input", "kind", (*OLD_TAX_INPUT_KINDS, PRIVATE_COST))
    _set_tax_input_year_scope((*OLD_YEAR_SCOPED, PRIVATE_COST))
    op.execute(TAX_INPUT_CARRIED)
    _set_value_list("tax_export", "kind", (*OLD_EXPORT_KINDS, ANLAGE_N_SHEET))
    op.execute(TAX_EXPORT_OWNER)
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute("DROP INDEX tax_export_owner_year")
    for name in ("tax_export_owner_kind_check", "tax_export_subject_check"):
        op.execute(f"ALTER TABLE tax_export DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE tax_export ALTER COLUMN property_id SET NOT NULL")
    op.execute("ALTER TABLE tax_export DROP COLUMN owner_id")  # drops tax_export_owner_fkey
    _set_value_list("tax_export", "kind", OLD_EXPORT_KINDS)
    op.execute("ALTER TABLE tax_input DROP COLUMN carried_from_id")  # drops its FK and CHECK
    _set_tax_input_year_scope(OLD_YEAR_SCOPED)
    _set_value_list("tax_input", "kind", OLD_TAX_INPUT_KINDS)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    _set_value_list("proposal", "kind", OLD_PROPOSAL_KINDS)
    _set_value_list("document_extraction", "kind", OLD_EXTRACTION_KINDS)
    _set_value_list("document", "type", OLD_DOCUMENT_TYPES)
