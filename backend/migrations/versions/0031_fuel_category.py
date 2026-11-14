"""Phase 6: a household category for car fuel (D159 amended 14 Nov 2026, Luis).

Luis: "I want to add Gas (car) category". Fuel was booked under „Mobilität“ (`hh_mobility`)
with public transport, taxi, parking and car repairs; `hh_fuel` („Tanken (Auto)“) joins the
category CHECKs of `allocation.category`, `matching_rule.category` and
`matching_rule_categories_check` (0012's element CHECK), the 0029 pattern. It is private
spending in the Mobilität group, on no tax form; `hh_mobility` keeps its code and label.

Phase 7's spec reserved 0031 for its step 0; it moves to 0032.

The downgrade is refused while a row or a learned rule uses hh_fuel (the 0025 guard), since
the old CHECKs would refuse it and a downgrade never drops data. It lifts FORCE RLS for each
check, since the owner has no workspace set.

Revision ID: 0031_fuel_category
Revises: 0030_balance_correction
Create Date: 2026-11-14
"""

import json

from alembic import op

revision = "0031_fuel_category"
down_revision = "0030_balance_correction"
branch_labels = None
depends_on = None

# 0029's list (OLD_CATEGORIES + NEW_CATEGORIES there), in its order; 0030 changed none.
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
    "hh_interest_fees",
    "hh_personal_care",
    "hh_clothing",
    "hh_dining",
    "hh_home",
    "hh_electronics",
    "hh_online_shopping",
    "hh_fees",
    "hh_gifts",
    "hh_family_transfers",
    "hh_telecom",
    "hh_work_education",
    "hh_sport",
    "hh_travel",
    "hh_investment",
    "hh_refund",
)
NEW_CATEGORIES = ("hh_fuel",)


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


_NEW_CATEGORY_IN_RULE = " OR ".join(f"categories @> '[\"{c}\"]'::jsonb" for c in NEW_CATEGORIES)
_DATA_CHECKS = (
    (
        "allocation",
        f"SELECT 1 FROM allocation WHERE category IN ({_sql_list(NEW_CATEGORIES)})",
        "an allocation uses a category of 0031",
    ),
    (
        "matching_rule",
        f"SELECT 1 FROM matching_rule WHERE category IN ({_sql_list(NEW_CATEGORIES)})"
        f" OR {_NEW_CATEGORY_IN_RULE}",
        "a learned rule uses a category of 0031",
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
    _set_categories((*OLD_CATEGORIES, *NEW_CATEGORIES))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_categories(OLD_CATEGORIES)
