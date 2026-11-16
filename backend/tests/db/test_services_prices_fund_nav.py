"""Which funds the daily fetch prices from their issuer (docs/phases/phase-6.md D174, amended
16 Nov 2026), as hausbuch_app under RLS: every registry fund a depot holds, a fund moved
between own depots, and a fund the registry does not list found in its issuer's catalogue by
its ISIN, its reference kept in the workspace's price rows. The registry's funds are taken
from the registry itself, so no test names a fund a household holds; every other ISIN is
invented, and the recorded files are the synthetic samples of fixtures/phase6/samples/."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import insert, select

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import price as price_table
from hausbuch.rules.household.assets import registry
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor
from hausbuch.services.depot import asset_for_isin
from hausbuch.services.prices import fetch as prices_fetch
from hausbuch.services.prices import overview as prices_overview
from hausbuch.services.prices.fetch import Sources, run_workspace
from hausbuch.services.prices.issuer_nav import ADAPTERS

pytestmark = pytest.mark.db

ACTOR = Actor(ActorType.SYSTEM, "fetch_prices")
FIRST = datetime(2026, 6, 24, 9, 30, tzinfo=UTC)
TODAY = date(2026, 6, 27)
NOW = datetime(2026, 6, 27, 7, 0, tzinfo=UTC)
SAMPLES = __import__("fixture_models_phase6").SAMPLES
ISHARES_ISIN, SPDR_ISIN, OTHER_ISIN = "IE000INVENT2", "IE000INVENT1", "IE000INVENT9"


class Recorded:
    """A Fetch answering the synthetic samples by URL part (the catalogues first: their
    addresses also contain the issuer's name); `down` parts answer 503."""

    FILES = (
        ("eurofxref-hist-90d", "ecb_hist_90d.xml"),
        ("eurofxref-daily", "ecb_daily.xml"),
        ("product-screener/product-data", "ishares_catalogue.json"),
        ("ssmp/fund/fundfinder", "spdr_fundfinder.json"),
        ("portfolioId=1&", "ishares_fund_download.xls"),
        ("navhist-emea-en-inv-gy", "spdr_navhist.xlsx"),
    )

    def __init__(self, down: tuple[str, ...] = ()) -> None:
        self.down = down
        self.urls: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.urls.append(url)
        if any(part in url for part in self.down):
            return 503, b""
        for part, name in self.FILES:
            if part in url:
                return 200, (SAMPLES / name).read_bytes()
        return 404, b""

    def asked(self, part: str) -> int:
        return sum(part in url for url in self.urls)


class Down:
    """A Fetch that records every URL and answers 503: what was asked is the point here."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.urls.append(url)
        return 503, b""


def _depot(run, ws, label):
    return run(
        ws,
        create_account,
        AccountCreate(
            label=label,
            bank="Beispiel",
            kind=AccountKind.DEPOT,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 6, 1),
        ),
        iban_key=TEST_IBAN_KEY,
        now=FIRST,
    ).id


def _move(ctx, account_id, asset_id, kind, units):
    ctx.conn.execute(
        insert(movement_table).values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            asset_id=asset_id,
            kind=kind,
            quantity=Decimal(units),
            occurred_at=FIRST,
            source="securities_transfer" if "transfer" in kind else "user",
            status="confirmed",
            created_at=ctx.now,
        )
    )


def test_every_registry_fund_a_depot_holds_is_asked_from_its_issuer(run, read, fresh_workspaces):
    """The live data of 15 Nov 2026 had no issuer NAV at all: each registry fund, created as
    the depot's documents create it (services.depot.asset_for_isin) and held by an
    Einbuchung, is planned with its registry entry and its issuer's file is asked for."""
    ws, _ = fresh_workspaces
    depot = _depot(run, ws, "Depot")
    funds = registry().funds

    def seed(ctx):
        ids = {}
        for fund in funds:
            asset_id = asset_for_isin(ctx, fund.isin, fund.name)
            _move(ctx, depot, asset_id, "internal_transfer_in", "3.5")
            ids[asset_id] = fund
        return ids

    ids = run(ws, seed, now=FIRST)
    planned = read(ws, prices_fetch.plan)
    held = {h.id: h for h in planned.held}
    assert set(held) == set(ids)
    for asset_id, fund in ids.items():
        assert held[asset_id].fund == fund, fund.issuer
        assert held[asset_id].first_on == FIRST.date()

    down = Down()
    out = prices_fetch.collect(planned, Sources(fetch=down, coingecko=None), TODAY, {})
    for fund in funds:
        url = ADAPTERS[fund.issuer](down).url(fund.fund_ref or "")
        assert down.urls.count(url) == 1, fund.issuer
    assert set(out.failed) == set(ids)  # every file answered 503 here


def test_units_moved_between_own_depots_stay_held(run, read, fresh_workspaces):
    """Units moved from one own depot to another whose purchases came before the app: the
    first depot shows −n, the second +n. The Übersicht shows the +n position; the fetch and
    the price cards treat the fund as held although the units sum to zero over both."""
    ws, _ = fresh_workspaces
    old, new = _depot(run, ws, "Altes Depot"), _depot(run, ws, "Neues Depot")

    def seed(ctx):
        moved = asset_for_isin(ctx, "IE000INVENT7", "Invented Moved Fund")
        _move(ctx, old, moved, "internal_transfer_out", "-4")
        _move(ctx, new, moved, "internal_transfer_in", "4")
        sold = asset_for_isin(ctx, "IE000INVENT8", "Invented Sold Fund")
        _move(ctx, new, sold, "acquisition", "2")
        _move(ctx, new, sold, "disposal", "-2")
        return moved, sold

    moved, sold = run(ws, seed, now=FIRST)
    held = {h.id for h in read(ws, prices_fetch.held_assets)}
    assert moved in held and sold not in held
    cards = {r.id for r in read(ws, prices_overview._held)}
    assert cards == held


def _unlisted_funds(run, ws):
    """Three funds the registry does not list, held on one depot: an iShares and an SPDR
    fund the catalogues name, and a fund of an issuer without a catalogue."""
    depot = _depot(run, ws, "Depot")

    def seed(ctx):
        ids = {}
        for isin, name in (
            (ISHARES_ISIN, "iShsIII-Invented USD U.ETF"),
            (SPDR_ISIN, "SPDR Invented EUR UCITS ETF"),
            (OTHER_ISIN, "Xtrackers Invented UCITS ETF"),
        ):
            ids[isin] = asset_for_isin(ctx, isin, name)
            _move(ctx, depot, ids[isin], "internal_transfer_in", "2")
        return ids

    return run(ws, seed, now=FIRST)


def _nav_rows(read, ws):
    def query(conn):
        p = price_table.c
        return conn.execute(
            select(p.asset_id, p.on, p.raw).where(p.source == "issuer_nav").order_by(p.on)
        ).all()

    return read(ws, query)


def test_a_fund_the_registry_does_not_list_is_found_in_its_issuers_catalogue(
    run, read, app_engine, fresh_workspaces
):
    ws, other = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fetched = Recorded()
    sources = Sources(fetch=fetched, coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW)

    # Each catalogue once; the third fund names no issuer with a catalogue and asks nothing.
    assert fetched.asked("product-screener") == fetched.asked("fundfinder") == 1
    assert set(out.discovered) == {ids[ISHARES_ISIN], ids[SPDR_ISIN]}
    assert out.failed == [] and dict(sources.errors) == {}
    rows = _nav_rows(read, ws)
    by_asset = {r.asset_id: r for r in rows}
    assert ids[OTHER_ISIN] not in by_asset
    # The reference found is kept on each row the fund's NAV stored, in the workspace's data.
    ishares = [r for r in rows if r.asset_id == ids[ISHARES_ISIN]]
    spdr = [r for r in rows if r.asset_id == ids[SPDR_ISIN]]
    assert ishares and spdr
    assert all(r.raw["issuer"] == "ishares" and r.raw["fund_ref"] == "1" for r in ishares)
    assert all(r.raw["issuer"] == "spdr" and r.raw["fund_ref"] == "inv-gy" for r in spdr)
    assert _nav_rows(read, other) == []  # RLS

    # The next run reads the stored reference and asks no catalogue.
    again = Recorded()
    later = NOW + timedelta(days=1)
    _, out = run_workspace(app_engine, ws, ACTOR, Sources(fetch=again, coingecko=None), later)
    assert again.asked("product-screener") == again.asked("fundfinder") == 0
    assert again.asked("portfolioId=1&") == again.asked("navhist-emea-en-inv-gy") == 1
    assert out.discovered == {} and out.failed == []

    # The screens name the issuer the fetch used, as for a registry fund.
    overview = read(ws, prices_overview.assets_overview, TODAY)
    positions = {p.asset_id: p for a in overview.accounts for p in a.positions}
    assert positions[ids[ISHARES_ISIN]].price.issuer == "iShares"
    assert positions[ids[SPDR_ISIN]].from_report is False
    assets = {r.id: r for r in read(ws, prices_overview.list_assets, TODAY)}
    assert (assets[ids[ISHARES_ISIN]].issuer, assets[ids[ISHARES_ISIN]].has_source) == (
        "ishares",
        True,
    )
    assert (assets[ids[OTHER_ISIN]].issuer, assets[ids[OTHER_ISIN]].has_source) == (None, False)


def test_a_catalogue_that_fails_costs_its_funds_nothing_but_a_stale_price(
    run, read, app_engine, fresh_workspaces
):
    """A catalogue down is counted once for the log; its fund is not a failed fund (it never
    had an issuer price, so no D174 card), and the other issuer's fund is still priced."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    sources = Sources(fetch=Recorded(down=("product-screener",)), coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW)
    assert dict(sources.errors) == {("issuer_nav:ishares:catalogue", "HTTPError", 503): 1}
    assert out.failed == [] and set(out.discovered) == {ids[SPDR_ISIN]}
    priced = {r.asset_id for r in _nav_rows(read, ws)}
    assert priced == {ids[SPDR_ISIN]}


def test_a_catalogue_reference_naming_another_isin_stores_nothing(
    run, read, app_engine, fresh_workspaces, monkeypatch
):
    """The catalogue is only a candidate: a file stating another ISIN is refused as for a
    registry fund (read_points), and nothing is kept for the next run."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    wrong = ISHARES_ISIN.replace("2", "8")

    def remap(body):
        return {wrong: "1"}

    from hausbuch.services.prices.issuer_nav import ISharesAdapter

    monkeypatch.setattr(ISharesAdapter, "parse_catalogue", staticmethod(remap))

    def seed(ctx):
        depot = ctx.conn.execute(select(movement_table.c.account_id).limit(1)).scalar_one()
        asset_id = asset_for_isin(ctx, wrong, "iShares Invented Wrong")
        _move(ctx, depot, asset_id, "internal_transfer_in", "1")
        return asset_id

    wrong_id = run(ws, seed, now=FIRST)
    sources = Sources(fetch=Recorded(), coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW)
    assert wrong_id in out.failed
    assert ("issuer_nav:ishares", "isin_mismatch", None) in sources.errors
    assert wrong_id not in {r.asset_id for r in _nav_rows(read, ws)}
    assert ids[ISHARES_ISIN] not in {r.asset_id for r in _nav_rows(read, ws)}
