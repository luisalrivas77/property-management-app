"""An asset held earlier and sold since (docs/phases/phase-6.md D174 amended 20 Nov 2026), in
the daily fetch as hausbuch_app under RLS: it is planned for the days it was held only, its
issuer NAV, Lang & Schwarz and coin rows end with its last held day and never go beyond, and
it raises no card and no `asset.price_source_failed` event, since no account holds it today.
„When a sold asset is done“: it leaves the plan only after a run read its sources and stored
nothing new for its held stretch (settled), or after MAX_SOLD_RUNS runs, both counted from
the runs' events, never from rows stored while it was held or from the sale's date; a
changed stretch is asked afresh, and a fund bought again counts its failures from its
current held stretch. A still-held asset is priced as before. Over the recorded files of
test_services_prices_fetch and the synthetic Lang & Schwarz answers of
test_services_prices_exchange; invented ISINs, names and prices."""

# ruff: noqa: F811  (the imported fixtures are parameters)

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_phase7_chain_assets import _priced_as_world
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
from sqlalchemy import select

from hausbuch.db.tables import account as account_table
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import EventType
from hausbuch.services.accounts import create_account
from hausbuch.services.prices import fetch as prices_fetch
from hausbuch.services.prices.fetch import Sources, failure_runs, run_workspace
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

    # The run stored new rows, so it settled nothing; the coin has every held day priced
    # (coins trade daily), so nothing is left to ask for it.
    assert set(_stretches(read, ws)[-1]) == {
        (ids["sold_fund"], "2026-06-24", "2026-06-25", False),
        (ids["sold_coin"], "2026-06-24", "2026-06-25", False),
    }
    # The next run reads the fund's file again, stores nothing new: settled.
    again = Fake()
    _, out = _run(app_engine, ws, again, NOW + timedelta(days=1))
    assert again.asked("navhist-emea-en-inv-gy") == 1 and again.asked("OHLC") == 0
    assert out.assets == 2 and out.failed == []
    assert _stretches(read, ws)[-1] == [(ids["sold_fund"], "2026-06-24", "2026-06-25", True)]
    # From then on its sources are never asked again; the held fund's are.
    third = Fake()
    _, out = _run(app_engine, ws, third, NOW + timedelta(days=2))
    assert third.asked("navhist-emea-en-inv-gy") == 0 and third.asked("ishares") == 1
    assert out.assets == 1 and out.failed == []
    assert {h.id for h in read(ws, prices_fetch.plan).held} == {ids["held_fund"]}
    assert _stretches(read, ws)[-1] == []


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

    # The next read stores nothing new and settles it; then the venue is not asked again.
    again = Venue({SEARCH: _search(isin, 4711), "dataForInstrument": _history(isin, weekdays)})
    _, out = _run(app_engine, ws, again, now + timedelta(days=1))
    assert any("dataForInstrument" in u for u in again.urls) and out.failed == []
    assert _stretches(read, ws)[-1] == [(asset_id, "2026-11-02", "2026-11-10", True)]
    third = Venue({SEARCH: _search(isin, 4711), "dataForInstrument": _history(isin, weekdays)})
    _, out = _run(app_engine, ws, third, now + timedelta(days=2))
    assert third.urls == [] and out.sources == set()


def test_a_movement_of_no_units_after_the_sale_does_not_extend_the_days(
    run, read, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _world(run, ws)
    depot = read(ws, lambda conn: conn.execute(select(account_table.c.id)).scalar_one())

    def note(ctx):  # a distribution note of 0 units a month after the sale
        _held(ctx, depot, ids["sold_fund"], "0", at=SOLD + timedelta(days=30))

    run(ws, note, now=NOW)
    planned = {h.id: h for h in read(ws, prices_fetch.planned_assets)}
    assert planned[ids["sold_fund"]].last_on == SOLD.date()


def test_a_coin_sold_beyond_its_sources_reach_is_not_asked(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    depot = _depot(run, ws)

    def seed(ctx):
        coin = _asset(
            ctx, kind="crypto", symbol="OLDINV", name="Old Invented Coin", kraken_pair="OLDEUR"
        )
        _held(ctx, depot, coin, "5", at=datetime(2023, 1, 10, 9, 0, tzinfo=UTC))
        _held(ctx, depot, coin, "-5", at=datetime(2023, 6, 1, 9, 0, tzinfo=UTC))
        return coin

    coin = run(ws, seed, now=BOUGHT)
    # Kraken's 720 days from 27 Jun 2026 reach back to July 2024: nothing to ask for.
    assert read(ws, prices_fetch.plan, NOW.date()).held == []
    # Without a day to count back from, every held day is still owed.
    assert [h.id for h in read(ws, prices_fetch.plan).held] == [coin]


def test_sold_assets_no_source_prices_ask_nothing_and_write_no_event(
    run, read, app_engine, fresh_workspaces
):
    ws, _ = fresh_workspaces
    depot = _depot(run, ws)

    def seed(ctx):
        stock = _asset(ctx, kind="stock", isin="DE000BEISPL1", name="Beispiel AG")
        coin = _asset(ctx, kind="crypto", symbol="NOSRC", name="Invented Coin Without Source")
        for asset_id in (stock, coin):  # an ETF always carries an ISIN (asset_check)
            _held(ctx, depot, asset_id, "1", at=BOUGHT)
            _held(ctx, depot, asset_id, "-1", at=SOLD)

    run(ws, seed, now=BOUGHT)
    assert len(read(ws, prices_fetch.planned_assets)) == 2
    fake = Fake()
    new, out = _run(app_engine, ws, fake)
    assert (new, out.sources, fake.calls) == (0, set(), [])
    assert _events(read, ws, EventType.WORKSPACE_PRICES_FETCHED) == []


def test_a_sold_coin_that_chain_assets_are_priced_as_stays_priced_to_today(
    run, read, fresh_workspaces
):
    ws, _ = fresh_workspaces
    ids = run(ws, _priced_as_world, now=BOUGHT)

    def sold(ctx):  # the chainless INV itself bought and sold on the depot
        _held(ctx, ids["account"], ids["twin"], "1", at=BOUGHT)
        _held(ctx, ids["account"], ids["twin"], "-1", at=SOLD)

    run(ws, sold, now=BOUGHT)
    planned = {h.id: h for h in read(ws, prices_fetch.planned_assets)}
    assert planned[ids["twin"]].last_on is None  # its chain assets' lots need today's price


def _stretches(read, ws) -> list[list[tuple]]:
    """Per prices_fetched run, oldest first: (asset, first_on, last_on, settled)."""
    return [
        [
            (UUID(n["asset_id"]), n["first_on"], n["last_on"], n["settled"])
            for n in e.payload.get("sold_stretches") or []
        ]
        for e in _events(read, ws, EventType.WORKSPACE_PRICES_FETCHED)
    ]


def _venue_fund(run, ws, first, moves, isin="IE00BEISPL01", name="NORDSTERN INVENT AKT. A"):
    """A fund no issuer adapter prices (its name wants Lang & Schwarz, D264), with
    (units, at) movements on one depot."""
    depot = _depot(run, ws, first)

    def seed(ctx):
        asset_id = _asset(ctx, kind="etf", isin=isin, name=name)
        for units, at in moves:
            _held(ctx, depot, asset_id, units, at=at)
        return asset_id

    return run(ws, seed, now=first), depot


def _weekdays(start: date, end: date) -> list[date]:
    return [
        start + timedelta(days=n)
        for n in range((end - start).days + 1)
        if (start + timedelta(days=n)).weekday() < 5
    ]


def _lands(isin: str, days: list[date]) -> Venue:
    return Venue({SEARCH: _search(isin, 4711), "dataForInstrument": _history(isin, days)})


def test_rows_stored_while_held_never_end_the_asking(run, read, app_engine, fresh_workspaces):
    """F1 of the review: rows on every weekday until 26 Nov were stored while the fund was
    held, its last held day is 2 Dec. The days 27 Nov to 2 Dec are fetched, and the fund
    leaves the plan only after a later run that stored nothing new."""
    isin = "IE00BEISPL01"
    first = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
    ws, _ = fresh_workspaces
    asset_id, depot = _venue_fund(run, ws, first, [("3", first)])
    held_days = _weekdays(date(2026, 11, 2), date(2026, 11, 26))
    _run(app_engine, ws, _lands(isin, held_days), datetime(2026, 11, 27, 6, 30, tzinfo=UTC))
    assert sorted(r.on for r in _prices(read, ws)) == held_days

    sold = datetime(2026, 12, 2, 15, 0, tzinfo=UTC)
    run(ws, lambda ctx: _held(ctx, depot, asset_id, "-3", at=sold), now=sold)
    later = _weekdays(date(2026, 11, 2), date(2026, 12, 3))
    _run(app_engine, ws, _lands(isin, later), datetime(2026, 12, 3, 6, 30, tzinfo=UTC))
    stored = sorted(r.on for r in _prices(read, ws))
    assert stored == [d for d in later if d <= sold.date()]  # 27 Nov to 2 Dec, never 3 Dec
    assert _stretches(read, ws)[-1] == [(asset_id, "2026-11-02", "2026-12-02", False)]
    assert [h.id for h in read(ws, prices_fetch.plan, date(2026, 12, 4)).held] == [asset_id]

    _run(app_engine, ws, _lands(isin, later), datetime(2026, 12, 4, 6, 30, tzinfo=UTC))
    assert _stretches(read, ws)[-1] == [(asset_id, "2026-11-02", "2026-12-02", True)]
    done = _lands(isin, later)
    _run(app_engine, ws, done, datetime(2026, 12, 5, 6, 30, tzinfo=UTC))
    assert done.urls == []


def test_a_fund_sold_months_ago_is_fetched_on_the_first_run(
    run, read, app_engine, fresh_workspaces
):
    isin = "IE00BEISPL01"
    bought = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
    sold = datetime(2026, 4, 30, 15, 0, tzinfo=UTC)
    ws, _ = fresh_workspaces
    asset_id, _ = _venue_fund(run, ws, bought, [("3", bought), ("-3", sold)])
    days = _weekdays(date(2026, 3, 2), date(2026, 11, 18))
    _, out = _run(app_engine, ws, _lands(isin, days), datetime(2026, 11, 19, 6, 30, tzinfo=UTC))
    stored = sorted(r.on for r in _prices(read, ws) if r.asset_id == asset_id)
    assert stored == _weekdays(bought.date(), sold.date())
    assert out.failed == []


def test_a_stretch_is_asked_at_most_ten_runs_and_afresh_when_it_changes(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _world(run, ws)
    down = Fake(down=("navhist-emea-en-inv-gy",))
    for day in range(prices_fetch.MAX_SOLD_RUNS):
        _, out = _run(app_engine, ws, down, NOW + timedelta(days=day))
        assert out.failed == [ids["sold_fund"]], day
    after = Fake(down=("navhist-emea-en-inv-gy",))
    _run(app_engine, ws, after, NOW + timedelta(days=10))
    assert after.asked("navhist-emea-en-inv-gy") == 0
    assert ids["sold_fund"] not in {h.id for h in read(ws, prices_fetch.plan).held}
    assert _events(read, ws, EventType.ASSET_PRICE_SOURCE_FAILED) == []

    # An older statement moves its first day: another stretch, asked afresh.
    depot = read(ws, lambda conn: conn.execute(select(account_table.c.id)).scalar_one())
    older = datetime(2026, 6, 23, 9, 30, tzinfo=UTC)

    def statement(ctx):
        _held(ctx, depot, ids["sold_fund"], "1", at=older)
        _held(ctx, depot, ids["sold_fund"], "-1", at=older + timedelta(hours=1))

    run(ws, statement, now=NOW)
    planned = {h.id: h for h in read(ws, prices_fetch.plan).held}
    assert (planned[ids["sold_fund"]].first_on, planned[ids["sold_fund"]].last_on) == (
        older.date(),
        SOLD.date(),
    )
    up = Fake()
    _run(app_engine, ws, up, NOW + timedelta(days=11))
    assert up.asked("navhist-emea-en-inv-gy") == 1
    assert _stretches(read, ws)[-1] == [(ids["sold_fund"], "2026-06-23", "2026-06-25", False)]


def test_the_row_limit_leaves_the_older_days_to_the_next_read(
    run, read, app_engine, fresh_workspaces, funds, monkeypatch
):
    """F3 of the review: a held stretch longer than the row limit. The newest rows are
    stored first; the next read of the same file takes the older days still missing, and
    only a read that adds nothing settles it."""
    monkeypatch.setattr(prices_fetch, "MAX_ROWS", 2)
    ws, _ = fresh_workspaces
    bought = datetime(2026, 6, 23, 9, 30, tzinfo=UTC)
    sold = datetime(2026, 6, 26, 14, 0, tzinfo=UTC)
    depot = _depot(run, ws, bought)

    def seed(ctx):
        asset_id = _asset(ctx, kind="etf", isin=EUR_FUND.isin, name=EUR_FUND.name)
        _held(ctx, depot, asset_id, "2", at=bought)
        _held(ctx, depot, asset_id, "-2", at=sold)
        return asset_id

    asset_id = run(ws, seed, now=bought)

    def days():
        return sorted(r.on for r in _prices(read, ws) if r.asset_id == asset_id)

    _run(app_engine, ws, Fake())
    assert days() == [date(2026, 6, 25), date(2026, 6, 26)]  # the newest two
    _run(app_engine, ws, Fake(), NOW + timedelta(days=1))
    assert days() == [date(2026, 6, 23), date(2026, 6, 24), date(2026, 6, 25), date(2026, 6, 26)]
    unsettled = [(asset_id, "2026-06-23", "2026-06-26", False)]
    assert _stretches(read, ws) == [unsettled, unsettled]  # both runs stored new rows
    _run(app_engine, ws, Fake(), NOW + timedelta(days=2))
    assert _stretches(read, ws)[-1] == [(asset_id, "2026-06-23", "2026-06-26", True)]
    last = Fake()
    _run(app_engine, ws, last, NOW + timedelta(days=3))
    assert last.asked("navhist-emea-en-inv-gy") == 0


def test_a_fund_bought_again_and_sold_again_is_a_new_stretch(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _world(run, ws)
    _run(app_engine, ws, Fake())
    _run(app_engine, ws, Fake(), NOW + timedelta(days=1))  # settles 24–25 Jun
    assert ids["sold_fund"] not in {h.id for h in read(ws, prices_fetch.plan).held}
    depot = read(ws, lambda conn: conn.execute(select(account_table.c.id)).scalar_one())

    rebuy = datetime(2026, 6, 26, 10, 0, tzinfo=UTC)
    run(ws, lambda ctx: _held(ctx, depot, ids["sold_fund"], "2", at=rebuy), now=NOW)
    held = {h.id: h for h in read(ws, prices_fetch.plan).held}
    assert held[ids["sold_fund"]].last_on is None  # held again: priced to today
    _run(app_engine, ws, Fake(), NOW + timedelta(days=2))
    sold_fund_days = sorted(r.on for r in _prices(read, ws) if r.asset_id == ids["sold_fund"])
    assert sold_fund_days == [date(2026, 6, 24), date(2026, 6, 25), date(2026, 6, 26)]

    resold = datetime(2026, 6, 26, 15, 0, tzinfo=UTC)
    run(ws, lambda ctx: _held(ctx, depot, ids["sold_fund"], "-2", at=resold), now=NOW)
    fake = Fake()
    _run(app_engine, ws, fake, NOW + timedelta(days=3))  # the new stretch, asked afresh
    assert fake.asked("navhist-emea-en-inv-gy") == 1
    assert _stretches(read, ws)[-1] == [(ids["sold_fund"], "2026-06-24", "2026-06-26", True)]


def test_units_moved_between_own_depots_stay_held(run, read, app_engine, fresh_workspaces, funds):
    ws, _ = fresh_workspaces
    first, second = _depot(run, ws), _depot(run, ws)

    def seed(ctx):
        asset_id = _asset(ctx, kind="etf", isin=EUR_FUND.isin, name=EUR_FUND.name)
        _held(ctx, first, asset_id, "2", at=BOUGHT)
        moved = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)  # one booking, both legs
        _held(ctx, first, asset_id, "-2", at=moved)
        _held(ctx, second, asset_id, "2", at=moved)
        return asset_id

    asset_id = run(ws, seed, now=BOUGHT)
    [planned] = read(ws, prices_fetch.planned_assets)
    assert (planned.id, planned.last_on) == (asset_id, None)
    _run(app_engine, ws, Fake())
    stored = sorted(r.on for r in _prices(read, ws) if r.asset_id == asset_id)
    assert stored == [date(2026, 6, 24), date(2026, 6, 25), date(2026, 6, 26)]
    assert _stretches(read, ws)[-1] == []


def test_failures_while_sold_never_count_toward_the_card_after_a_rebuy(
    run, read, app_engine, fresh_workspaces, funds
):
    """F4 of the review: a sold fund's file fails for 8 runs; it is bought again, and its
    first failing held run raises no event and no card. Its streak then counts from the
    rebuy and raises the event on its seventh held day, as for any held fund."""
    ws, _ = fresh_workspaces
    ids = _world(run, ws)
    down = Fake(down=("navhist-emea-en-inv-gy",))
    for day in range(8):
        _run(app_engine, ws, down, NOW + timedelta(days=day))
    depot = read(ws, lambda conn: conn.execute(select(account_table.c.id)).scalar_one())
    rebuy = NOW + timedelta(days=7, hours=4)
    run(ws, lambda ctx: _held(ctx, depot, ids["sold_fund"], "2", at=rebuy), now=rebuy)

    since = NOW - timedelta(days=31)
    for day in range(8, 15):
        now = NOW + timedelta(days=day)
        _run(app_engine, ws, down, now)
        streak = read(ws, failure_runs, ids["sold_fund"], since)
        assert len(streak) == day - 7 and all(failed for _, failed in streak), day
        raised = _events(read, ws, EventType.ASSET_PRICE_SOURCE_FAILED)
        cards = [
            i
            for i in read(ws, price_inbox_items, now)
            if i.asset_id == ids["sold_fund"] and i.kind is InboxItemKind.PRICE_SOURCE_FAILED
        ]
        if day < 14:
            assert raised == [] and cards == [], day
    [event] = raised
    assert event.entity_id == ids["sold_fund"] and event.payload["failed_days"] == 7
    assert len(cards) == 1
