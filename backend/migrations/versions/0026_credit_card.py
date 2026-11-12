"""Phase 6 step 2b: the credit card as an account fed by its PDF statements.

Spec: docs/phases/phase-6.md D176, §14.1 („The card statement“), §15 step 2b.
- `account.kind` gains `credit_card` (the kind CHECK, auto-named account_kind_check by 0025's
  inline CHECK, rebuilt). A card account has no IBAN (account_identity_check of 0025 already
  says so for every kind that is not a bank kind) and is routed by the issuer's customer number
  in external_ref_masked / external_ref_hmac (D156, D164).
- `account.payment_iban_masked` and `account.payment_iban_hmac` (D176 (4)): the IBAN the
  household pays the card to, printed on the statement, masked and keyed-HMAC'd like the
  account's own IBAN columns (0006), set only when a person confirms it; only a card account
  carries one (account_payment_iban_check).
- Category `hh_interest_fees` (D176 (3), „Zinsen und Gebühren“) in allocation.category,
  matching_rule.category and matching_rule_categories_check (0012's element CHECK), the 0025
  pattern.
- Two CHECKs D176 does not name but its enums need (listed in the step 2b report):
  statement_import.format gains `card_pdf` (the statement format of D176 (3)), and
  allocation.method gains `matcher_card` (the code that books an interest or fee line,
  D176 (3); `rule` needs a learned rule's id, and these lines have no payee to learn).

The downgrade is refused while it would leave a row the old CHECKs refuse (the 0025 guard): a
card account, a payment IBAN, a card_pdf import, or the new category or method in use. It lifts
FORCE RLS for each check, since the owner has no workspace set.

Revision ID: 0026_credit_card
Revises: 0025_household_accounts
Create Date: 2026-11-12
"""

import json

from alembic import op

revision = "0026_credit_card"
down_revision = "0025_household_accounts"
branch_labels = None
depends_on = None

# 0025's lists, in their order.
OLD_ACCOUNT_KINDS = ("bank_current", "bank_savings", "depot", "exchange", "wallet")
NEW_ACCOUNT_KINDS = ("credit_card",)
OLD_CATEGORIES = (
    "rent_cold",
    "operating_costs_prepayment",
    "operating_costs_settlement",
    "deposit_in",
    "deposit_out",
    "owner_transfer_in",
    "owner_transfer_out",
    "loan_interest",
    "loan_principal",
    "loan_fees",
    "hausgeld_umlagefaehig",
    "hausgeld_verwaltung",
    "hausgeld_ruecklage",
    "grundsteuer",
    "insurance",
    "repairs",
    "renovation",
    "acquisition_cost",
    "professional_fees",
    "bank_fees",
    "other_income",
    "other_expense",
    "uncategorized",
    "loan_disbursement",
    "purchase_price",
    "property_management",
    "electricity",
    "hh_salary",
    "hh_child_benefit",
    "hh_tax_refund",
    "hh_capital_income",
    "hh_other_income",
    "hh_housing",
    "hh_groceries",
    "hh_family_child",
    "hh_mobility",
    "hh_insurance",
    "hh_subscriptions",
    "hh_leisure_travel",
    "hh_health",
    "hh_other",
    "hh_donation",
    "hh_household_services",
    "hh_childcare",
    "hh_property_cost",
    "hh_tax_payment",
    "internal_transfer_in",
    "internal_transfer_out",
    "hh_cash",
    "hh_credit_card",
    "security_purchase",
    "security_sale",
    "distribution",
    "capital_income_tax",
    "depot_fee",
)
NEW_CATEGORIES = ("hh_interest_fees",)
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
)
NEW_METHODS = ("matcher_card",)
OLD_FORMATS = ("csv_camt", "camt", "pdf", "dkb_csv")
NEW_FORMATS = ("card_pdf",)

PAYMENT_IBAN = r"""
ALTER TABLE account
    ADD COLUMN payment_iban_masked text
        CHECK (payment_iban_masked ~ '^[A-Z]{2}[0-9]{2} \*{4} [A-Z0-9]{4}$'),
    ADD COLUMN payment_iban_hmac bytea CHECK (octet_length(payment_iban_hmac) = 32),
    ADD CONSTRAINT account_payment_iban_check CHECK (
        (payment_iban_hmac IS NULL) = (payment_iban_masked IS NULL)
        AND (payment_iban_hmac IS NULL OR kind = 'credit_card')
    );
COMMENT ON COLUMN account.payment_iban_hmac IS
    'D176 (4): the IBAN the household pays a credit card to, printed on its statement and '
    'confirmed by a person; keyed HMAC like iban_hmac.';
"""


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


def _set_categories(names: tuple[str, ...]) -> None:
    _set_value_list("allocation", "category", names)
    _set_value_list("matching_rule", "category", names)
    known = json.dumps(list(names))
    op.execute("ALTER TABLE matching_rule DROP CONSTRAINT matching_rule_categories_check")
    op.execute(
        "ALTER TABLE matching_rule ADD CONSTRAINT matching_rule_categories_check CHECK ("
        " categories IS NULL OR (jsonb_typeof(categories) = 'array'"
        " AND jsonb_array_length(categories) >= 2"
        f" AND categories <@ '{known}'::jsonb))"
    )


def _set_checks(new: bool) -> None:
    def pick(old: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
        return (*old, *added) if new else old

    _set_value_list("account", "kind", pick(OLD_ACCOUNT_KINDS, NEW_ACCOUNT_KINDS))
    _set_categories(pick(OLD_CATEGORIES, NEW_CATEGORIES))
    _set_value_list("allocation", "method", pick(OLD_METHODS, NEW_METHODS))
    _set_value_list("statement_import", "format", pick(OLD_FORMATS, NEW_FORMATS))


_NEW_CATEGORY_IN_RULE = " OR ".join(f"categories @> '[\"{c}\"]'::jsonb" for c in NEW_CATEGORIES)
_DATA_CHECKS = (
    (
        "account",
        f"SELECT 1 FROM account WHERE kind IN ({_sql_list(NEW_ACCOUNT_KINDS)})"
        " OR payment_iban_hmac IS NOT NULL",
        "an account is a credit card",
    ),
    (
        "allocation",
        f"SELECT 1 FROM allocation WHERE category IN ({_sql_list(NEW_CATEGORIES)})"
        f" OR method IN ({_sql_list(NEW_METHODS)})",
        "an allocation uses hh_interest_fees or matcher_card",
    ),
    (
        "matching_rule",
        f"SELECT 1 FROM matching_rule WHERE category IN ({_sql_list(NEW_CATEGORIES)})"
        f" OR {_NEW_CATEGORY_IN_RULE}",
        "a learned rule uses hh_interest_fees",
    ),
    (
        "statement_import",
        f"SELECT 1 FROM statement_import WHERE format IN ({_sql_list(NEW_FORMATS)})",
        "an import is of format card_pdf",
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
    op.execute(PAYMENT_IBAN)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute("""
ALTER TABLE account
    DROP CONSTRAINT account_payment_iban_check,
    DROP COLUMN payment_iban_hmac,
    DROP COLUMN payment_iban_masked;
""")
    _set_checks(new=False)
