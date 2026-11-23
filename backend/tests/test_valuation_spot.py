"""Phase 10 D291 (§7.7): a coin's price read on demand (`spot`) values today only.

The cases follow the spec's sentences: spot is read for today and never for a past day, the
month-end, the lots or the statement (a caller that passes no `today`); the newest spot row
of the day wins; the next day's close replaces it. Invented coin and amounts only."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from hausbuch.rules import valuation
from hausbuch.schemas.household import PricePoint, PriceSourceKind

S = PriceSourceKind
COIN = uuid4()
D0 = date(2026, 11, 22)  # yesterday
D1 = date(2026, 11, 23)  # today
D2 = date(2026, 11, 24)  # tomorrow


def _p(on: date, source: S, eur: str, at: datetime | None = None) -> PricePoint:
    return PricePoint(
        asset_id=COIN,
        on=on,
        price_eur=Decimal(eur),
        source=source,
        fetched_at=at or datetime(on.year, on.month, on.day, 7, tzinfo=UTC),
    )


YESTERDAY_CLOSE = _p(D0, S.KRAKEN_DAILY, "100")
GECKO_TODAY = _p(D1, S.COINGECKO_DAILY, "101", datetime(2026, 11, 23, 7, tzinfo=UTC))
SPOT_MORNING = _p(D1, S.SPOT, "105", datetime(2026, 11, 23, 9, tzinfo=UTC))
SPOT_NOON = _p(D1, S.SPOT, "110", datetime(2026, 11, 23, 12, tzinfo=UTC))


def test_spot_is_in_the_precedence_and_never_beats_a_typed_price_or_a_trade():
    assert set(valuation.SOURCE_PRECEDENCE) == set(S)
    assert set(valuation.TODAY_PRECEDENCE) == set(S)
    order = valuation.TODAY_PRECEDENCE
    assert order.index(S.USER) < order.index(S.SPOT)
    assert order.index(S.TRADE) < order.index(S.SPOT)
    assert order.index(S.SPOT) < order.index(S.COINGECKO_DAILY)
    assert order.index(S.SPOT) < order.index(S.KRAKEN_DAILY)
    # On any other day spot ranks below every daily source.
    daily = (S.ISSUER_NAV, S.EXCHANGE_CLOSE, S.KRAKEN_DAILY, S.COINGECKO_DAILY)
    for source in daily:
        assert valuation.SOURCE_PRECEDENCE.index(source) < (
            valuation.SOURCE_PRECEDENCE.index(S.SPOT)
        )


def test_today_the_newest_spot_wins_over_the_days_own_daily_row():
    rows = [YESTERDAY_CLOSE, GECKO_TODAY, SPOT_MORNING, SPOT_NOON]
    best = valuation.price_on(rows, D1, today=D1)
    assert best == SPOT_NOON


def test_a_typed_price_of_today_still_wins_over_spot():
    typed = _p(D1, S.USER, "99", datetime(2026, 11, 23, 8, tzinfo=UTC))
    best = valuation.price_on([SPOT_NOON, typed], D1, today=D1)
    assert best == typed


def test_without_today_spot_is_never_read():
    # The month-end, the key figures, the lots and the statement pass no `today`.
    rows = [YESTERDAY_CLOSE, GECKO_TODAY, SPOT_NOON]
    assert valuation.price_on(rows, D1) == GECKO_TODAY
    assert valuation.price_on([YESTERDAY_CLOSE, SPOT_NOON], D1) == YESTERDAY_CLOSE
    assert valuation.price_on([SPOT_NOON], D1) is None


def test_a_past_day_ignores_spot_even_on_a_live_read():
    rows = [YESTERDAY_CLOSE, _p(D0, S.SPOT, "120", datetime(2026, 11, 22, 15, tzinfo=UTC))]
    assert valuation.price_on(rows, D0, today=D1) == YESTERDAY_CLOSE


def test_the_next_day_yesterdays_spot_never_hides_the_close_before_it():
    # Tomorrow, with only today's spot rows and yesterday's close stored: yesterday's close.
    rows = [YESTERDAY_CLOSE, SPOT_MORNING, SPOT_NOON]
    assert valuation.price_on(rows, D2, today=D2) == YESTERDAY_CLOSE


def test_tomorrows_close_replaces_spot():
    close = _p(D1, S.KRAKEN_DAILY, "108", datetime(2026, 11, 24, 7, tzinfo=UTC))
    rows = [YESTERDAY_CLOSE, SPOT_MORNING, SPOT_NOON, close]
    # Valued on the day itself afterwards (a month-end, a past view): the close, never spot.
    assert valuation.price_on(rows, D1) == close
    assert valuation.price_on(rows, D1, today=D2) == close
    assert valuation.price_on(rows, D2, today=D2) == close


def test_a_twins_spot_row_follows_the_same_rule():
    twin = PricePoint(
        asset_id=uuid4(),
        on=D1,
        price_eur=Decimal("111"),
        source=S.SPOT,
        fetched_at=datetime(2026, 11, 23, 13, tzinfo=UTC),
    )
    assert valuation.price_on([YESTERDAY_CLOSE], D1, priced_as=[twin], today=D1) == twin
    assert valuation.price_on([YESTERDAY_CLOSE], D1, priced_as=[twin]) == YESTERDAY_CLOSE
