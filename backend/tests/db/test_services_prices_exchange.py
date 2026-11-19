"""The Lang & Schwarz exchange price in the daily fetch (docs/phases/phase-6.md D264), as
hausbuch_app under RLS: a fund with no issuer source priced every run by one request once its
instrument id is stored, Legal & General's one-off history stored in full, a renumbered id
searched again, a failure named as Lang & Schwarz, the first-read check and the card for a
report that disagrees. Every ISIN, id, name and price is invented; the recorded answers are
the synthetic samples of fixtures/phase6/samples/."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import insert, select

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import price as price_table
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.schemas.prices import TypedPriceBody
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor
from hausbuch.services.depot import asset_for_isin
from hausbuch.services.prices import overview as prices_overview
from hausbuch.services.prices.fetch import MAX_ROWS, Sources, exchange_ids, run_workspace
from hausbuch.services.prices.overview import list_assets, record_price

pytestmark = pytest.mark.db

ACTOR = Actor(ActorType.SYSTEM, "fetch_prices")
SAMPLES = __import__("fixture_models_phase6").SAMPLES
ISIN, NAME = "IE00LSINVNT1", "NORDLICHT WELTAKT. A"
LANDG_ISIN, LANDG_NAME = "IE00LSINVNT2", "L&G Invent Theme UCITS ETF"
FIRST = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
NOW = datetime(2026, 11, 19, 6, 30, tzinfo=UTC)  # Thursday; the sample's days 12 to 18 Nov
SEARCH, HISTORY = "instrument/search/main", "dataForInstrument"


class Venue:
    """A Fetch for ls-tc.de: the search and history answers by URL part (the longest part
    first), every other address (the ECB, an issuer) 503; it records every URL."""

    def __init__(self, answers: dict[str, tuple[int, bytes]]) -> None:
        self.answers = sorted(answers.items(), key=lambda a: -len(a[0]))
        self.urls: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes, *body):
        self.urls.append(url)
        if "ls-tc.de" not in url:
            return 503, b""
        for part, answer in self.answers:
            if part in url:
                return answer
        return 404, b""

    def asked(self, part: str) -> int:
        return sum(part in url and "ls-tc.de" in url for url in self.urls)


def _sample(name: str) -> tuple[int, bytes]:
    return 200, (SAMPLES / name).read_bytes()


def _recorded() -> Venue:
    return Venue({SEARCH: _sample("lstc_search.json"), HISTORY: _sample("lstc_history.json")})


def _search(isin: str, instrument_id: int) -> tuple[int, bytes]:
    entry = {"isin": isin, "instrumentId": instrument_id, "categorySymbol": "ETF"}
    return 200, json.dumps([entry]).encode()


def _history(isin: str, days: list[date], price: str = "8.5") -> tuple[int, bytes]:
    data = [
        [int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000), float(price)]
        for d in days
    ]
    body = {"info": {"isin": isin, "plotlines": []}, "series": {"history": {"data": data}}}
    return 200, json.dumps(body).encode()


def _run(app_engine, ws, fetch, now=NOW):
    return run_workspace(app_engine, ws, ACTOR, Sources(fetch=fetch, coingecko=None), now)


def _fund(run, ws, isin=ISIN, name=NAME, first=FIRST):
    depot = run(
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
        now=first,
    ).id

    def seed(ctx):
        asset_id = asset_for_isin(ctx, isin, name)
        ctx.conn.execute(
            insert(movement_table).values(
                workspace_id=ctx.workspace_id,
                account_id=depot,
                asset_id=asset_id,
                kind="internal_transfer_in",
                quantity=Decimal("3.5"),
                occurred_at=first,
                source="securities_transfer",
                status="confirmed",
                created_at=ctx.now,
            )
        )
        return asset_id

    return run(ws, seed, now=first)


def _price(run, ws, asset_id, on, value, source, raw=None, fetched=FIRST):
    def seed(ctx):
        ctx.conn.execute(
            insert(price_table).values(
                workspace_id=ctx.workspace_id,
                asset_id=asset_id,
                on=on,
                price_eur=Decimal(value),
                source=source,
                fetched_at=fetched,
                **({} if raw is None else {"raw": raw}),
                created_at=fetched,
            )
        )

    run(ws, seed, now=fetched)


def _rows(read, ws, asset_id):
    def query(conn):
        p = price_table.c
        return conn.execute(
            select(p.on, p.price_eur, p.raw)
            .where(p.asset_id == asset_id, p.source == "exchange_close")
            .order_by(p.on)
        ).all()

    return read(ws, query)


def _weekdays(start: date, end: date) -> list[date]:
    days, on = [], start
    while on <= end:
        if on.weekday() < 5:
            days.append(on)
        on += timedelta(days=1)
    return days


def test_a_fund_with_no_issuer_source_is_priced_by_the_exchange(
    run, read, app_engine, fresh_workspaces
):
    """The first run searches the ISIN and reads the history: the past weekdays are stored
    as exchange_close with the venue and the id, today's points never. The next run asks the
    history alone, by the stored id, and stores only the day it did not have."""
    ws, _ = fresh_workspaces
    fund = _fund(run, ws)
    first = _recorded()
    _, out = _run(app_engine, ws, first)
    assert (first.asked(SEARCH), first.asked(HISTORY)) == (1, 1)
    assert out.failed == []
    rows = _rows(read, ws, fund)
    assert [r.on for r in rows] == _weekdays(date(2026, 11, 12), date(2026, 11, 18))
    assert rows[0].price_eur == Decimal("8.432")
    assert rows[0].raw == {
        "close": "8.432000",
        "currency": "EUR",
        "venue": "ls_tc",
        "instrument_id": 9000001,
        "quote": "mid",
    }
    assert read(ws, exchange_ids) == {fund: 9000001}

    later = _recorded()
    _run(app_engine, ws, later, NOW + timedelta(days=1))
    assert (later.asked(SEARCH), later.asked(HISTORY)) == (0, 1)
    # Read on Friday: Thursday's day point counts, its live quote (the last point) never.
    assert [r.on for r in _rows(read, ws, fund)][-1] == date(2026, 11, 19)
    assert _rows(read, ws, fund)[-1].price_eur == Decimal("8.4765")

    [row] = [a for a in read(ws, list_assets, NOW.date()) if a.id == fund]
    assert row.price_venue == "Lang & Schwarz" and row.has_source and row.issuer is None
    assert row.latest_price is not None and row.latest_price.source.value == "exchange_close"


def test_a_fund_naming_an_issuers_brand_never_asks_the_exchange(
    run, read, app_engine, fresh_workspaces
):
    """D264 (1): its lookup missing keeps it on its report price; no request to the venue."""
    ws, _ = fresh_workspaces
    _fund(run, ws, isin="IE00LSINVNT9", name="iShares Invent Fund UCITS ETF")
    venue = _recorded()
    _run(app_engine, ws, venue)
    assert venue.asked("ls-tc.de") == 0


def test_a_renumbered_instrument_is_searched_again_in_the_same_run(
    run, read, app_engine, fresh_workspaces
):
    """D264 (6): the stored id's history is gone (404): one search in the run finds the new
    id, which is read at once and kept with the new rows."""
    ws, _ = fresh_workspaces
    fund = _fund(run, ws)
    _run(app_engine, ws, _recorded())
    moved = Venue(
        {
            "instrumentId=9000001&": (404, b""),
            SEARCH: _search(ISIN, 9000003),
            "instrumentId=9000003&": _sample("lstc_history.json"),
        }
    )
    _, out = _run(app_engine, ws, moved, NOW + timedelta(days=1))
    assert (moved.asked(SEARCH), moved.asked(HISTORY)) == (1, 2)
    assert out.failed == []
    assert _rows(read, ws, fund)[-1].raw["instrument_id"] == 9000003
    assert read(ws, exchange_ids) == {fund: 9000003}


def test_an_outage_of_a_stored_id_searches_nothing_and_is_a_failed_fetch(
    run, read, app_engine, fresh_workspaces
):
    """A 5xx is an outage, never a renumbering. Seven failed days raise D174's card, which
    names Lang & Schwarz, and the event names the source exchange_close with its last day."""
    ws, _ = fresh_workspaces
    fund = _fund(run, ws)
    _run(app_engine, ws, _recorded())
    now = NOW
    for n in range(1, 8):
        now = NOW + timedelta(days=n)
        down = Venue({HISTORY: (503, b"")})
        _, out = _run(app_engine, ws, down, now)
        assert down.asked(SEARCH) == 0
        assert out.failed == [fund]
        [note] = [n for n in out.notes if n.asset_id == fund]
        assert (note.issuer, note.fund_ref) == ("ls_tc", "9000001")

    [card] = [c for c in read(ws, prices_overview.price_inbox_items, now) if c.asset_id == fund]
    assert card.kind is InboxItemKind.PRICE_SOURCE_FAILED
    assert "nicht von Lang & Schwarz" in card.reasoning and "älteren Kurs" in card.reasoning

    def raised(conn):
        e = event_table.c
        return conn.execute(
            select(e.payload).where(e.type == "asset.price_source_failed")
        ).scalar_one()

    payload = read(ws, raised)
    assert payload["source"] == "exchange_close"
    assert payload["last_price_on"] == "2026-11-18"


def test_a_fund_the_venue_does_not_list_is_a_lookup_miss(run, read, app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    fund = _fund(run, ws)
    venue = Venue({SEARCH: _sample("lstc_search_empty.json")})
    sources = Sources(fetch=venue, coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW)
    assert out.failed == [] and fund not in {n.asset_id for n in out.notes}
    assert ("exchange_close:search", "not_listed", None) in sources.misses
    assert venue.asked(HISTORY) == 0
    # D264 (8): Anlagen names the venue only once a price of it is stored, so a fund the
    # venue does not list never says it is fetched there every morning.
    [row] = [a for a in read(ws, list_assets, NOW.date()) if a.id == fund]
    assert row.price_venue is None and not row.has_source


def test_a_first_read_far_from_the_report_stores_nothing(run, read, app_engine, fresh_workspaces):
    """D264 (5): the report priced 18 Nov at 9.00, the venue 8.4765 (5.8 % apart): nothing is
    stored, the event names the fund, and it is no failed fetch."""
    ws, _ = fresh_workspaces
    fund = _fund(run, ws)
    _price(run, ws, fund, date(2026, 11, 18), "9.00", "statement")
    _, out = _run(app_engine, ws, _recorded())
    assert out.exchange_disagreed == [fund] and out.failed == []
    assert _rows(read, ws, fund) == []

    def fetched(conn):
        e = event_table.c
        return conn.execute(
            select(e.payload)
            .where(e.type == "workspace.prices_fetched")
            .order_by(e.recorded_at.desc())
            .limit(1)
        ).scalar_one()

    assert read(ws, fetched)["exchange_disagreed"] == [str(fund)]


def test_landg_history_is_read_once_and_stored_in_full(run, read, app_engine, fresh_workspaces):
    """D264 (1), (3): a Legal & General fund (its issuer gives the latest NAV only) gets the
    venue's whole history once, every missing weekday from its first movement on, more than
    MAX_ROWS of them; its issuer's NAV keeps its own day. The next run asks the venue nothing."""
    ws, _ = fresh_workspaces
    first = datetime(2024, 7, 1, 9, 0, tzinfo=UTC)
    fund = _fund(run, ws, isin=LANDG_ISIN, name=LANDG_NAME, first=first)
    _price(
        run,
        ws,
        fund,
        date(2026, 11, 17),
        "8.40",
        "issuer_nav",
        raw={"issuer": "landg", "fund_ref": "invent-theme", "nav": "8.40", "currency": "EUR"},
        fetched=datetime(2026, 11, 18, 6, 30, tzinfo=UTC),
    )
    days = _weekdays(date(2024, 1, 2), date(2026, 11, 18))
    venue = Venue(
        {SEARCH: _search(LANDG_ISIN, 9000002), HISTORY: _history(LANDG_ISIN, days, "8.45")}
    )
    _, out = _run(app_engine, ws, venue)
    assert (venue.asked(SEARCH), venue.asked(HISTORY)) == (1, 1)
    stored = [r.on for r in _rows(read, ws, fund)]
    expected = _weekdays(first.date(), date(2026, 11, 18))
    assert len(expected) > MAX_ROWS
    assert stored == expected  # its NAV's day too: both stored, the NAV wins on read
    assert out.failed_sources.get(fund) is None  # its issuer's own 503 is an issuer failure

    again = Venue({SEARCH: _search(LANDG_ISIN, 9000002), HISTORY: (503, b"")})
    _, out = _run(app_engine, ws, again, NOW + timedelta(days=1))
    assert again.asked("ls-tc.de") == 0


def test_landg_one_off_failing_is_no_failed_fetch(run, read, app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    fund = _fund(run, ws, isin=LANDG_ISIN, name=LANDG_NAME)
    _price(
        run,
        ws,
        fund,
        date(2026, 11, 17),
        "8.40",
        "issuer_nav",
        raw={"issuer": "landg", "fund_ref": "invent-theme", "nav": "8.40", "currency": "EUR"},
    )
    venue = Venue({SEARCH: _search(LANDG_ISIN, 9000002), HISTORY: (503, b"")})
    _, out = _run(app_engine, ws, venue)
    assert venue.asked(HISTORY) == 1
    assert "ls_tc" not in {n.issuer for n in out.notes if n.asset_id == fund}
    assert out.failed_sources.get(fund) is None


def test_a_report_the_exchange_disagrees_with_raises_a_card_until_a_price_is_typed(
    run, read, app_engine, fresh_workspaces
):
    """D264 (7): on the newest report day the venue's price is 3.0 % below the report's; the
    card names both and only alerts. A price typed for that day ends it."""
    ws, _ = fresh_workspaces
    fund = _fund(run, ws)
    day = date(2026, 11, 18)
    _price(run, ws, fund, date(2026, 9, 30), "8.00", "statement")
    _price(run, ws, fund, day, "8.70", "exchange_close", raw={"instrument_id": 9000001})
    _price(run, ws, fund, day, "8.97", "statement")

    def cards():
        return [
            c
            for c in read(ws, prices_overview.price_inbox_items, NOW)
            if c.kind is InboxItemKind.PRICE_REPORT_DIFFERS
        ]

    [card] = cards()
    assert card.asset_id == fund
    assert "18.11.2026" in card.reasoning and "8,97 €" in card.reasoning
    assert "8,70 €" in card.reasoning and "Lang & Schwarz" in card.reasoning
    assert "3,0 % Abstand" in card.reasoning
    run(ws, record_price, fund, TypedPriceBody(on=day, price_eur=Decimal("8.97")), now=NOW)
    assert cards() == []


def test_a_report_within_two_percent_raises_no_card(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    fund = _fund(run, ws)
    day = date(2026, 11, 18)
    _price(run, ws, fund, day, "8.80", "exchange_close", raw={"instrument_id": 9000001})
    _price(run, ws, fund, day, "8.97", "statement")
    kinds = {c.kind for c in read(ws, prices_overview.price_inbox_items, NOW)}
    assert InboxItemKind.PRICE_REPORT_DIFFERS not in kinds
