"""Phase 7 D182 (3) amended: a sync run that asks a new question keeps its original
(docs/phases/phase-7.md D182 (3), §15.1; review of PR #387, 16 Nov 2026).

0041 let a `wallet_sync` row hold a stored original (`import_id`, `document_id`) only with a
new movement: `CHECK (import_id IS NULL OR movements_new > 0)`, named `wallet_sync_check3` by
Postgres. A run whose only new item is a question card (a Bitcoin spend with two or more
unregistered outputs, D185 (2); a card has no movement) then stored nothing, and the card
could not be answered without asking the provider again (D192). The same CHECK refused a run
whose only new item was an unstorable transaction, which the sync already keeps the original
for. So:

- `wallet_sync_check3` is dropped. What a run's original brings that is new (a movement, a
  question card, an unstorable transaction) is the sync's rule, since a CHECK cannot see the
  groups a run wrote;
- `wallet_sync_original_check`: an original only on a succeeded run (a failed, throttled or
  refused run writes nothing).

Nothing else changes: `(import_id IS NULL) = (document_id IS NULL)` stays, and so do the
finish-once trigger, the grants and the row-level security of 0041.

The downgrade is refused while a run holds an original without a new movement: the old CHECK
would refuse that row, and a downgrade never drops data. It lifts FORCE RLS for the check,
since the owner has no workspace set, and restores it (0043's guard).

Revision ID: 0044_sync_original_for_questions
Revises: 0043_exchange_trade_groups
Create Date: 2026-11-16
"""

from alembic import op

revision = "0044_sync_original_for_questions"
down_revision = "0043_exchange_trade_groups"
branch_labels = None
depends_on = None

OLD_CHECK = "wallet_sync_check3"  # 0041's unnamed CHECK, named by Postgres
ORIGINAL_CHECK = "wallet_sync_original_check"

UPGRADE = f"""
ALTER TABLE wallet_sync
    DROP CONSTRAINT {OLD_CHECK},
    ADD CONSTRAINT {ORIGINAL_CHECK} CHECK (import_id IS NULL OR status = 'succeeded');
"""

REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE wallet_sync NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM wallet_sync WHERE import_id IS NOT NULL AND movements_new = 0) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a sync run keeps an original '
                        'without a new movement'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE wallet_sync FORCE ROW LEVEL SECURITY;
END
$$;
"""

DOWNGRADE = f"""
ALTER TABLE wallet_sync
    DROP CONSTRAINT {ORIGINAL_CHECK},
    ADD CONSTRAINT {OLD_CHECK} CHECK (import_id IS NULL OR movements_new > 0);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(DOWNGRADE)
