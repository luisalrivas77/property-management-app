"""Phase 6 D264: an exchange's daily price (Lang & Schwarz) as its own price source.

Luis, 19 Nov 2026: „Let's go with lang and Schwarz … it's only for Personal use.“ A held fund
whose issuer publishes no usable daily NAV is priced by the exchange's daily price in euros,
stored as `exchange_close`, never as a NAV (docs/phases/phase-6.md D264).

The value joins the CHECKs of `price.source` and `valuation_snapshot.price_source` (0025's
inline column CHECKs, which Postgres named `price_source_check` and
`valuation_snapshot_price_source_check`; checked on a database migrated from 0001), and the
partial unique index `price_daily_key`, so an exchange prices an asset once a day like the
other daily feeds and the fetch's ON CONFLICT DO NOTHING infers the index.

Both tables are append-only (0025's triggers refuse UPDATE and DELETE), so the downgrade
refuses while any `exchange_close` row exists instead of dropping data, as 0040's does, and
then restores 0025's CHECKs and index exactly.

Phase 8's spec had reserved the next free number; it takes the one after.

Revision ID: 0049_exchange_close_price
Revises: 0048_household_plan
Create Date: 2026-11-19
"""

from alembic import op

revision = "0049_exchange_close_price"
down_revision = "0048_household_plan"
branch_labels = None
depends_on = None

# 0025's lists (hausbuch.schemas.household.PriceSourceKind), in their order.
OLD_SOURCES = (
    "trade",
    "statement",
    "issuer_nav",
    "coingecko_daily",
    "kraken_daily",
    "ecb_fx",
    "user",
)
OLD_DAILY = ("issuer_nav", "coingecko_daily", "kraken_daily", "ecb_fx")
NEW_SOURCE = "exchange_close"


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _set(sources: tuple[str, ...], daily: tuple[str, ...]) -> None:
    op.execute(f"""
ALTER TABLE price DROP CONSTRAINT price_source_check;
ALTER TABLE price ADD CONSTRAINT price_source_check
    CHECK (source IN ({_sql_list(sources)}));
ALTER TABLE valuation_snapshot DROP CONSTRAINT valuation_snapshot_price_source_check;
ALTER TABLE valuation_snapshot ADD CONSTRAINT valuation_snapshot_price_source_check
    CHECK (price_source IN ({_sql_list(sources)}));
DROP INDEX price_daily_key;
CREATE UNIQUE INDEX price_daily_key ON price (workspace_id, asset_id, "on", source)
    WHERE source IN ({_sql_list(daily)});
""")


REFUSE_IF_DATA = f"""DO $$
BEGIN
    ALTER TABLE price NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE valuation_snapshot NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM price WHERE source = '{NEW_SOURCE}')
       OR EXISTS (SELECT 1 FROM valuation_snapshot WHERE price_source = '{NEW_SOURCE}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: an exchange_close price exists'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE price FORCE ROW LEVEL SECURITY;
    ALTER TABLE valuation_snapshot FORCE ROW LEVEL SECURITY;
END
$$;"""


def upgrade() -> None:
    _set((*OLD_SOURCES, NEW_SOURCE), (*OLD_DAILY, NEW_SOURCE))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set(OLD_SOURCES, OLD_DAILY)
