"""Phase 4b step 6: the Einkommensteuerbescheid as a document subtype and an extraction kind.

Spec: docs/phases/phase-4b.md D129, §8.4.
- `document.subtype` gains `einkommensteuerbescheid` (type bescheid; a tax record like every
  Bescheid, so retention is unchanged), and the `document_extraction.kind` and `proposal.kind`
  CHECKs gain the same kind. 0001 and 0005 wrote these CHECKs inline, so Postgres named them
  document_subtype_check, document_extraction_kind_check and proposal_kind_check (0004 and 0018
  relied on the same naming). They are dropped by name without IF EXISTS, so a wrong name fails
  the migration loudly. Mirrors hausbuch.schemas.enums.DocumentSubtype and ExtractionKind.
- No table or column: the zvE the AI reads is confirmed into income_input (0016) as a
  household bescheid_zve row, whose CHECKs already allow it.

The downgrade is refused while it would leave a value its CHECK no longer allows (as 0018): a
document of the subtype, or an extraction or proposal of the kind. It lifts FORCE RLS for each
check, since the owner has no workspace set, and then restores the earlier CHECKs under the
names above.

Revision ID: 0020_income_tax_assessment
Revises: 0019_automation
Create Date: 2026-11-10
"""

from alembic import op

revision = "0020_income_tax_assessment"
down_revision = "0019_automation"
branch_labels = None
depends_on = None

NEW = "einkommensteuerbescheid"

# 0004's document.subtype list, in its order.
OLD_SUBTYPES = (
    "purchase_contract",
    "price_split_evidence",
    "appraisal",
    "loan_contract",
    "lease",
    "handover_protocol",
    "deposit_record",
    "cost_basis_evidence",
    "grundsteuerbescheid",
)
# 0018's document_extraction.kind list, in its order; proposal.kind is the same without 'manual'.
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
    "lohnsteuerbescheinigung",
    "gehaltsabrechnung",
)
OLD_PROPOSAL_KINDS = tuple(k for k in OLD_EXTRACTION_KINDS if k != "manual")


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


_DATA_CHECKS = (
    (
        "document",
        f"SELECT 1 FROM document WHERE subtype = '{NEW}'",
        "a document is an Einkommensteuerbescheid",
    ),
    (
        "document_extraction",
        f"SELECT 1 FROM document_extraction WHERE kind = '{NEW}'",
        "an extraction is of an Einkommensteuerbescheid",
    ),
    (
        "proposal",
        f"SELECT 1 FROM proposal WHERE kind = '{NEW}'",
        "a proposal is of an Einkommensteuerbescheid",
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
    _set_value_list("document", "subtype", (*OLD_SUBTYPES, NEW))
    _set_value_list("document_extraction", "kind", (*OLD_EXTRACTION_KINDS, NEW))
    _set_value_list("proposal", "kind", (*OLD_PROPOSAL_KINDS, NEW))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_value_list("proposal", "kind", OLD_PROPOSAL_KINDS)
    _set_value_list("document_extraction", "kind", OLD_EXTRACTION_KINDS)
    _set_value_list("document", "subtype", OLD_SUBTYPES)
