"""Phase 4: category electricity.

Spec: docs/phases/phase-4.md D92.
- `electricity` (deductible): the flat's own electricity bill or monthly Abschlag, paid by the
  landlord because the rent's Nebenkostenpauschale covers it; the Hausgeld's Allgemeinstrom
  stays hausgeld_umlagefaehig. Mirrors hausbuch.schemas.allocation.AllocationCategory. As 0009
  and 0012 did, this replaces the CHECK on allocation.category and on matching_rule.category
  with the extended list (allocation_category_check, matching_rule_category_check, 0007's
  column constraints, which keep their names), and, as 0012's note asks of every later
  category, the element CHECK on matching_rule.categories (matching_rule_categories_check,
  0012), so an as_last_split rule's kind may name it.
Runs as the schema owner like every migration; no grant changes.

The downgrade restores 0016's state and is refused while any allocation or rule uses
electricity, as its category or inside an as_last_split rule's categories: those rows would
violate the old CHECKs, and a downgrade never rewrites data. It lifts FORCE RLS for the check,
since the owner has no workspace set (as 0007 to 0016).

Revision ID: 0017_electricity_category
Revises: 0016_performance
Create Date: 2026-11-09
"""

import json

from alembic import op

revision = "0017_electricity_category"
down_revision = "0016_performance"
branch_labels = None
depends_on = None

TABLES = ("allocation", "matching_rule")

# 0012's list, in its order.
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
    "property_management",
)
ADDED_CATEGORY = "electricity"
NEW_CATEGORY_NAMES = (*OLD_CATEGORY_NAMES, ADDED_CATEGORY)


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _set_categories(names: tuple[str, ...]) -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_category_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_category_check"
            f" CHECK (category IN ({_sql_list(names)}))"
        )
    # 0012's element CHECK, with the same shape and the new list.
    known = json.dumps(list(names))
    op.execute("ALTER TABLE matching_rule DROP CONSTRAINT matching_rule_categories_check")
    op.execute(
        "ALTER TABLE matching_rule ADD CONSTRAINT matching_rule_categories_check CHECK ("
        " categories IS NULL OR (jsonb_typeof(categories) = 'array'"
        " AND jsonb_array_length(categories) >= 2"
        f" AND categories <@ '{known}'::jsonb))"
    )


REFUSE_NEW_VALUES = f"""
DO $$
BEGIN
    ALTER TABLE allocation NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE matching_rule NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM allocation WHERE category = '{ADDED_CATEGORY}')
       OR EXISTS (SELECT 1 FROM matching_rule WHERE category = '{ADDED_CATEGORY}')
       OR EXISTS (
           SELECT 1 FROM matching_rule WHERE categories @> '["{ADDED_CATEGORY}"]'::jsonb
       ) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a row uses the new category'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'An allocation or a learned rule uses electricity, as its category or '
                         'in its categories; allocations are append-only and are not rewritten.';
    END IF;
    ALTER TABLE allocation FORCE ROW LEVEL SECURITY;
    ALTER TABLE matching_rule FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    _set_categories(NEW_CATEGORY_NAMES)


def downgrade() -> None:
    op.execute(REFUSE_NEW_VALUES)
    _set_categories(OLD_CATEGORY_NAMES)
