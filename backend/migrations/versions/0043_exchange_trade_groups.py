"""Phase 6 D178 amended (3) / phase 7 D188 (1): an exchange trade is one movement group
(docs/phases/phase-6.md D178 amended (3), docs/phases/phase-7.md §15.1).

An exchange's trade (Binance: the acquisition, the disposal and the fee movements of one
second; BISON: its one trade row) is written as one `movement_group` of kind `swap`, so phase
7's lot rules read its legs together: the given leg's value when the received coin has no
price for the day, and the fee movements as part of the cost (D188 (1), (4)). 0041 made
`movement_group.chain` and `tx_hash` NOT NULL, since it only knew chain transactions; an
exchange trade has neither. So:

- `movement_group.chain` and `movement_group.tx_hash` become nullable;
- `movement_group_chainless_check`: chain and tx_hash are null together, and a group without
  a chain is a `swap` (the only kind an exchange writes). The chain CHECK of 0041 stays (a NULL
  passes it, a non-null value must still be one of the five chains).

Nothing else changes: the group's append-only trigger (group_forward), its grants and its
row-level security are 0041's. `asset_movement` is unchanged: a movement already stored is
grouped by a new row that supersedes it (`hausbuch group-exchange-trades`), never by an UPDATE
(0025's movement_forward still allows only status and superseded_by).

The downgrade is refused while a group without a chain exists: NOT NULL would refuse it, and a
downgrade never drops data. It lifts FORCE RLS for the check, since the owner has no workspace
set, and restores it (0040's and 0042's guard).

Revision ID: 0043_exchange_trade_groups
Revises: 0042_quantity_scale
Create Date: 2026-11-16
"""

from alembic import op

revision = "0043_exchange_trade_groups"
down_revision = "0042_quantity_scale"
branch_labels = None
depends_on = None

CHAINLESS_CHECK = "movement_group_chainless_check"
EXCHANGE_GROUP_KIND = "swap"  # hausbuch.rules.exchange.TRADE_GROUP_KIND

UPGRADE = f"""
ALTER TABLE movement_group
    ALTER COLUMN chain DROP NOT NULL,
    ALTER COLUMN tx_hash DROP NOT NULL,
    ADD CONSTRAINT {CHAINLESS_CHECK} CHECK (
        (chain IS NULL) = (tx_hash IS NULL)
        AND (chain IS NOT NULL OR kind = '{EXCHANGE_GROUP_KIND}')
    );
COMMENT ON TABLE movement_group IS
    'D183: one transaction that touches an own address, with one asset_movement per leg and '
    'one for the fee; since 0043 also one exchange trade (chain and tx_hash null, kind swap). '
    'Status proposed -> confirmed, superseded_by set once.';
"""

REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE movement_group NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM movement_group WHERE chain IS NULL OR tx_hash IS NULL) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: an exchange trade is a movement group'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE movement_group FORCE ROW LEVEL SECURITY;
END
$$;
"""

DOWNGRADE = f"""
ALTER TABLE movement_group
    DROP CONSTRAINT {CHAINLESS_CHECK},
    ALTER COLUMN chain SET NOT NULL,
    ALTER COLUMN tx_hash SET NOT NULL;
COMMENT ON TABLE movement_group IS
    'D183: one transaction that touches an own address, with one asset_movement per leg and '
    'one for the fee. Status proposed -> confirmed, superseded_by set once.';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(DOWNGRADE)
