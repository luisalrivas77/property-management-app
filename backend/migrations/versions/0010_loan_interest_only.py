"""Phase 2: loan.interest_only_before_first_payment.

A loan paid out in parts: until the first full annuity the lender debits interest only on
what it has paid out, a different amount each month. With this flag the repayment schedule
starts at first_payment_on (period 1 is the 30 days before it, not the time since the
payout), and the loan matcher books a debit from the loan between payout and first annuity
as loan_interest when its purpose states that interest exactly (rules.loan_schedule,
rules.matching; tests/fixtures/phase1/loan_schedule.yaml, phase2/matchers.yaml).

A boolean column, NOT NULL DEFAULT false, so every existing loan keeps its schedule. Runs as
the schema owner like every migration; no grant changes (0001 grants the app role UPDATE on
loan table-wide, which covers a new column). Mirrors hausbuch.schemas.loan.LoanTerms.

The downgrade is refused while any loan has the flag set: dropping the column would change
that loan's schedule and how its debits were booked, and a downgrade never rewrites data. It
lifts FORCE RLS for the check, since the owner has no workspace set (as 0007 to 0009).

Revision ID: 0010_loan_interest_only
Revises: 0009_payout_price_categories
Create Date: 2026-11-08
"""

from alembic import op

revision = "0010_loan_interest_only"
down_revision = "0009_payout_price_categories"
branch_labels = None
depends_on = None

COLUMN = "interest_only_before_first_payment"

REFUSE_FLAGGED_LOANS = f"""
DO $$
BEGIN
    ALTER TABLE loan NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM loan WHERE {COLUMN}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a loan is flagged interest only'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Clear {COLUMN} on every loan first; its schedule would change.';
    END IF;
    ALTER TABLE loan FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(f"ALTER TABLE loan ADD COLUMN {COLUMN} boolean NOT NULL DEFAULT false")
    op.execute(
        f"COMMENT ON COLUMN loan.{COLUMN} IS 'Before first_payment_on the lender charges "
        "interest only (a loan paid out in parts); the annuity schedule starts at "
        "first_payment_on.'"
    )


def downgrade() -> None:
    op.execute(REFUSE_FLAGGED_LOANS)
    op.execute(f"ALTER TABLE loan DROP COLUMN {COLUMN}")
