"""Phase 6 D292: a depot trade booked from its cash statement line.

Spec: docs/phases/phase-6.md D292 (1), (5). quirion issues no trade confirmation for a
savings-plan purchase or a rebalancing trade; its monthly cash statement prints the fund's ISIN
and units on each `security_purchase` and `security_sale` line, so the line itself becomes the
trade's movement (services/depot.book_statement_trades).

- asset_movement.source gains `depot_statement` (the value list as 0041 left it, plus the new
  value; rebuilt with the 0027/0041 pattern, the CHECK named asset_movement_source_check).
- asset_movement_depot_statement_check: such a movement names the cash line it was read from
  (`transaction_id`) and the statement's document (`document_id`), as 0025's documented-source
  CHECK asks of the other document sources.
- asset_movement_depot_statement_line_key: one live statement movement per cash line (a partial
  unique index), so the match job, the import hook and `hausbuch book-statement-trades` racing
  on one line can never book it twice. Only `depot_statement` rows are in it: a trade
  confirmation that supersedes such a movement keeps the line linked and is inserted before the
  old row can point at it, which an index over every source would refuse (0025's note on
  transfer_link).

The downgrade is refused while any movement uses `depot_statement` (the 0027 guard, lifting
FORCE RLS for the check since the owner has no workspace set); a downgrade never drops data.

Revision ID: 0052_depot_statement_trades
Revises: 0051_sign_in_keeps_data_version
Create Date: 2026-11-22
"""

from alembic import op

revision = "0052_depot_statement_trades"
down_revision = "0051_sign_in_keeps_data_version"
branch_labels = None
depends_on = None

# The list as 0041 left it, in its order.
OLD_MOVEMENT_SOURCES = (
    "csv",
    "trade_confirmation",
    "distribution_note",
    "report",
    "user",
    "securities_transfer",
    "chain",
)
NEW_MOVEMENT_SOURCE = "depot_statement"
LINE_CHECK = "asset_movement_depot_statement_check"
LINE_KEY = "asset_movement_depot_statement_line_key"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


UPGRADE = f"""
ALTER TABLE asset_movement ADD CONSTRAINT {LINE_CHECK} CHECK (
    source <> '{NEW_MOVEMENT_SOURCE}'
    OR (transaction_id IS NOT NULL AND document_id IS NOT NULL)
);
CREATE UNIQUE INDEX {LINE_KEY} ON asset_movement (workspace_id, transaction_id)
    WHERE source = '{NEW_MOVEMENT_SOURCE}' AND superseded_by IS NULL;
COMMENT ON INDEX {LINE_KEY} IS
    'D292: one live movement read from a depot cash statement line per line.';
"""

REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE asset_movement NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM asset_movement WHERE source = '{NEW_MOVEMENT_SOURCE}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a movement was read from a depot '
                        'cash statement line'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE asset_movement FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    _set_value_list("asset_movement", "source", (*OLD_MOVEMENT_SOURCES, NEW_MOVEMENT_SOURCE))
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(f"DROP INDEX {LINE_KEY};")
    op.execute(f"ALTER TABLE asset_movement DROP CONSTRAINT {LINE_CHECK};")
    _set_value_list("asset_movement", "source", OLD_MOVEMENT_SOURCES)
