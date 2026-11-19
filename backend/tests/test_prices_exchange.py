"""D264: exchange_price.yaml replayed against the code (no network, no database): the Lang &
Schwarz search and history over the recorded samples, the fetch's choice of fund, the
precedence with exchange_close, the first-read check and the report card's gap."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import valuation
from hausbuch.schemas.household import PricePoint
from hausbuch.services.prices import lang_schwarz
from hausbuch.services.prices.http import USER_AGENT, SourceError
from hausbuch.services.prices.issuer_nav import ADAPTERS

EXCHANGE = phase6.load("exchange_price")
FETCHED = datetime(2026, 11, 19, 6, 30, tzinfo=UTC)
ASSET = UUID("00000000-0000-4000-8000-000000000264")


class Recorder:
    """A Fetch answering one recorded sample to every URL, recording the URL and headers."""

    def __init__(self, sample: str) -> None:
        self.body = (phase6.SAMPLES / sample).read_bytes()
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url, headers, timeout, max_bytes, *body):
        assert not body  # two GETs, never a POST
        self.calls.append((url, headers))
        return 200, self.body


@pytest.mark.parametrize("case", EXCHANGE.search_cases, ids=lambda c: c.id)
def test_search_cases(case):
    fetch = Recorder(case.sample)
    if case.expect.error is not None:
        with pytest.raises(SourceError) as raised:
            lang_schwarz.instrument(fetch, case.isin)
        assert raised.value.kind == case.expect.error
    else:
        assert lang_schwarz.instrument(fetch, case.isin) == case.expect.instrument_id
    ((url, headers),) = fetch.calls
    assert url == lang_schwarz.SEARCH_URL.format(isin=case.isin)
    assert headers["User-Agent"] == USER_AGENT  # the app's own, never a browser's


@pytest.mark.parametrize("case", EXCHANGE.history_cases, ids=lambda c: c.id)
def test_history_cases(case):
    fetch = Recorder(case.sample)
    if case.expect.error is not None:
        with pytest.raises(SourceError) as raised:
            lang_schwarz.history(fetch, 9000001, case.isin, case.today)
        assert raised.value.kind == case.expect.error
        return
    points = lang_schwarz.history(fetch, 9000001, case.isin, case.today)
    assert [(p.on, p.nav, p.currency) for p in points] == [
        (p.on, p.price_eur, "EUR") for p in case.expect.points
    ]
    assert fetch.calls[0][0] == lang_schwarz.history_url(9000001)


def test_a_bad_reference_asks_nothing():
    fetch = Recorder("lstc_search.json")
    for isin in ("not an isin", "IE00LSINVNT1&x=1"):
        with pytest.raises(SourceError, match="fund_ref"):
            lang_schwarz.instrument(fetch, isin)
    for instrument_id in (0, -1, True):
        with pytest.raises(SourceError, match="fund_ref"):
            lang_schwarz.history(fetch, instrument_id, "IE00LSINVNT1", FETCHED.date())
    assert fetch.calls == []


@pytest.mark.parametrize("case", EXCHANGE.selection_cases, ids=lambda c: c.id)
def test_selection_cases(case):
    """The rule as fetch.collect applies it: an issuer source asks only the one-off history
    of a latest_only adapter; no issuer source asks daily only when no brand is named."""
    assert {i for i, cls in ADAPTERS.items() if cls.latest_only} == phase6.LATEST_ONLY_ISSUERS
    if case.issuer is not None:
        latest_only = ADAPTERS[case.issuer].latest_only
        asks = "history" if latest_only and not case.exchange_stored else "none"
    else:
        asks = "daily" if lang_schwarz.wants_exchange(case.name) else "none"
    assert asks == case.expect


def _points(rows):
    return [
        PricePoint(
            asset_id=ASSET,
            on=r.on,
            price_eur=r.price_eur,
            source=r.source,
            fetched_at=FETCHED,
        )
        for r in rows
    ]


@pytest.mark.parametrize("case", EXCHANGE.precedence_cases, ids=lambda c: c.id)
def test_precedence_cases(case):
    best = valuation.price_on(_points(case.prices), case.on)
    assert best is not None and best.source == case.expect


@pytest.mark.parametrize("case", EXCHANGE.guard_cases, ids=lambda c: c.id)
def test_guard_cases(case):
    reports = {r.on: r.price_eur for r in case.reports}
    check = valuation.nav_check(case.navs, [], reports)
    if case.expect.checked_on is None:
        assert check is None
    else:
        assert check is not None
        assert (check.on, check.refused) == (case.expect.checked_on, case.expect.refused)


@pytest.mark.parametrize("case", EXCHANGE.report_cases, ids=lambda c: c.id)
def test_report_cases(case):
    assert valuation.REPORT_GAP_TOLERANCE == phase6.REPORT_GAP_TOLERANCE
    gap = valuation.report_gap(_points(case.prices))
    if case.expect is None:
        assert gap is None
        return
    assert gap is not None
    assert (gap.on, gap.source, gap.report_price, gap.price, gap.gap_percent) == (
        case.expect.on,
        case.expect.source,
        case.expect.report_price,
        case.expect.price,
        case.expect.gap_percent,
    )


class Answer:
    """A Fetch answering one built answer (the real layout, invented values)."""

    def __init__(self, data: list[list[float]]) -> None:
        body = {
            "info": {"isin": "IE00LSINVNT1", "plotlines": []},
            "series": {"history": {"id": "history", "data": data}},
        }
        self.body = json.dumps(body).encode()

    def __call__(self, url, headers, timeout, max_bytes, *body):
        return 200, self.body


def _ms(on: date, hour: int = 0) -> int:
    return int(datetime(on.year, on.month, on.day, hour, tzinfo=UTC).timestamp() * 1000)


def test_before_the_venue_opens_the_live_quote_is_yesterdays_and_is_dropped():
    """The daily run is early in the morning: the last point is still Wednesday's evening
    quote beside Wednesday's own day point. Wednesday's day point counts; the quote never."""
    wed, thu = date(2026, 11, 18), date(2026, 11, 19)
    fetch = Answer([[_ms(date(2026, 11, 17)), 8.44], [_ms(wed), 8.4765], [_ms(wed, 21), 8.5]])
    points = lang_schwarz.history(fetch, 9000001, "IE00LSINVNT1", thu)
    assert [(p.on, p.nav) for p in points] == [
        (date(2026, 11, 17), Decimal("8.44")),
        (wed, Decimal("8.4765")),
    ]


def test_a_quote_inside_the_list_is_no_live_quote():
    """Only the last point can be the live quote: one between day points is off midnight, so
    the whole answer is unreadable (D264 (2) amended)."""
    wed = date(2026, 11, 18)
    fetch = Answer([[_ms(wed), 8.4765], [_ms(wed, 21), 8.5], [_ms(date(2026, 11, 19)), 8.6]])
    with pytest.raises(SourceError, match="parse"):
        lang_schwarz.history(fetch, 9000001, "IE00LSINVNT1", date(2026, 11, 20))


def test_a_past_day_point_off_midnight_stores_nothing_of_the_answer():
    """D264 (2) amended, fail closed: a past point at 01:00 UTC, though its day appears once,
    refuses the whole answer, never shifts or keeps the other days."""
    fetch = Answer(
        [
            [_ms(date(2026, 11, 16)), 8.39],
            [_ms(date(2026, 11, 17), 1), 8.44],
            [_ms(date(2026, 11, 18)), 8.47],
        ]
    )
    with pytest.raises(SourceError, match="parse"):
        lang_schwarz.history(fetch, 9000001, "IE00LSINVNT1", date(2026, 11, 19))


def test_an_off_midnight_point_of_today_is_refused_too():
    """The guard runs before today's points are dropped: only the last point is exempt."""
    thu = date(2026, 11, 19)
    fetch = Answer([[_ms(date(2026, 11, 18)), 8.47], [_ms(thu, 2), 8.5], [_ms(thu, 9), 8.51]])
    with pytest.raises(SourceError, match="parse"):
        lang_schwarz.history(fetch, 9000001, "IE00LSINVNT1", thu)


def test_a_stamp_with_a_fraction_of_a_millisecond_is_refused():
    fetch = Answer([[_ms(date(2026, 11, 17)) + 0.5, 8.44], [_ms(date(2026, 11, 18)), 8.47]])
    with pytest.raises(SourceError, match="parse"):
        lang_schwarz.history(fetch, 9000001, "IE00LSINVNT1", date(2026, 11, 19))
