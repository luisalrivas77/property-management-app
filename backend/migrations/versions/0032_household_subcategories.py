"""Phase 6: household subcategories inside the spending groups (D159 amended 14 Nov 2026).

Luis: "I want to be as detailed as possible", then "Let's recategorize everything". 42 codes
join the category CHECKs of `allocation.category`, `matching_rule.category` and
`matching_rule_categories_check` (0012's element CHECK), the 0029 pattern: subcategories of
the existing spending groups (Abos, Mobilität, Lebensmittel, Essen gehen, Wohnen,
Versicherungen, Gesundheit, Freizeit, Reisen, Familie, Telefon, Sonstiges). All private
spending, on no tax form. Each group's old code (hh_subscriptions, hh_mobility, …) keeps its
code as the group's fallback, relabelled „Sonstige …“, so no existing row or rule changes
meaning.

The downgrade is refused while a row or a learned rule uses a new code (the 0025 guard),
since the old CHECKs would refuse it and a downgrade never drops data. It lifts FORCE RLS for
each check, since the owner has no workspace set, and restores it (an exception rolls the
whole statement back, FORCE included).

Revision ID: 0032_household_subcategories
Revises: 0031_fuel_category
Create Date: 2026-11-14
"""

import json

from alembic import op

revision = "0032_household_subcategories"
down_revision = "0031_fuel_category"
branch_labels = None
depends_on = None

# 0031's list (OLD_CATEGORIES + NEW_CATEGORIES there), in its order.
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
    "hh_fuel",
)
NEW_CATEGORIES = (
    "hh_streaming",
    "hh_music_audio",
    "hh_software",
    "hh_cloud",
    "hh_gaming",
    "hh_news",
    "hh_memberships",
    "hh_public_transport",
    "hh_taxi",
    "hh_parking",
    "hh_car_service",
    "hh_car_tax",
    "hh_supermarket",
    "hh_drugstore",
    "hh_bakery",
    "hh_restaurant",
    "hh_cafe",
    "hh_sweets",
    "hh_fast_food",
    "hh_delivery",
    "hh_rent",
    "hh_energy",
    "hh_broadcast_fee",
    "hh_flat_search",
    "hh_ins_car",
    "hh_ins_liability_contents",
    "hh_ins_health",
    "hh_ins_life",
    "hh_pharmacy",
    "hh_doctor",
    "hh_dental_optical",
    "hh_outings",
    "hh_books_media",
    "hh_hobbies",
    "hh_lodging",
    "hh_flights",
    "hh_kids_gear",
    "hh_toys",
    "hh_pregnancy_birth",
    "hh_mobile_phone",
    "hh_internet",
    "hh_postage",
)


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
        "an allocation uses a category of 0032",
    ),
    (
        "matching_rule",
        f"SELECT 1 FROM matching_rule WHERE category IN ({_sql_list(NEW_CATEGORIES)})"
        f" OR {_NEW_CATEGORY_IN_RULE}",
        "a learned rule uses a category of 0032",
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
