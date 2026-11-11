"""Phase 3 D143: a payment proof linked to the bill it proves was paid.

Spec: docs/phases/phase-3.md D143.
- `document_link` gains `target_document_id`, the link target `document` (LinkTarget.DOCUMENT;
  the column is not named `document_id`, which is the link's own document). Like every other
  target it has a workspace-scoped composite foreign key, named document_link_target_document_fkey
  (not *_document_id_fkey: services.documents tells the link's own document's FK apart by that
  suffix), ON DELETE CASCADE as the link's own document has: removing either document removes
  the link. A CHECK refuses a document linked to itself. 0001's one-target CHECK
  (document_link_check) and its uniqueness (document_link_document_id_owner_id_property_id_unit_
  id_tena_key, Postgres's truncated name) are dropped by name without IF EXISTS, so a wrong
  name fails the migration loudly, and re-created with the new column. Row-level security is
  per table (0001's _rls), so the column needs no policy of its own.
- `document.subtype` gains `payment_proof`; `document_extraction.kind` and `proposal.kind` gain
  `payment_proof` (named <table>_<column>_check, as 0021 relied on). Mirrors
  hausbuch.schemas.enums.

The downgrade is refused while a link to a document, a payment proof, or an extraction or
proposal of the kind exists (as 0021, 0022): it lifts FORCE RLS for the check, since the owner
has no workspace set, and then restores 0022's constraints.

Revision ID: 0023_payment_proof
Revises: 0022_financing_invoice_subtype
Create Date: 2026-11-11
"""

from alembic import op

revision = "0023_payment_proof"
down_revision = "0022_financing_invoice_subtype"
branch_labels = None
depends_on = None

# 0022's document.subtype list, in its order.
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
    # 0022
    "financing_invoice",
)
# 0021's document_extraction.kind list (proposal.kind: the same without manual).
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
    "general",
    "management_contract",
)
OLD_PROPOSAL_KINDS = tuple(k for k in OLD_EXTRACTION_KINDS if k != "manual")
NEW = ("payment_proof",)

_OLD_TARGETS = "owner_id, property_id, unit_id, tenant_id, tenancy_id, loan_id"
_NEW_TARGETS = f"{_OLD_TARGETS}, target_document_id"
_UNIQUE = "document_link_document_id_owner_id_property_id_unit_id_tena_key"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


def _set_link_targets(targets: str) -> None:
    """0001's one-target CHECK and uniqueness over the given target columns (no IF EXISTS)."""
    op.execute("ALTER TABLE document_link DROP CONSTRAINT document_link_check")
    op.execute(f"ALTER TABLE document_link DROP CONSTRAINT {_UNIQUE}")
    op.execute(
        "ALTER TABLE document_link ADD CONSTRAINT document_link_check"
        f" CHECK (num_nonnulls({targets}) = 1)"
    )
    op.execute(
        f"ALTER TABLE document_link ADD CONSTRAINT {_UNIQUE}"
        f" UNIQUE NULLS NOT DISTINCT (document_id, {targets})"
    )


_DATA_CHECKS = (
    (
        "document_link",
        "SELECT 1 FROM document_link WHERE target_document_id IS NOT NULL",
        "a document is linked to a document",
    ),
    (
        "document",
        f"SELECT 1 FROM document WHERE subtype IN ({_sql_list(NEW)})",
        "a document is a payment proof",
    ),
    (
        "document_extraction",
        f"SELECT 1 FROM document_extraction WHERE kind IN ({_sql_list(NEW)})",
        "an extraction is of a payment proof",
    ),
    (
        "proposal",
        f"SELECT 1 FROM proposal WHERE kind IN ({_sql_list(NEW)})",
        "a proposal is of a payment proof",
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
    op.execute("ALTER TABLE document_link ADD COLUMN target_document_id uuid")
    op.execute(
        "ALTER TABLE document_link ADD CONSTRAINT document_link_target_document_fkey"
        " FOREIGN KEY (workspace_id, target_document_id)"
        " REFERENCES document (workspace_id, id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE document_link ADD CONSTRAINT document_link_not_itself_check"
        " CHECK (target_document_id IS NULL OR target_document_id <> document_id)"
    )
    _set_link_targets(_NEW_TARGETS)
    _set_value_list("document", "subtype", (*OLD_SUBTYPES, *NEW))
    _set_value_list("document_extraction", "kind", (*OLD_EXTRACTION_KINDS, *NEW))
    _set_value_list("proposal", "kind", (*OLD_PROPOSAL_KINDS, *NEW))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_value_list("proposal", "kind", OLD_PROPOSAL_KINDS)
    _set_value_list("document_extraction", "kind", OLD_EXTRACTION_KINDS)
    _set_value_list("document", "subtype", OLD_SUBTYPES)
    _set_link_targets(_OLD_TARGETS)
    op.execute("ALTER TABLE document_link DROP CONSTRAINT document_link_not_itself_check")
    op.execute("ALTER TABLE document_link DROP CONSTRAINT document_link_target_document_fkey")
    op.execute("ALTER TABLE document_link DROP COLUMN target_document_id")
