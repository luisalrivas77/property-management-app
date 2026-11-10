"""Phase 2 D130, D131: the document catalogue's subtypes and the general reading's kinds.

Spec: docs/phases/phase-2.md D130, D131, §6.2, §6.2.1.
- `document.subtype` gains every subtype of the catalogue (§6.2.1), `management_contract`
  (D130) among them, and the `document_extraction.kind` and `proposal.kind` CHECKs gain
  `general` and `management_contract`. 0001 and 0005 wrote these CHECKs inline, so Postgres
  named them document_subtype_check, document_extraction_kind_check and proposal_kind_check
  (0004, 0018 and 0020 relied on the same naming). They are dropped by name without IF EXISTS,
  so a wrong name fails the migration loudly. Mirrors hausbuch.schemas.enums.DocumentSubtype
  and ExtractionKind.
- No table or column: these documents propose typing and at most one link, and no record.

The downgrade is refused while it would leave a value its CHECK no longer allows (as 0020): a
document of a new subtype, or an extraction or proposal of a new kind. It lifts FORCE RLS for
each check, since the owner has no workspace set, and then restores 0020's CHECKs under the
names above.

Revision ID: 0021_document_catalogue
Revises: 0020_income_tax_assessment
Create Date: 2026-11-10
"""

from alembic import op

revision = "0021_document_catalogue"
down_revision = "0020_income_tax_assessment"
branch_labels = None
depends_on = None

# 0020's document.subtype list, in its order.
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
    "einkommensteuerbescheid",
)
# §6.2.1, in its order (by group).
NEW_SUBTYPES = (
    # property and purchase
    "land_register_extract",
    "declaration_of_division",
    "land_charge_deed",
    "energy_certificate",
    "building_records",
    "insurance_policy",
    "management_contract",
    "service_contract",
    # WEG
    "weg_minutes",
    "weg_invitation",
    "special_levy",
    # loan
    "repayment_schedule",
    "loan_terms_change",
    "loan_statement",
    # tenancy
    "lease_amendment",
    "rent_increase",
    "termination_notice",
    "tenant_self_disclosure",
    "landlord_confirmation",
    "operating_costs_statement",
    "tenant_correspondence",
    # running costs
    "utility_bill",
    "quote",
    "chimney_sweep_notice",
    # income tax
    "prepayment_notice",
    "tax_return_copy",
    "donation_receipt",
    "childcare_invoice",
    "household_services_invoice",
    "insurance_contribution_certificate",
    "pension_contribution_certificate",
    "benefit_notice",
    "child_benefit_notice",
    "employment_contract",
    "employer_certificate",
    "work_expense_invoice",
    # household finances
    "depot_statement",
    "depot_report",
    "trade_confirmation",
    "capital_income_tax_certificate",
)
# 0020's document_extraction.kind list, in its order; proposal.kind is the same without 'manual'.
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
    "einkommensteuerbescheid",
)
OLD_PROPOSAL_KINDS = tuple(k for k in OLD_EXTRACTION_KINDS if k != "manual")
NEW_KINDS = ("general", "management_contract")


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
        f"SELECT 1 FROM document WHERE subtype IN ({_sql_list(NEW_SUBTYPES)})",
        "a document has a subtype of the catalogue",
    ),
    (
        "document_extraction",
        f"SELECT 1 FROM document_extraction WHERE kind IN ({_sql_list(NEW_KINDS)})",
        "an extraction is of the general reading or a Verwaltervertrag",
    ),
    (
        "proposal",
        f"SELECT 1 FROM proposal WHERE kind IN ({_sql_list(NEW_KINDS)})",
        "a proposal is of the general reading or a Verwaltervertrag",
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
    _set_value_list("document", "subtype", (*OLD_SUBTYPES, *NEW_SUBTYPES))
    _set_value_list("document_extraction", "kind", (*OLD_EXTRACTION_KINDS, *NEW_KINDS))
    _set_value_list("proposal", "kind", (*OLD_PROPOSAL_KINDS, *NEW_KINDS))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_value_list("proposal", "kind", OLD_PROPOSAL_KINDS)
    _set_value_list("document_extraction", "kind", OLD_EXTRACTION_KINDS)
    _set_value_list("document", "subtype", OLD_SUBTYPES)
