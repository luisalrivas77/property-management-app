"""Phase 2: matching_rule.same_amount_only.

Spec: docs/phases/phase-2.md D49. A learned rule from a counterparty that sends two kinds of
payment (a Hausverwaltung debiting Hausgeld and its management fee from one IBAN) can be
learned as "only payments of this amount". The flag is a boolean, never an amount (D25 still
holds: the table has no money column): at match time a flagged single_category hit stands
only when the line's amount equals the latest confirmed line of the rule's category from the
same counterparty, read from the ledger; any other amount becomes a proposal for review
(rules.matching.rule_answer; tests/fixtures/phase2/rule_history.yaml).

A boolean column, NOT NULL DEFAULT false, so every existing rule keeps booking any amount.
Runs as the schema owner like every migration; no grant changes (0007 grants the app role
UPDATE on matching_rule table-wide, and INSERT, which cover a new column).

The downgrade is refused while any rule has the flag set: dropping the column would let that
rule book every amount, and a downgrade never rewrites data. It lifts FORCE RLS for the
check, since the owner has no workspace set (as 0007 to 0010).

Revision ID: 0011_rule_same_amount_only
Revises: 0010_loan_interest_only
Create Date: 2026-11-08
"""

from alembic import op

revision = "0011_rule_same_amount_only"
down_revision = "0010_loan_interest_only"
branch_labels = None
depends_on = None

COLUMN = "same_amount_only"

REFUSE_FLAGGED_RULES = f"""
DO $$
BEGIN
    ALTER TABLE matching_rule NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM matching_rule WHERE {COLUMN}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a rule is flagged same amount only'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Clear {COLUMN} on every rule first; it would book every amount.';
    END IF;
    ALTER TABLE matching_rule FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(f"ALTER TABLE matching_rule ADD COLUMN {COLUMN} boolean NOT NULL DEFAULT false")
    op.execute(
        f"COMMENT ON COLUMN matching_rule.{COLUMN} IS 'D49: a single_category hit stands only "
        "when the amount equals the latest confirmed line of its category from the same "
        "counterparty (read from the ledger, never stored here); else it is a proposal.'"
    )


def downgrade() -> None:
    op.execute(REFUSE_FLAGGED_RULES)
    op.execute(f"ALTER TABLE matching_rule DROP COLUMN {COLUMN}")
