"""Phase 7: coin units to 18 decimal places (docs/phases/phase-7.md §15.1 item 9 and the step 0
review's item 3, §16 step 2's migration slot).

A chain keeps its amounts in integer base units, and an 18-decimal asset (ETH and every
ERC-20 on the Ethereum family, BSC's stablecoins) has amounts below 0.0000000001 of a unit:
an EVM fee of 21,000,000,147,000 wei is 0.000021000000147 ETH (`evm_tx.yaml`'s
`fee_below_numeric_scale`). Since D185 reconciles a wallet to the unit, nothing may round, so
every column 0025 gave a coin's units, numeric(28,10), becomes numeric(38,18): 20 integer
digits instead of 18, so no value stored today loses a digit or stops fitting.

- `asset_movement.quantity` and `asset_movement.fee_quantity` (a movement's units and the
  units of a fee paid in the asset);
- `holding_statement.declared_units` and `holding_statement.difference_units` (what a report,
  an export's typed balance or a chain's balance says an account held, and the gap);
- `valuation_snapshot.units` (the units a month-end valued);
- `asset.decimals` may be 0..18 (0025: 0..10), the CHECK replaced under its own name.

Not changed: `price.price_eur` and `valuation_snapshot.price_eur` (numeric(20,8), a price in
EUR per unit, not a count of units) and every EUR figure (numeric(14,2)).

ALTER COLUMN TYPE rewrites the four tables but fires no row trigger, so the append-only
tables (holding_statement, valuation_snapshot) keep their rows, triggers, grants and
row-level security unchanged; numeric(28,10) to numeric(38,18) is exact for every value.

The downgrade is refused while a stored value needs more than 10 places or 18 integer digits,
or an asset has more than 10 decimals: numeric(28,10) would round or refuse it, and a
downgrade never drops data. It lifts FORCE RLS for the checks, since the owner has no
workspace set, and restores it (0040's guard).

Revision ID: 0042_quantity_scale
Revises: 0041_wallets
Create Date: 2026-11-15
"""

from alembic import op

revision = "0042_quantity_scale"
down_revision = "0041_wallets"
branch_labels = None
depends_on = None

OLD_TYPE = "numeric(28,10)"
NEW_TYPE = "numeric(38,18)"
# (table, column): every coin-unit column of 0025.
QUANTITY_COLUMNS = (
    ("asset_movement", "quantity"),
    ("asset_movement", "fee_quantity"),
    ("holding_statement", "declared_units"),
    ("holding_statement", "difference_units"),
    ("valuation_snapshot", "units"),
)
DECIMALS_CHECK = "asset_decimals_check"  # 0025's inline CHECK, named by Postgres
OLD_MAX_DECIMALS = 10
NEW_MAX_DECIMALS = 18


def _alter(type_: str) -> str:
    return "\n".join(
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {type_};"
        for table, column in QUANTITY_COLUMNS
    )


def _decimals_check(maximum: int) -> str:
    return f"""
ALTER TABLE asset DROP CONSTRAINT {DECIMALS_CHECK};
ALTER TABLE asset ADD CONSTRAINT {DECIMALS_CHECK} CHECK (decimals BETWEEN 0 AND {maximum});"""


def _lossy(column: str) -> str:
    """A value numeric(28,10) cannot hold exactly: more than 10 places, or 18 integer digits
    or more (numeric(28,10) keeps 18 integer digits: |x| < 1e18)."""
    return f"({column} <> round({column}, 10) OR abs({column}) >= 1e18)"


_DATA_CHECKS: tuple[tuple[str, str, str], ...] = tuple(
    (
        table,
        f"SELECT 1 FROM {table} WHERE {_lossy(column)}",
        f"{table}.{column} holds a value numeric(28,10) cannot",
    )
    for table, column in QUANTITY_COLUMNS
) + (
    (
        "asset",
        f"SELECT 1 FROM asset WHERE decimals > {OLD_MAX_DECIMALS}",
        f"an asset has more than {OLD_MAX_DECIMALS} decimals",
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
    op.execute(_alter(NEW_TYPE))
    op.execute(_decimals_check(NEW_MAX_DECIMALS))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(_decimals_check(OLD_MAX_DECIMALS))
    op.execute(_alter(OLD_TYPE))
