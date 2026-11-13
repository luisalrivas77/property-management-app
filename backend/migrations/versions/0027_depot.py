"""Phase 6 step 4: the depot (quirion).

Spec: docs/phases/phase-6.md D156 and D165 as amended in step 4 (§14.1, Q-O61).
What 0025's tables lack, found on quirion's real documents:
- Every quirion account prints its own IBAN: the Verrechnungskonto and each strategy's
  Vermögensverwaltungskonto. A transfer from a Sparkasse or DKB account to quirion names that
  IBAN, so D162 can link it only when the depot account carries it. account_identity_check
  (0025) is rebuilt so an account of kind `depot` may carry an IBAN beside its external_ref
  (D156 amended); a bank kind still needs one and any other kind still has none. The IBAN is
  set only when a person confirms the one a statement prints (the D176 (4) pattern).
- statement_import.format gains `depot_pdf`: the cash statement (Kontoauszug of a
  Vermögensverwaltungskonto or the Verrechnungskonto), read by code from its text layer as the
  card statement is (D176 (3)); the PDF statement path D165 (1) relied on was never built.
- allocation.method gains `matcher_depot`: the code that books a depot cash line by its booking
  text (D165 (1), rules.depot.categorise); `rule` needs a learned rule's id.
- Units moved between two own depots arrive as an Einbuchung and an Ausbuchung with one
  reference: document.subtype, document_extraction.kind and proposal.kind gain
  `securities_transfer`, and asset_movement.source gains it (a movement read from such a
  document names it).

The downgrade is refused while it would leave a row the old CHECKs refuse (the 0025 guard): a
depot account with an IBAN, a depot_pdf import, a matcher_depot allocation, a
securities_transfer document, reading, proposal or movement. It lifts FORCE RLS for each check,
since the owner has no workspace set.

Revision ID: 0027_depot
Revises: 0026_credit_card
Create Date: 2026-11-13
"""

from alembic import op

revision = "0027_depot"
down_revision = "0026_credit_card"
branch_labels = None
depends_on = None

BANK_KINDS = ("bank_current", "bank_savings")
# The lists as 0025 and 0026 left them, in their order.
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
    "land_register_extract",
    "declaration_of_division",
    "land_charge_deed",
    "energy_certificate",
    "building_records",
    "insurance_policy",
    "management_contract",
    "service_contract",
    "weg_minutes",
    "weg_invitation",
    "special_levy",
    "repayment_schedule",
    "loan_terms_change",
    "loan_statement",
    "financing_invoice",
    "lease_amendment",
    "rent_increase",
    "termination_notice",
    "tenant_self_disclosure",
    "landlord_confirmation",
    "operating_costs_statement",
    "tenant_correspondence",
    "utility_bill",
    "quote",
    "chimney_sweep_notice",
    "payment_proof",
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
    "depot_statement",
    "depot_report",
    "trade_confirmation",
    "capital_income_tax_certificate",
    "exchange_export",
    "distribution_note",
)
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
    "payment_proof",
    "trade_confirmation",
    "distribution_note",
    "depot_report",
)
OLD_METHODS = (
    "rule",
    "matcher_rent",
    "matcher_loan",
    "matcher_owner",
    "matcher_hausgeld",
    "document_match",
    "llm",
    "user",
    "matcher_transfer",
    "matcher_card",
)
OLD_FORMATS = ("csv_camt", "camt", "pdf", "dkb_csv", "card_pdf")
OLD_MOVEMENT_SOURCES = ("csv", "trade_confirmation", "distribution_note", "report", "user")
NEW = "securities_transfer"
NEW_METHOD = "matcher_depot"
NEW_FORMAT = "depot_pdf"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _identity(depot_iban: bool) -> str:
    """account_identity_check: a bank kind has an IBAN and no external reference; a depot may
    carry an IBAN (0027) or not (0025); every other kind has none."""
    others = "kind = 'depot' OR " if depot_iban else ""
    return f"""
ALTER TABLE account DROP CONSTRAINT account_identity_check;
ALTER TABLE account ADD CONSTRAINT account_identity_check CHECK (
    (iban_hmac IS NULL) = (iban_masked IS NULL)
    AND (external_ref_hmac IS NULL) = (external_ref_masked IS NULL)
    AND (kind NOT IN ({_sql_list(BANK_KINDS)}) OR iban_hmac IS NOT NULL)
    AND ({others}kind IN ({_sql_list(BANK_KINDS)}) OR iban_hmac IS NULL)
    AND (kind NOT IN ({_sql_list(BANK_KINDS)}) OR external_ref_hmac IS NULL)
);
"""


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


def _set_checks(new: bool) -> None:
    def pick(old: tuple[str, ...], added: str) -> tuple[str, ...]:
        return (*old, added) if new else old

    kinds = pick(OLD_EXTRACTION_KINDS, NEW)
    _set_value_list("document", "subtype", pick(OLD_SUBTYPES, NEW))
    _set_value_list("document_extraction", "kind", kinds)
    _set_value_list("proposal", "kind", tuple(k for k in kinds if k != "manual"))
    _set_value_list("asset_movement", "source", pick(OLD_MOVEMENT_SOURCES, NEW))
    _set_value_list("allocation", "method", pick(OLD_METHODS, NEW_METHOD))
    _set_value_list("statement_import", "format", pick(OLD_FORMATS, NEW_FORMAT))
    op.execute(_identity(depot_iban=new))


_DATA_CHECKS = (
    (
        "account",
        "SELECT 1 FROM account WHERE kind = 'depot' AND iban_hmac IS NOT NULL",
        "a depot account carries an IBAN",
    ),
    (
        "statement_import",
        f"SELECT 1 FROM statement_import WHERE format = '{NEW_FORMAT}'",
        "an import is of format depot_pdf",
    ),
    (
        "allocation",
        f"SELECT 1 FROM allocation WHERE method = '{NEW_METHOD}'",
        "an allocation was booked by matcher_depot",
    ),
    ("document", f"SELECT 1 FROM document WHERE subtype = '{NEW}'", "a securities_transfer"),
    (
        "document_extraction",
        f"SELECT 1 FROM document_extraction WHERE kind = '{NEW}'",
        "a securities_transfer reading",
    ),
    ("proposal", f"SELECT 1 FROM proposal WHERE kind = '{NEW}'", "a securities_transfer proposal"),
    (
        "asset_movement",
        f"SELECT 1 FROM asset_movement WHERE source = '{NEW}'",
        "a movement read from a securities_transfer",
    ),
)
_CHECKED = tuple(dict.fromkeys(t for t, _, _ in _DATA_CHECKS))
REFUSE_IF_DATA = (
    "DO $$\nBEGIN"
    + "".join(f"\n    ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;" for table in _CHECKED)
    + "".join(
        f"""
    IF EXISTS ({query}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: {what}'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;"""
        for _, query, what in _DATA_CHECKS
    )
    + "".join(f"\n    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" for table in _CHECKED)
    + "\nEND\n$$;"
)


def upgrade() -> None:
    _set_checks(new=True)
    op.execute(
        "COMMENT ON COLUMN account.iban_hmac IS 'A bank account''s IBAN, keyed HMAC; on a depot"
        " (0027, D156 amended) the IBAN its statement prints, set when a person confirms it.';"
    )


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_checks(new=False)
