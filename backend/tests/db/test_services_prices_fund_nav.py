"""Which funds the daily fetch prices from their issuer (docs/phases/phase-6.md D174, amended
16 Nov 2026), as hausbuch_app under RLS: every registry fund a depot holds, a fund moved
between own depots, and a fund the registry does not list found in its issuer's catalogue by
its ISIN, its reference kept in the workspace's price rows. The registry's funds are taken
from the registry itself, so no test names a fund a household holds; every other ISIN is
invented, and the recorded files are the synthetic samples of fixtures/phase6/samples/."""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import insert, select

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import price as price_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.fetch_prices import run_fetch_prices
from hausbuch.rules.household.assets import registry
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.schemas.prices import TypedPriceBody
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor
from hausbuch.services.depot import asset_for_isin
from hausbuch.services.prices import fetch as prices_fetch
from hausbuch.services.prices import overview as prices_overview
from hausbuch.services.prices.fetch import Sources, run_workspace
from hausbuch.services.prices.issuer_nav import ADAPTERS, ISharesAdapter
from hausbuch.services.prices.overview import record_price
from hausbuch.settings import Settings

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


# --- follow-ups of the PR #381 review (D174 amended 16 Nov 2026) ----------------------------


class Served(Recorded):
    """Recorded, with some URL parts answered by other bodies or statuses (checked first)."""

    def __init__(self, served: dict[str, tuple[int, bytes]], down: tuple[str, ...] = ()):
        super().__init__(down)
        self.served = served

    def __call__(self, url, headers, timeout, max_bytes):
        for part, answer in self.served.items():
            if part in url:
                self.urls.append(url)
                return answer
        return super().__call__(url, headers, timeout, max_bytes)


def _ishares_file(*, day: bytes = b"26.Juni2026", currency: bytes = b"USD") -> bytes:
    """The synthetic iShares file (IE000INVENT2), its newest NAV day or its NAV currency
    replaced in memory (the fixture file itself is unchanged)."""
    body = (SAMPLES / "ishares_fund_download.xls").read_bytes()
    body = body.replace(b">26.Juni2026<", b">" + day + b"<")
    return body.replace(b">USD 81,20<", b">" + currency + b" 81,20<")


def _cards(read, ws, now):
    return read(ws, prices_overview.price_inbox_items, now)


def _run(app_engine, ws, fetch, now):
    return run_workspace(app_engine, ws, ACTOR, Sources(fetch=fetch, coingecko=None), now)


def test_a_stored_reference_that_stopped_working_is_found_again_in_the_catalogue(
    run, read, app_engine, fresh_workspaces, monkeypatch
):
    """Follow-up A: day 1 prices the fund through the catalogue's reference 1; on day 2 that
    file is gone (404) and the catalogue lists 2: the same run reads 2 and its row keeps 2;
    day 3 reads 2 at once, no catalogue."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fund = ids[ISHARES_ISIN]
    _run(app_engine, ws, Recorded(), NOW)
    assert {r.raw["fund_ref"] for r in _nav_rows(read, ws) if r.asset_id == fund} == {"1"}

    monkeypatch.setattr(
        ISharesAdapter, "parse_catalogue", staticmethod(lambda body: {ISHARES_ISIN: "2"})
    )
    moved = Served(
        {
            "portfolioId=1&": (404, b""),
            "portfolioId=2&": (200, _ishares_file(day=b"27.Juni2026")),
        }
    )
    sources = Sources(fetch=moved, coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW + timedelta(days=1))
    assert moved.asked("product-screener") == 1
    assert moved.asked("portfolioId=1&") == moved.asked("portfolioId=2&") == 1
    assert out.failed == [] and out.discovered[fund].fund_ref == "2"
    assert dict(sources.errors) == {("issuer_nav:ishares", "HTTPError", 404): 1}
    [new_row] = [r for r in _nav_rows(read, ws) if r.asset_id == fund and r.on == TODAY]
    assert (new_row.raw["issuer"], new_row.raw["fund_ref"]) == ("ishares", "2")

    third = Served(
        {
            "portfolioId=1&": (404, b""),
            "portfolioId=2&": (200, _ishares_file(day=b"27.Juni2026")),
        }
    )
    _run(app_engine, ws, third, NOW + timedelta(days=2))
    assert third.asked("product-screener") == third.asked("portfolioId=1&") == 0
    assert third.asked("portfolioId=2&") == 1


def test_an_outage_of_a_stored_reference_asks_no_catalogue(run, read, app_engine, fresh_workspaces):
    """A 503 is an outage, not a moved fund: the fund fails for the day, nothing else."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    _run(app_engine, ws, Recorded(), NOW)
    down = Recorded(down=("portfolioId=1&",))
    _, out = _run(app_engine, ws, down, NOW + timedelta(days=1))
    assert down.asked("product-screener") == 0 and down.asked("portfolioId=1&") == 1
    assert out.failed == [ids[ISHARES_ISIN]] and out.discovered == {}


def _fail_for(app_engine, ws, days: int, start: datetime) -> datetime:
    """`days` daily runs from `start` on which the iShares file answers 503."""
    now = start
    for n in range(days):
        now = start + timedelta(days=n)
        _run(app_engine, ws, Recorded(down=("portfolioId=1&",)), now)
    return now


def test_a_catalogue_fund_whose_file_never_worked_gets_the_source_failed_card(
    run, read, app_engine, fresh_workspaces
):
    """Follow-up B: the catalogue names the fund but its file fails from the first day, so
    no row ever keeps the reference. The card is D174's („kam nicht von iShares“) after
    seven days, never „veröffentlicht keinen Kurs“; the fund no catalogue names keeps that."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fund, other = ids[ISHARES_ISIN], ids[OTHER_ISIN]
    first = _fail_for(app_engine, ws, 1, NOW)
    by_asset = {c.asset_id: c for c in _cards(read, ws, first)}
    assert fund not in by_asset  # under seven days: no card, and never the false one
    assert by_asset[other].kind is InboxItemKind.PRICE_NO_SOURCE

    last = _fail_for(app_engine, ws, 7, NOW)
    by_asset = {c.asset_id: c for c in _cards(read, ws, last)}
    card = by_asset[fund]
    assert card.kind is InboxItemKind.PRICE_SOURCE_FAILED
    assert "nicht von iShares" in card.reasoning and "fehlt sein Wert" in card.reasoning
    assert by_asset[other].kind is InboxItemKind.PRICE_NO_SOURCE

    def raised(conn):
        e = event_table.c
        return conn.execute(
            select(e.entity_id, e.payload).where(e.type == "asset.price_source_failed")
        ).all()

    [(entity, payload)] = read(ws, raised)
    assert entity == fund and payload["failed_days"] == 7
    assert payload["last_price_on"] is None


def test_a_stored_reference_naming_another_isin_is_found_again_in_the_catalogue(
    run, read, app_engine, fresh_workspaces, monkeypatch
):
    """Follow-up A, the other trigger: the stored reference's file now states another ISIN
    (the issuer renumbered its funds), so the catalogue is asked and its reference read."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fund = ids[ISHARES_ISIN]
    _run(app_engine, ws, Recorded(), NOW)
    monkeypatch.setattr(
        ISharesAdapter, "parse_catalogue", staticmethod(lambda body: {ISHARES_ISIN: "2"})
    )
    other_fund = _ishares_file(day=b"27.Juni2026").replace(b"IE000INVENT2", b"IE000INVENT8")
    renumbered = Served(
        {
            "portfolioId=1&": (200, other_fund),
            "portfolioId=2&": (200, _ishares_file(day=b"27.Juni2026")),
        }
    )
    sources = Sources(fetch=renumbered, coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW + timedelta(days=1))
    assert renumbered.asked("product-screener") == 1
    assert out.failed == [] and out.discovered[fund].fund_ref == "2"
    [new_row] = [r for r in _nav_rows(read, ws) if r.asset_id == fund and r.on == TODAY]
    assert new_row.raw["fund_ref"] == "2"


def test_a_stored_reference_that_fails_gets_the_source_failed_card(
    run, read, app_engine, fresh_workspaces
):
    """Follow-up B, the other way: a fund priced once through its catalogue reference, then
    failing seven days, gets D174's card naming the issuer and its older price."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    _run(app_engine, ws, Recorded(), NOW)
    last = _fail_for(app_engine, ws, 7, NOW + timedelta(days=1))
    [card] = [c for c in _cards(read, ws, last) if c.asset_id == ids[ISHARES_ISIN]]
    assert card.kind is InboxItemKind.PRICE_SOURCE_FAILED
    assert "nicht von iShares" in card.reasoning and "älteren Kurs" in card.reasoning


def _ecb_rows(read, ws, symbol):
    def query(conn):
        p, a = price_table.c, asset_table.c
        return conn.execute(
            select(p.on, p.raw)
            .join(asset_table, a.id == p.asset_id)
            .where(p.source == "ecb_fx", a.symbol == symbol)
            .order_by(p.on)
        ).all()

    return read(ws, query)


def _issuer_price(read, ws, asset_id):
    def query(conn):
        p = price_table.c
        return conn.execute(
            select(p.price_eur).where(p.asset_id == asset_id, p.source == "issuer_nav")
        ).scalar_one()

    return read(ws, query)


def test_a_nav_in_another_ecb_currency_is_converted_by_its_ecb_rate(
    run, read, app_engine, fresh_workspaces
):
    """Follow-up C: a workspace that already has its dollar rates holds a fund quoted in
    yen. The run stores the ECB's yen rate (the 90-day file asked too, for the first need)
    and the NAV converted by it, raw keeping the yen figure."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fund = ids[ISHARES_ISIN]
    _run(app_engine, ws, Recorded(down=("portfolioId=1&",)), NOW)
    assert _ecb_rows(read, ws, "USD") and _ecb_rows(read, ws, "JPY") == []

    yen = Served({"portfolioId=1&": (200, _ishares_file(currency=b"JPY"))})
    _, out = _run(app_engine, ws, yen, NOW + timedelta(days=1))
    assert out.failed == [] and out.unconverted == {}
    assert yen.asked("eurofxref-daily") == yen.asked("eurofxref-hist-90d") == 1
    assert [(r.on, r.raw) for r in _ecb_rows(read, ws, "JPY")] == [
        (date(2026, 6, 26), {"per_eur": "170.10"})
    ]
    rows = [r for r in _nav_rows(read, ws) if r.asset_id == fund]
    # The 25th has no yen rate on or before it in the files: nothing stored for it.
    assert [r.on for r in rows] == [date(2026, 6, 26)]
    assert rows[0].raw == {
        "nav": "81.20000000",
        "currency": "JPY",
        "rate_on": "2026-06-26",
        "issuer": "ishares",
        "fund_ref": "1",
    }
    expected = (Decimal("81.20") / Decimal("170.10")).quantize(Decimal("0.00000001"))
    assert _issuer_price(read, ws, fund) == expected
    assert not [c for c in _cards(read, ws, NOW + timedelta(days=1)) if c.asset_id == fund]


def test_a_nav_in_a_currency_the_ecb_does_not_publish_is_a_card_never_a_price(
    run, read, app_engine, fresh_workspaces
):
    """Follow-up C, the rest: no ECB rate converts the NAV, so nothing is stored and a card
    names the fund and the currency at once; a typed price ends it."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fund = ids[ISHARES_ISIN]
    odd = Served({"portfolioId=1&": (200, _ishares_file(currency=b"SEK"))})
    _, out = _run(app_engine, ws, odd, NOW)
    assert out.unconverted == {fund: "SEK"} and fund not in out.failed
    assert fund not in {r.asset_id for r in _nav_rows(read, ws)}
    [card] = [c for c in _cards(read, ws, NOW) if c.asset_id == fund]
    assert card.kind is InboxItemKind.PRICE_SOURCE_FAILED
    assert "in SEK" in card.reasoning and "iShsIII-Invented USD U.ETF" in card.reasoning
    assert "Kurs eintragen" in card.question

    def typed(ctx):
        return record_price(ctx, fund, TypedPriceBody(on=TODAY, price_eur=Decimal("7.5")))

    later = NOW + timedelta(hours=1)
    run(ws, typed, now=later)
    assert not [c for c in _cards(read, ws, later) if c.asset_id == fund]


# --- review of PR #391 -----------------------------------------------------------------------

FX = {"USD": "1.0850", "GBP": "0.84500", "CHF": "0.9500"}


def _ecb_xml(days: dict[date, dict[str, str]]) -> bytes:
    """An ECB reference-rate file holding `days` (one Cube per day)."""
    cubes = "".join(
        f"<Cube time='{on.isoformat()}'>"
        + "".join(f"<Cube currency='{code}' rate='{rate}'/>" for code, rate in rates.items())
        + "</Cube>"
        for on, rates in days.items()
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<gesmes:Envelope xmlns:gesmes='http://www.gesmes.org/xml/2002-08-01' "
        "xmlns='http://www.ecb.int/vocabulary/2002-08-01/eurofxref'>"
        f"<Cube>{cubes}</Cube></gesmes:Envelope>"
    ).encode()


def _yen_rows(read, ws, fund):
    return {r.on: r.raw for r in _nav_rows(read, ws) if r.asset_id == fund}


def test_a_held_currency_s_rate_is_stored_while_the_fund_file_fails(
    run, read, app_engine, fresh_workspaces
):
    """Finding 1 (a): once the workspace holds yen rates, every run stores the day's yen rate
    like the dollar's, so a NAV day a failed file left to a later run is converted by its
    own day's rate, never the last one stored before the failure."""
    ws, _ = fresh_workspaces
    fund = _unlisted_funds(run, ws)[ISHARES_ISIN]
    days = {date(2026, 6, d): FX | {"JPY": f"{142 + d}.00"} for d in (24, 25, 26)}
    first = Served(
        {
            "eurofxref-hist-90d": (200, _ecb_xml(days)),
            "portfolioId=1&": (200, _ishares_file(currency=b"JPY")),
        }
    )
    _run(app_engine, ws, first, NOW)
    outage = Served(
        {
            "eurofxref-daily": (200, _ecb_xml({date(2026, 6, 27): FX | {"JPY": "171.00"}})),
            "portfolioId=1&": (503, b""),
        }
    )
    _, out = _run(app_engine, ws, outage, NOW + timedelta(days=1))
    assert out.failed == [fund]
    assert (date(2026, 6, 27), {"per_eur": "171.00"}) in [
        (r.on, r.raw) for r in _ecb_rows(read, ws, "JPY")
    ]
    later_file = _ishares_file(day=b"28.Juni2026", currency=b"JPY").replace(
        b">25.Juni2026<", b">27.Juni2026<"
    )
    back = Served(
        {
            "eurofxref-daily": (200, _ecb_xml({date(2026, 6, 28): FX | {"JPY": "172.00"}})),
            "portfolioId=1&": (200, later_file),
        }
    )
    _run(app_engine, ws, back, NOW + timedelta(days=2))
    rows = _yen_rows(read, ws, fund)
    assert {on: raw["rate_on"] for on, raw in rows.items()} == {
        date(2026, 6, d): f"2026-06-{d}" for d in (25, 26, 27, 28)
    }


def test_a_currency_absent_on_the_newest_ecb_day_is_a_card_never_a_price(
    run, read, app_engine, fresh_workspaces
):
    """Finding 1 (b), a first need: the 90-day file lists yen on older days only (the ECB
    stopped publishing it), so no NAV is converted at that frozen rate: nothing is stored,
    no yen rate either, and the card names the fund and the currency at once."""
    ws, _ = fresh_workspaces
    fund = _unlisted_funds(run, ws)[ISHARES_ISIN]
    days = {
        date(2026, 6, 24): FX | {"JPY": "168.00"},
        date(2026, 6, 25): FX | {"JPY": "169.00"},
        date(2026, 6, 26): FX,
    }
    dropped = Served(
        {
            "eurofxref-hist-90d": (200, _ecb_xml(days)),
            "portfolioId=1&": (200, _ishares_file(currency=b"JPY")),
        }
    )
    _, out = _run(app_engine, ws, dropped, NOW)
    assert out.unconverted == {fund: "JPY"}
    assert _yen_rows(read, ws, fund) == {} and _ecb_rows(read, ws, "JPY") == []
    [card] = [c for c in _cards(read, ws, NOW) if c.asset_id == fund]
    assert card.kind is InboxItemKind.PRICE_SOURCE_FAILED and "in JPY" in card.reasoning


def test_a_stored_currency_the_ecb_drops_is_never_converted_at_its_last_rate(
    run, read, app_engine, fresh_workspaces
):
    """Finding 1 (b), a stored currency: priced in yen on day 1; on day 2 the daily file has
    no yen, so the new NAV day stores nothing and the card shows."""
    ws, _ = fresh_workspaces
    fund = _unlisted_funds(run, ws)[ISHARES_ISIN]
    days = {date(2026, 6, d): FX | {"JPY": f"{142 + d}.00"} for d in (24, 25, 26)}
    _run(
        app_engine,
        ws,
        Served(
            {
                "eurofxref-hist-90d": (200, _ecb_xml(days)),
                "portfolioId=1&": (200, _ishares_file(currency=b"JPY")),
            }
        ),
        NOW,
    )
    assert set(_yen_rows(read, ws, fund)) == {date(2026, 6, 25), date(2026, 6, 26)}
    later = NOW + timedelta(days=1)
    gone = Served(
        {
            "eurofxref-daily": (200, _ecb_xml({date(2026, 6, 27): FX})),
            "portfolioId=1&": (200, _ishares_file(day=b"27.Juni2026", currency=b"JPY")),
        }
    )
    _, out = _run(app_engine, ws, gone, later)
    assert out.unconverted == {fund: "JPY"}
    assert set(_yen_rows(read, ws, fund)) == {date(2026, 6, 25), date(2026, 6, 26)}
    [card] = [c for c in _cards(read, ws, later) if c.asset_id == fund]
    assert card.kind is InboxItemKind.PRICE_SOURCE_FAILED and "in JPY" in card.reasoning


def test_an_older_note_never_hides_the_card_of_a_fund_without_a_price(
    run, read, app_engine, fresh_workspaces
):
    """Finding 2: day 1 the catalogue names the fund and its file fails; day 2 the catalogue
    is down, so the fund is not asked and not failed. It has no price, so a card shows at
    once, saying it has no current price, never that iShares publishes none."""
    ws, _ = fresh_workspaces
    fund = _unlisted_funds(run, ws)[ISHARES_ISIN]
    _run(app_engine, ws, Recorded(down=("portfolioId=1&",)), NOW)
    later = NOW + timedelta(days=1)
    _run(app_engine, ws, Recorded(down=("portfolioId=1&", "product-screener")), later)
    [card] = [c for c in _cards(read, ws, later) if c.asset_id == fund]
    assert card.kind is InboxItemKind.PRICE_NO_SOURCE
    assert "keinen aktuellen Kurs" in card.reasoning and "iShares" in card.reasoning
    assert "veröffentlicht" not in card.reasoning
    assert "Kurs eintragen" in card.question


def test_an_unknown_product_s_400_asks_the_catalogue(
    run, read, app_engine, fresh_workspaces, monkeypatch
):
    """Finding 3: iShares answers 400 for a product id it does not know, so a 400 counts as
    a moved fund; the file read through the catalogue's reference is ISIN-checked anyway."""
    ws, _ = fresh_workspaces
    fund = _unlisted_funds(run, ws)[ISHARES_ISIN]
    _run(app_engine, ws, Recorded(), NOW)
    monkeypatch.setattr(
        ISharesAdapter, "parse_catalogue", staticmethod(lambda body: {ISHARES_ISIN: "2"})
    )
    unknown = Served(
        {
            "portfolioId=1&": (400, b""),
            "portfolioId=2&": (200, _ishares_file(day=b"27.Juni2026")),
        }
    )
    _, out = _run(app_engine, ws, unknown, NOW + timedelta(days=1))
    assert unknown.asked("product-screener") == 1
    assert out.failed == [] and out.discovered[fund].fund_ref == "2"
    [new_row] = [r for r in _nav_rows(read, ws) if r.asset_id == fund and r.on == TODAY]
    assert new_row.raw["fund_ref"] == "2"


# --- D174 amended 19 Nov 2026: the ECB history (a) and the first-read check (g) ------------

FIRST_MARCH = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)  # a Sunday: the rate before is Friday's


def _history_zip(days: dict[date, str]) -> bytes:
    """The ECB's eurofxref-hist.zip in its layout (one CSV, newest first, a trailing comma)
    with these USD rates and the other columns empty („N/A“)."""
    import io
    import zipfile

    lines = ["Date,USD,JPY,GBP,CHF,"]
    for on in sorted(days, reverse=True):
        lines.append(f"{on.isoformat()},{days[on]},N/A,0.85000,0.95000,")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("eurofxref-hist.csv", "\n".join(lines) + "\n")
    return buffer.getvalue()


HISTORY = {
    date(2026, 2, 26): "1.0380",
    date(2026, 2, 27): "1.0390",
    date(2026, 3, 2): "1.0400",
    date(2026, 5, 29): "1.0700",
    date(2026, 6, 26): "1.0850",
}


def _fund_held_since(run, ws, occurred_at):
    """The iShares fund the catalogue names, held since `occurred_at`."""
    depot = _depot(run, ws, "Depot")

    def seed(ctx):
        asset_id = asset_for_isin(ctx, ISHARES_ISIN, "iShsIII-Invented USD U.ETF")
        ctx.conn.execute(
            insert(movement_table).values(
                workspace_id=ctx.workspace_id,
                account_id=depot,
                asset_id=asset_id,
                kind="internal_transfer_in",
                quantity=Decimal("2"),
                occurred_at=occurred_at,
                source="securities_transfer",
                status="confirmed",
                created_at=ctx.now,
            )
        )
        return asset_id

    return run(ws, seed, now=occurred_at)


def test_a_fund_held_before_the_90_day_file_gets_its_rates_from_the_ecb_history(
    run, read, app_engine, fresh_workspaces
):
    """(a): the 90-day file starts in June, the fund is held since 1 Mar. The run asks the
    history ZIP once and stores its dollar rates from the last one before 1 Mar (Friday
    27 Feb) on, never the 26th, so the 2 Mar NAV is converted by its own day's rate; the next
    run asks the ZIP no more."""
    ws, _ = fresh_workspaces
    fund = _fund_held_since(run, ws, FIRST_MARCH)
    fetch = Served({"eurofxref-hist.zip": (200, _history_zip(HISTORY))})
    _, out = _run(app_engine, ws, fetch, NOW)
    assert fetch.asked("eurofxref-hist.zip") == 1
    assert out.failed == [] and out.nav_disagreed == []
    usd = {r.on: r.raw["per_eur"] for r in _ecb_rows(read, ws, "USD")}
    assert date(2026, 2, 26) not in usd
    assert usd[date(2026, 2, 27)] == "1.0390" and usd[date(2026, 3, 2)] == "1.0400"
    assert usd[date(2026, 6, 24)] == "1.0800"  # the 90-day file's, not the history's
    navs = {r.on: r.raw for r in _nav_rows(read, ws) if r.asset_id == fund}
    assert navs[date(2026, 3, 2)] == {
        "nav": "95.10000000",
        "currency": "USD",
        "rate_on": "2026-03-02",
        "issuer": "ishares",
        "fund_ref": "1",
    }
    assert date(2025, 9, 15) not in navs  # before the first movement ((3) unchanged)

    again = Served({"eurofxref-hist.zip": (200, _history_zip(HISTORY))})
    _run(app_engine, ws, again, NOW + timedelta(days=1))
    assert again.asked("eurofxref-hist.zip") == 0


def test_a_failed_ecb_history_is_counted_and_never_fails_the_run(
    run, read, app_engine, fresh_workspaces
):
    """(a): the ZIP answers 503: the log line counts it, the June NAVs the 90-day file
    converts are stored, the March one waits for its rate, and the next run asks again."""
    ws, _ = fresh_workspaces
    fund = _fund_held_since(run, ws, FIRST_MARCH)
    sources = Sources(fetch=Served({"eurofxref-hist.zip": (503, b"")}), coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW)
    assert dict(sources.errors) == {("ecb_fx:history", "HTTPError", 503): 1}
    assert out.failed == []
    days = {r.on for r in _nav_rows(read, ws) if r.asset_id == fund}
    assert date(2026, 6, 26) in days and date(2026, 3, 2) not in days

    again = Served({"eurofxref-hist.zip": (200, _history_zip(HISTORY))})
    _run(app_engine, ws, again, NOW + timedelta(days=1))
    assert again.asked("eurofxref-hist.zip") == 1
    assert date(2026, 3, 2) in {r.on for r in _nav_rows(read, ws) if r.asset_id == fund}


def _report_price(run, ws, asset_id, on, price):
    def seed(ctx):
        ctx.conn.execute(
            insert(price_table).values(
                workspace_id=ctx.workspace_id,
                asset_id=asset_id,
                on=on,
                price_eur=Decimal(price),
                source="statement",
                fetched_at=ctx.now,
                raw={},
                created_at=ctx.now,
            )
        )

    run(ws, seed, now=FIRST)


def _fetched_events(read, ws):
    def query(conn):
        e = event_table.c
        return (
            conn.execute(
                select(e.payload).where(e.type == "workspace.prices_fetched").order_by(e.seq)
            )
            .scalars()
            .all()
        )

    return read(ws, query)


def test_a_first_read_far_from_the_report_stores_nothing_and_is_named(
    run, read, app_engine, fresh_workspaces
):
    """(g): the report priced the fund at 50.00 € on 26 Jun; the file's NAV that day is
    81.20 USD, 74.84 € at 1.0850, more than 2 % away. Nothing is stored for it, the run's
    event names it, it is no failed fetch; the other fund of the run is stored."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fund = ids[ISHARES_ISIN]
    _report_price(run, ws, fund, date(2026, 6, 26), "50.00")
    _, out = _run(app_engine, ws, Recorded(), NOW)
    assert out.nav_disagreed == [fund] and out.failed == []
    rows = _nav_rows(read, ws)
    assert fund not in {r.asset_id for r in rows}
    assert ids[SPDR_ISIN] in {r.asset_id for r in rows}
    [event] = _fetched_events(read, ws)
    assert event["nav_disagreed"] == [str(fund)] and event["failed_asset_ids"] == []


def test_a_first_read_within_two_percent_of_the_report_is_stored_and_not_checked_again(
    run, read, app_engine, fresh_workspaces
):
    """(g): the report's 74.00 € is within 2 % of 74.84 €, so the file is stored. Once the
    fund has issuer rows the check no longer runs: a report far from the next NAV changes
    nothing."""
    ws, _ = fresh_workspaces
    ids = _unlisted_funds(run, ws)
    fund = ids[ISHARES_ISIN]
    _report_price(run, ws, fund, date(2026, 6, 26), "74.00")
    _, out = _run(app_engine, ws, Recorded(), NOW)
    assert out.nav_disagreed == []
    assert date(2026, 6, 26) in {r.on for r in _nav_rows(read, ws) if r.asset_id == fund}

    _report_price(run, ws, fund, TODAY, "10.00")
    later = Served({"portfolioId=1&": (200, _ishares_file(day=b"27.Juni2026"))})
    _, out = _run(app_engine, ws, later, NOW + timedelta(days=1))
    assert out.nav_disagreed == []
    assert TODAY in {r.on for r in _nav_rows(read, ws) if r.asset_id == fund}


# --- the review of the build (D174 amended after the review, 19 Nov 2026) -------------------

BNP_ISIN = "IE000INVENT8"
BNP_FILE = (SAMPLES / "bnp_navs.json").read_bytes()
BNP_EMPTY = (SAMPLES / "bnp_navs_empty.json").read_bytes()


def _bnp_fund(run, ws):
    depot = _depot(run, ws, "Depot")

    def seed(ctx):
        fund = asset_for_isin(ctx, BNP_ISIN, "BNPP Invented Equity ETF")
        _move(ctx, depot, fund, "internal_transfer_in", "2")
        return fund

    return run(ws, seed, now=FIRST)


def test_bnp_discovery_with_two_currencies_answering_stores_nothing_and_names_the_fund(
    run, read, app_engine, fresh_workspaces
):
    """(h): EUR and USD both have rows for the ISIN: no currency is chosen, nothing is
    stored, the run's event names the fund, and it is no failed fetch."""
    ws, _ = fresh_workspaces
    fund = _bnp_fund(run, ws)
    fetch = Served({"currency=EUR": (200, BNP_FILE), "currency=USD": (200, BNP_FILE)})
    _, out = _run(app_engine, ws, fetch, NOW)
    assert out.nav_unresolved == [fund] and out.failed == [] and fund not in out.discovered
    assert fund not in {r.asset_id for r in _nav_rows(read, ws)}
    [event] = _fetched_events(read, ws)
    assert event["nav_unresolved"] == [str(fund)] and event["failed_asset_ids"] == []


def test_a_stored_bnp_currency_that_stops_answering_is_never_switched(
    run, read, app_engine, fresh_workspaces
):
    """(h): day 1 finds the fund in USD alone and stores its reference; on day 2 USD has no
    rows and EUR has: nothing is stored, the USD reference stays, the fund is named and is no
    failed fetch; day 3, USD back, reads USD again."""
    ws, _ = fresh_workspaces
    fund = _bnp_fund(run, ws)
    _, out = _run(app_engine, ws, Served({"currency=USD": (200, BNP_FILE)}), NOW)
    assert out.discovered[fund].fund_ref == f"USD:{BNP_ISIN}"
    before = _nav_rows(read, ws)
    assert {r.raw["fund_ref"] for r in before if r.asset_id == fund} == {f"USD:{BNP_ISIN}"}

    moved = Served({"currency=USD": (200, BNP_EMPTY), "currency=EUR": (200, BNP_FILE)})
    _, out = _run(app_engine, ws, moved, NOW + timedelta(days=1))
    assert out.nav_unresolved == [fund] and out.failed == [] and fund not in out.discovered
    assert _nav_rows(read, ws) == before
    events = _fetched_events(read, ws)
    assert events[-1]["nav_unresolved"] == [str(fund)]
    assert events[-1]["failed_asset_ids"] == []

    back = Served({"currency=USD": (200, BNP_FILE)})
    _, out = _run(app_engine, ws, back, NOW + timedelta(days=2))
    assert out.nav_unresolved == [] and out.failed == []
    assert back.asked("currency=USD") == 1 and back.asked("currency=EUR") == 0


def test_the_run_s_log_line_counts_lookup_misses_apart_from_errors(
    run, app_engine, fresh_workspaces, caplog
):
    """(d), (k): the Xtrackers fund no issuer lists is asked once and answered 404: the log
    line counts it under `lookup_misses`, not `errors`, and the run is no warning."""
    ws, _ = fresh_workspaces
    _unlisted_funds(run, ws)
    settings = Settings(
        database_url="postgresql+psycopg://unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
    )
    deps = JobDeps(
        engine=app_engine, settings=settings, blob_store=None, model_client=None, clock=lambda: NOW
    )
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.fetch_prices"):
        result = run_fetch_prices(deps, fetch=Recorded(), workspace_ids=[ws])
    assert result.lookup_misses == {"issuer_nav:xtrackers:HTTPError:404": 1}
    assert result.errors == {} and result.assets_failed == 0
    [record] = caplog.records
    assert record.levelno == logging.INFO
    assert record.fields["lookup_misses"] == result.lookup_misses
    assert OTHER_ISIN not in caplog.text
