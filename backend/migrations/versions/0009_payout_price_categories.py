"""Phase 2: two categories, loan_disbursement and purchase_price.

The rental account received the loan's payout from the lender and paid the purchase price to
the seller from it; the fixed taxonomy (phase-2.md §8.2, D24) had no category for either.
`loan_disbursement` is pass_through (borrowed money, neither income nor a cost; the principal
is on the loan record). `purchase_price` is capitalized like `acquisition_cost`: the price
enters the AfA basis through the property record, never as a cost of the year paid. Both are
one-offs and never auto-confirmed (rules.review.THRESHOLDS). Mirrors
hausbuch.schemas.allocation.AllocationCategory.

This revision replaces 0007's CHECK on allocation.category and on matching_rule.category
with the extended list. Both were column constraints, so Postgres named them
allocation_category_check and matching_rule_category_check; they keep their names. Runs as
the schema owner like every migration; no grant changes.

The downgrade restores 0007's list and is refused while any allocation or learned rule uses
one of the two categories: those rows would violate it, and a downgrade never rewrites data.
It lifts FORCE RLS for the check, since the owner has no workspace set (as 0007 and 0008).

Revision ID: 0009_payout_price_categories
Revises: 0008_rule_matcher_erasure
Create Date: 2026-11-07
"""

from alembic import op

revision = "0009_payout_price_categories"
down_revision = "0008_rule_matcher_erasure"
branch_labels = None
depends_on = None

TABLES = ("allocation", "matching_rule")

OLD_CATEGORIES = (
    "'rent_cold', 'operating_costs_prepayment', 'operating_costs_settlement', 'deposit_in', "
    "'deposit_out', 'owner_transfer_in', 'owner_transfer_out', 'loan_interest', "
    "'loan_principal', 'loan_fees', 'hausgeld_umlagefaehig', 'hausgeld_verwaltung', "
    "'hausgeld_ruecklage', 'grundsteuer', 'insurance', 'repairs', 'renovation', "
    "'acquisition_cost', 'professional_fees', 'bank_fees', 'other_income', 'other_expense', "
    "'uncategorized'"
)
ADDED = "'loan_disbursement', 'purchase_price'"
NEW_CATEGORIES = f"{OLD_CATEGORIES}, {ADDED}"


def _set_categories(values: str) -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_category_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_category_check"
            f" CHECK (category IN ({values}))"
        )


REFUSE_NEW_CATEGORIES = f"""
DO $$
BEGIN
    ALTER TABLE allocation NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE matching_rule NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM allocation WHERE category IN ({ADDED}))
       OR EXISTS (SELECT 1 FROM matching_rule WHERE category IN ({ADDED})) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a row uses a new category'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'An allocation or a learned rule uses loan_disbursement or '
                         'purchase_price; allocations are append-only and are not rewritten.';
    END IF;
    ALTER TABLE allocation FORCE ROW LEVEL SECURITY;
    ALTER TABLE matching_rule FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    _set_categories(NEW_CATEGORIES)


def downgrade() -> None:
    op.execute(REFUSE_NEW_CATEGORIES)
    _set_categories(OLD_CATEGORIES)
