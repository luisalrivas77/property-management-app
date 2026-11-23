"""Phase 10 D291: a coin's current price read on demand as its own price source `spot`.

Luis, 22 Nov 2026: „Could you also scope a way of updating the crypto values at any time
instead of once a day“. „Jetzt aktualisieren“ fetches a current price for each held coin
(Kraken's ticker, CoinGecko's simple price as the fallback) and stores it as `spot`, with the
moment it was read (docs/phases/phase-10.md §7.7, D291). A `spot` price is read for today
only; every past day keeps its daily close (rules/valuation.py).

The value joins the CHECK of `price.source` only (0025's inline column CHECK, named
`price_source_check`, as 0049 last rebuilt it). `valuation_snapshot.price_source` keeps 0049's
list without `spot` (`valuation_snapshot_price_source_check` is not touched), so the database
itself refuses a stored month-end priced by a price read on demand (D291: a month-end keeps
the daily close). The partial unique index `price_daily_key` is rebuilt over the same daily
sources as 0049 left it, so `spot` stays outside it and may be stored several times a day; the
daily fetch's ON CONFLICT DO NOTHING still infers the index.

The price table is append-only (0025's triggers refuse UPDATE and DELETE), so the downgrade
refuses while any `spot` row exists instead of dropping data, as 0049's does, and then restores
0049's CHECK and index exactly.

Revision ID: 0053_spot_price
Revises: 0052_depot_statement_trades
Create Date: 2026-11-23
"""

from alembic import op

revision = "0053_spot_price"
down_revision = "0052_depot_statement_trades"
branch_labels = None
depends_on = None

# 0049's lists (hausbuch.schemas.household.PriceSourceKind), in their order.
OLD_SOURCES = (
    "trade",
    "statement",
    "issuer_nav",
    "coingecko_daily",
    "kraken_daily",
    "ecb_fx",
    "user",
    "exchange_close",
)
DAILY = ("issuer_nav", "coingecko_daily", "kraken_daily", "ecb_fx", "exchange_close")
NEW_SOURCE = "spot"


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _set(sources: tuple[str, ...]) -> None:
    op.execute(f"""
ALTER TABLE price DROP CONSTRAINT price_source_check;
ALTER TABLE price ADD CONSTRAINT price_source_check
    CHECK (source IN ({_sql_list(sources)}));
DROP INDEX price_daily_key;
CREATE UNIQUE INDEX price_daily_key ON price (workspace_id, asset_id, "on", source)
    WHERE source IN ({_sql_list(DAILY)});
""")


REFUSE_IF_DATA = f"""DO $$
BEGIN
    ALTER TABLE price NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM price WHERE source = '{NEW_SOURCE}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a spot price exists'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE price FORCE ROW LEVEL SECURITY;
END
$$;"""


def upgrade() -> None:
    _set((*OLD_SOURCES, NEW_SOURCE))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set(OLD_SOURCES)
