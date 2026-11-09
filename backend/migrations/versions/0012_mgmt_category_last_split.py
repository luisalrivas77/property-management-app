"""Phase 2: category property_management and split method as_last_split.

Spec: docs/phases/phase-2.md D51.
- `property_management` (deductible): the landlord's own Mietverwaltung
  (Sondereigentumsverwaltung) fee, distinct from hausgeld_verwaltung, the WEG administrator's
  share inside the Hausgeld. Mirrors hausbuch.schemas.allocation.AllocationCategory. As 0009
  did, this replaces the CHECK on allocation.category and on matching_rule.category with the
  extended list; both were column constraints in 0007, so Postgres named them
  allocation_category_check and matching_rule_category_check, and they keep their names.
- `as_last_split`: a rule learned from a split answer that no other split method fits. Its
  kind is the answer's category set, stored in the new column matching_rule.categories (a
  jsonb array of two or more known categories, exactly for as_last_split); never an amount
  (D25: the table still has no money column). At match time the parts are copied from the
  latest confirmed line of that set from the same counterparty, read from the ledger
  (rules.matching.apply_rule). The split_method CHECK (0007's column constraint,
  matching_rule_split_method_check) gains the value; 0007's
  `(split_method = 'single_category') = (category IS NOT NULL)` stays valid, since an
  as_last_split rule has no category.
  The column is jsonb, like matcher; NULL for every other split method (the app writes SQL
  NULL, never a JSON null: tables.py declares it none_as_null). Its element CHECK
  (matching_rule_categories_check) lists the categories too, so a later category added to
  the taxonomy must widen it as well as the two category CHECKs.
Runs as the schema owner like every migration; no grant changes (0007 grants the app role
INSERT and UPDATE on matching_rule table-wide, which cover a new column).

The downgrade restores 0011's state and is refused while any allocation or rule uses
property_management or any rule is as_last_split: those rows would violate the old CHECKs or
lose their kind, and a downgrade never rewrites data. It lifts FORCE RLS for the check, since
the owner has no workspace set (as 0007 to 0011).

Revision ID: 0012_mgmt_category_last_split
Revises: 0011_rule_same_amount_only
Create Date: 2026-11-09
"""

import json

from alembic import op

revision = "0012_mgmt_category_last_split"
down_revision = "0011_rule_same_amount_only"
branch_labels = None
depends_on = None

TABLES = ("allocation", "matching_rule")

# 0009's list, in its order.
OLD_CATEGORY_NAMES = (
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
)
ADDED_CATEGORY = "property_management"
NEW_CATEGORY_NAMES = (*OLD_CATEGORY_NAMES, ADDED_CATEGORY)

OLD_SPLIT_METHODS = (
    "'single_category', 'by_rent_schedule', 'by_loan_schedule', 'by_wirtschaftsplan', "
    "'owner_transfer'"
)
ADDED_SPLIT_METHOD = "as_last_split"
NEW_SPLIT_METHODS = f"{OLD_SPLIT_METHODS}, '{ADDED_SPLIT_METHOD}'"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _set_categories(names: tuple[str, ...]) -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_category_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_category_check"
            f" CHECK (category IN ({_sql_list(names)}))"
        )


def _set_split_methods(values: str) -> None:
    op.execute("ALTER TABLE matching_rule DROP CONSTRAINT matching_rule_split_method_check")
    op.execute(
        "ALTER TABLE matching_rule ADD CONSTRAINT matching_rule_split_method_check"
        f" CHECK (split_method IN ({values}))"
    )


KNOWN_CATEGORIES_JSON = json.dumps(list(NEW_CATEGORY_NAMES))

REFUSE_NEW_VALUES = f"""
DO $$
BEGIN
    ALTER TABLE allocation NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE matching_rule NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM allocation WHERE category = '{ADDED_CATEGORY}')
       OR EXISTS (SELECT 1 FROM matching_rule WHERE category = '{ADDED_CATEGORY}')
       OR EXISTS (SELECT 1 FROM matching_rule WHERE split_method = '{ADDED_SPLIT_METHOD}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a row uses a new category or split'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'An allocation or a learned rule uses property_management, or a rule '
                         'is as_last_split; allocations are append-only and are not rewritten.';
    END IF;
    ALTER TABLE allocation FORCE ROW LEVEL SECURITY;
    ALTER TABLE matching_rule FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    _set_categories(NEW_CATEGORY_NAMES)
    _set_split_methods(NEW_SPLIT_METHODS)
    op.execute("ALTER TABLE matching_rule ADD COLUMN categories jsonb")
    op.execute(
        "ALTER TABLE matching_rule ADD CONSTRAINT matching_rule_categories_check CHECK ("
        " categories IS NULL OR (jsonb_typeof(categories) = 'array'"
        " AND jsonb_array_length(categories) >= 2"
        f" AND categories <@ '{KNOWN_CATEGORIES_JSON}'::jsonb))"
    )
    op.execute(
        "ALTER TABLE matching_rule ADD CONSTRAINT matching_rule_categories_split_check"
        f" CHECK ((split_method = '{ADDED_SPLIT_METHOD}') = (categories IS NOT NULL))"
    )
    op.execute(
        "COMMENT ON COLUMN matching_rule.categories IS 'D51: as_last_split''s kind, the "
        "category set of the answer it was learned from (never an amount); a hit copies the "
        "parts of the latest confirmed line of this set from the same counterparty.'"
    )


def downgrade() -> None:
    op.execute(REFUSE_NEW_VALUES)
    op.execute("ALTER TABLE matching_rule DROP CONSTRAINT matching_rule_categories_split_check")
    op.execute("ALTER TABLE matching_rule DROP CONSTRAINT matching_rule_categories_check")
    op.execute("ALTER TABLE matching_rule DROP COLUMN categories")
    _set_split_methods(OLD_SPLIT_METHODS)
    _set_categories(OLD_CATEGORY_NAMES)
