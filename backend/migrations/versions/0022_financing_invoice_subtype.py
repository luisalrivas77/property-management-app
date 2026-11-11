"""Phase 2 D138: the subtype `financing_invoice` for a whole financing bill.

Spec: docs/phases/phase-2.md D138, §6.2.1.
- `document.subtype` gains `financing_invoice`. 0001 wrote this CHECK inline, so Postgres
  named it document_subtype_check (0004, 0018, 0020 and 0021 relied on the same naming). It is
  dropped by name without IF EXISTS, so a wrong name fails the migration loudly. Mirrors
  hausbuch.schemas.enums.DocumentSubtype.
- No table, column or kind: a financing bill is read by its type's reader (invoice or Bescheid).

The downgrade is refused while a document carries the new subtype (as 0021). It lifts FORCE RLS
for the check, since the owner has no workspace set, and then restores 0021's CHECK.

Revision ID: 0022_financing_invoice_subtype
Revises: 0021_document_catalogue
Create Date: 2026-11-11
"""

from alembic import op

revision = "0022_financing_invoice_subtype"
down_revision = "0021_document_catalogue"
branch_labels = None
depends_on = None

# 0021's document.subtype list, in its order.
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
NEW_SUBTYPES = ("financing_invoice",)


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_subtypes(names: tuple[str, ...]) -> None:
    """Replace document.subtype's value-list CHECK, document_subtype_check (no IF EXISTS)."""
    op.execute("ALTER TABLE document DROP CONSTRAINT document_subtype_check")
    op.execute(
        "ALTER TABLE document ADD CONSTRAINT document_subtype_check"
        f" CHECK (subtype IN ({_sql_list(names)}))"
    )


REFUSE_IF_DATA = f"""DO $$
BEGIN
    ALTER TABLE document NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM document WHERE subtype IN ({_sql_list(NEW_SUBTYPES)})) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a document is a financing bill'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE document FORCE ROW LEVEL SECURITY;
END
$$;"""


def upgrade() -> None:
    _set_subtypes((*OLD_SUBTYPES, *NEW_SUBTYPES))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_subtypes(OLD_SUBTYPES)
