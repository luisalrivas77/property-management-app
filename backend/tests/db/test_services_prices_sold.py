"""An asset held earlier and sold since (docs/phases/phase-6.md D174 amended 20 Nov 2026), in
the daily fetch as hausbuch_app under RLS: it is planned for the days it was held only, its
issuer NAV, Lang & Schwarz and coin rows end with its last held day and never go beyond, it
is not asked again once those days are priced, and it raises no card and no
`asset.price_source_failed` event, since no account holds it today. A still-held asset is
priced as before. Over the recorded files of test_services_prices_fetch and the synthetic
Lang & Schwarz answers of test_services_prices_exchange; invented ISINs, names and prices."""

# ruff: noqa: F811  (the imported fixtures are parameters)

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_prices_exchange import SEARCH, Venue, _history, _search
from db.test_services_prices_fetch import (  # noqa: F401  (funds is a fixture)
    ACTOR,
    EUR_FUND,
    NOW,
    USD_FUND,
    Fake,
    _asset,
    _events,
    _held,
    _prices,
    funds,
)

from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import EventType
from hausbuch.services.accounts import create_account
from hausbuch.services.prices import fetch as prices_fetch
from hausbuch.services.prices.fetch import Sources, run_workspace
from hausbuch.services.prices.overview import price_inbox_items

pytestmark = pytest.mark.db

BOUGHT = datetime(2026, 6, 24, 9, 30, tzinfo=UTC)
SOLD = datetime(2026, 6, 25, 14, 0, tzinfo=UTC)  # the recorded files reach the 26th


def _depot(run, ws, now=BOUGHT):
    return run(
        ws,
        create_account,
        AccountCreate(
            label="Depot",
            bank="Beispiel",
            kind=AccountKind.DEPOT,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2020, 1, 1),
        ),
        iban_key=TEST_IBAN_KEY,
        now=now,
    ).id


def _world(run, ws):
    """The EUR fund and an invented coin bought on the 24th and sold on the 25th; the USD
    fund bought on the 24th and still held."""
    depot = _depot(run, ws)

    def seed(ctx):
        sold_fund = _asset(ctx, kind="etf", isin=EUR_FUND.isin, name=EUR_FUND.name)
        held_fund = _asset(ctx, kind="etf", isin=USD_FUND.isin, name=USD_FUND.name)
        sold_coin = _asset(
            ctx, kind="crypto", symbol="INV", name="Invented Coin", kraken_pair="INVEUR"
        )
        for asset_id in (sold_fund, sold_coin):
            _held(ctx, depot, asset_id, "2", at=BOUGHT)
            _held(ctx, depot, asset_id, "-2", at=SOLD)
        _held(ctx, depot, held_fund, "2", at=BOUGHT)
        return {"sold_fund": sold_fund, "held_fund": held_fund, "sold_coin": sold_coin}

    return run(ws, seed, now=BOUGHT)


def _run(app_engine, ws, fetch, now=NOW):
    return run_workspace(app_engine, ws, ACTOR, Sources(fetch=fetch, coingecko=None), now)


def test_a_sold_asset_is_priced_for_the_days_it_was_held_and_never_beyond(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _world(run, ws)

    # The cards' „held today“ is unchanged; the plan takes the sold ones with their last day.
    held_today = {h.id for h in read(ws, prices_fetch.held_assets)}
    assert held_today == {ids["held_fund"]}
    planned = {h.id: h for h in read(ws, prices_fetch.planned_assets)}
    assert set(planned) == set(ids.values())
    assert planned[ids["held_fund"]].last_on is None
    assert planned[ids["sold_fund"]].last_on == planned[ids["sold_coin"]].last_on == SOLD.date()
    assert all(h.first_on == BOUGHT.date() for h in planned.values())
    # held today first, so a sold asset's source cannot end a source before them
    assert next(iter(planned.values())).id == ids["held_fund"]

    fake = Fake()
    _, out = _run(app_engine, ws, fake)
    rows = _prices(read, ws)
    days = {
        key: sorted(r.on for r in rows if r.asset_id == asset_id) for key, asset_id in ids.items()
    }
    # The files hold the 23rd to the 26th: nothing before the first movement, nothing after
    # the sale, and the still-held fund to the newest day as before.
    assert days["sold_fund"] == [date(2026, 6, 24), date(2026, 6, 25)]
    assert days["sold_coin"] == [date(2026, 6, 24), date(2026, 6, 25)]
    assert days["held_fund"] == [date(2026, 6, 25), date(2026, 6, 26)]
    assert {r.source for r in rows if r.asset_id == ids["sold_fund"]} == {"issuer_nav"}
    assert {r.source for r in rows if r.asset_id == ids["sold_coin"]} == {"kraken_daily"}
    assert out.failed == [] and out.assets == 3

    # No card for a fund no account holds today.
    items = read(ws, price_inbox_items, NOW)
    assert all(i.asset_id not in (ids["sold_fund"], ids["sold_coin"]) for i in items)

    # Its days are priced: the next runs never ask its sources again; the held fund's are.
    again = Fake()
    _, out = _run(app_engine, ws, again, NOW + timedelta(days=1))
    assert again.asked("navhist-emea-en-inv-gy") == 0 and again.asked("OHLC") == 0
    assert again.asked("ishares") == 1
    assert out.assets == 1 and out.failed == []
    assert {h.id for h in read(ws, prices_fetch.plan).held} == {ids["held_fund"]}


def test_a_sold_fund_whose_file_fails_raises_no_card_and_no_event(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _world(run, ws)
    down = Fake(down=("navhist-emea-en-inv-gy",))
    for day in range(8):
        _, out = _run(app_engine, ws, down, NOW + timedelta(days=day))
        # its held days are missing, so the run counts the failure honestly
        assert out.failed == [ids["sold_fund"]], day
    assert _events(read, ws, EventType.ASSET_PRICE_SOURCE_FAILED) == []
    items = read(ws, price_inbox_items, NOW + timedelta(days=7))
    assert all(i.asset_id != ids["sold_fund"] for i in items)
    assert all(r.asset_id != ids["sold_fund"] for r in _prices(read, ws))

    # The file is back: the held days are stored, none after the sale, and it is done.
    _, out = _run(app_engine, ws, Fake(), NOW + timedelta(days=8))
    assert out.failed == []
    stored = sorted(r.on for r in _prices(read, ws) if r.asset_id == ids["sold_fund"])
    assert stored == [date(2026, 6, 24), date(2026, 6, 25)]


def test_a_sold_fund_without_an_issuer_source_ends_its_exchange_prices_at_the_sale(
    run, read, app_engine, fresh_workspaces
):
    ws, _ = fresh_workspaces
    isin, name = "IE00BEISPL01", "NORDSTERN INVENT AKT. A"  # names no adapter's brand
    first = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
    sold = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    now = datetime(2026, 11, 19, 6, 30, tzinfo=UTC)
    depot = _depot(run, ws, first)

    def seed(ctx):
        asset_id = _asset(ctx, kind="etf", isin=isin, name=name)
        _held(ctx, depot, asset_id, "3", at=first)
        _held(ctx, depot, asset_id, "-3", at=sold)
        return asset_id

    asset_id = run(ws, seed, now=first)
    weekdays = [date(2026, 11, d) for d in range(2, 19) if date(2026, 11, d).weekday() < 5]
    venue = Venue({SEARCH: _search(isin, 4711), "dataForInstrument": _history(isin, weekdays)})
    _, out = _run(app_engine, ws, venue, now)
    stored = sorted(r.on for r in _prices(read, ws) if r.asset_id == asset_id)
    assert stored == [d for d in weekdays if d <= sold.date()]
    assert {r.source for r in _prices(read, ws) if r.asset_id == asset_id} == {"exchange_close"}
    assert out.failed == []

    # Done: the venue is not asked again for it.
    again = Venue({SEARCH: _search(isin, 4711), "dataForInstrument": _history(isin, weekdays)})
    _, out = _run(app_engine, ws, again, now + timedelta(days=1))
    assert again.urls == [] and out.sources == set()
