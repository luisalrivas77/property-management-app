"""Phase 10 D291 (§7.7) on the database: a coin's price read on demand (`spot`) values the
live reads of today only. The lots, the 31 Dec statement, the month-end and every read that
passes no `today` keep the daily closes. Invented coin, account and amounts only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.test_services_holdings import _account, _asset, _movement, _price
from sqlalchemy import insert, select

from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import valuation_snapshot as snapshot_table
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import PriceSourceKind
from hausbuch.services import holdings, net_worth
from hausbuch.services.prices import overview

pytestmark = pytest.mark.db

DAY = date(2026, 10, 31)  # the day the spot rows were read (a month-end, on purpose)
BEFORE = date(2026, 10, 30)
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
MORNING = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
NOON = datetime(2026, 10, 31, 11, 0, tzinfo=UTC)


def _spot(ctx, asset_id, price, at):
    ctx.conn.execute(
        insert(price_table).values(
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            on=DAY,
            price_eur=Decimal(price),
            source="spot",
            fetched_at=at,
            raw={"pair": "INVTEUR", "last": price, "read_at": at.isoformat()},
        )
    )


def _setup(ctx):
    wallet = _account(ctx, AccountKind.WALLET, "Wallet A")
    coin = _asset(ctx, "INVT", "Invented Coin")
    # 2 units bought the day before with their euros, 1 unit received on the spot day without.
    _movement(ctx, wallet, coin, "2", datetime(2026, 10, 30, 10, tzinfo=UTC), eur_amount="-200")
    _movement(ctx, wallet, coin, "1", datetime(2026, 10, 31, 10, tzinfo=UTC))
    _price(ctx, coin, BEFORE, "100.00", "kraken_daily")
    _spot(ctx, coin, "120.00", MORNING)
    _spot(ctx, coin, "125.00", NOON)
    return wallet, coin


def test_the_live_crypto_read_takes_the_newest_spot_and_names_its_moment(
    run, read, fresh_workspaces
):
    ws, other = fresh_workspaces
    _wallet, coin = run(ws, _setup, now=NOW)

    live = read(ws, holdings.crypto_holdings, DAY, today=DAY)
    [c] = live.coins
    assert c.price_eur == Decimal("125.00000000")
    assert c.price_source is PriceSourceKind.SPOT
    assert c.price_read_at == NOON
    assert c.value_eur == Decimal("375.00")
    assert live.spot_read_at == NOON

    # The lot of the spot day has no price of its day: a spot row never prices a lot.
    assert c.unpriced_lots == 1
    assert c.cost_eur is None

    # Without `today` (any other caller) and on a past day: the close before, never spot.
    plain = read(ws, holdings.crypto_holdings, DAY)
    assert plain.coins[0].price_eur == Decimal("100.00000000")
    assert plain.coins[0].price_source is PriceSourceKind.KRAKEN_DAILY
    assert plain.coins[0].price_read_at is None
    assert plain.spot_read_at is None
    later = read(ws, holdings.crypto_holdings, date(2026, 11, 1), today=date(2026, 11, 1))
    assert later.coins[0].price_eur == Decimal("100.00000000")
    assert later.spot_read_at is None

    assert read(other, holdings.crypto_holdings, DAY, today=DAY).spot_read_at is None
    del coin


def test_the_statement_and_the_assets_overview_without_today_ignore_spot(
    run, read, fresh_workspaces
):
    ws, _ = fresh_workspaces
    _wallet, coin = run(ws, _setup, now=NOW)
    [acc] = read(ws, holdings.statement, DAY).accounts
    [position] = acc.positions
    assert position.value_eur == Decimal("300.00")

    plain = read(ws, overview.assets_overview, DAY)
    live = read(ws, overview.assets_overview, DAY, today=DAY)
    assert plain.total_eur == Decimal("300.00")
    assert live.total_eur == Decimal("375.00")
    del coin


def test_the_net_worth_reads_spot_only_for_its_live_figure(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    run(ws, _setup, now=NOW)
    plain = read(ws, net_worth.net_worth, DAY)
    live = read(ws, net_worth.net_worth, DAY, today=DAY)
    assert live.total - plain.total == Decimal("75.00")


def test_a_month_end_stored_after_a_spot_read_keeps_the_daily_close(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    _wallet, coin = run(ws, _setup, now=NOW)
    result = run(ws, net_worth.capture_month_end, DAY, now=NOW)
    assert result.run is not None

    def stored(conn):
        s = snapshot_table.c
        return conn.execute(
            select(s.price_eur, s.price_source, s.value_eur).where(s.asset_id == coin)
        ).one()

    row = read(ws, stored)
    assert row.price_source == "kraken_daily"
    assert row.price_eur == Decimal("100.00000000")
    assert row.value_eur == Decimal("300.00")
