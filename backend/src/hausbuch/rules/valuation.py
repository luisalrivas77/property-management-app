"""Prices and valuations (docs/phases/phase-6.md D167, D174, §9; fixtures
tests/fixtures/phase6/valuation.yaml and issuer_nav.yaml). Built in phase 6 step 5b; step 6
sums the valuations into the net worth.

- `price_on(prices, on)`: the price an asset is valued at on a day, date first: the latest
  day on or before `on` that has a price, and among that day's rows the source highest in
  SOURCE_PRECEDENCE (D167 revised: a trade beats a CoinGecko price on the same day, a NAV
  beats a report value, but yesterday's NAV beats last quarter's trade price), then the
  latest `fetched_at` within one source. Older than STALE_AFTER_DAYS -> stale.
- `to_eur(amount, on, rates)`: a figure in another currency, divided by the ECB rate (units
  of the currency per euro) of the latest ECB day on or before the figure's day (a Saturday
  NAV by Friday's rate), 8 places half up.
- `value(position, price, on)`: units x price, rounded to the cent half up; a valuation day
  after the position's reconciled date is not valued (valuation.not_valued); no price is
  price.no_source; a cash asset (EUR) is valued at 1.0 without a price. A position without a
  reconciled date (an account kind with no reconciliation duty) is valued (step 5b reading).
- `nav_rows(navs, first_movement_on, rates, stored_days)`: the price rows an issuer's NAV
  file adds (D174 (3)): one per NAV day on or after the asset's first movement and not yet
  stored, converted by `to_eur` when the NAV is not in EUR.
- `failed_days(runs)`: how many daily runs in a row, ending at the latest, failed for one
  asset (D174 (4): seven raise price.source_failed); a success or a day without a run ends
  the count.
"""

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from hausbuch.schemas.household import (
    NavPoint,
    Position,
    PricePoint,
    PriceSourceKind,
    Valuation,
)
from hausbuch.schemas.prices import FxRate, NavRow

from . import errors as E

# One day's rows, best first (D167 (2) revised, D174 (3)). ecb_fx prices a currency, never
# a security, so it is last.
SOURCE_PRECEDENCE: tuple[PriceSourceKind, ...] = (
    PriceSourceKind.TRADE,
    PriceSourceKind.ISSUER_NAV,
    PriceSourceKind.STATEMENT,
    PriceSourceKind.KRAKEN_DAILY,
    PriceSourceKind.COINGECKO_DAILY,
    PriceSourceKind.USER,
    PriceSourceKind.ECB_FX,
)
STALE_AFTER_DAYS = 7
SOURCE_FAILED_AFTER_DAYS = 7  # D174: seven failed days raise price.source_failed

if set(SOURCE_PRECEDENCE) != set(PriceSourceKind):  # pragma: no cover
    raise RuntimeError("every price source needs a place in the precedence")

CENT = Decimal("0.01")
PRICE_PLACES = Decimal("0.00000001")  # price.price_eur numeric(20,8)


def price_on(prices: Sequence[PricePoint], on: date) -> PricePoint | None:
    """The row an asset is valued at on `on`, or None when no row is on or before it."""
    eligible = [p for p in prices if p.on <= on]
    if not eligible:
        return None
    day = max(p.on for p in eligible)
    rows = [p for p in eligible if p.on == day]
    # Best source first; within a source the latest fetched_at (a correction appended later).
    return min(rows, key=lambda p: (SOURCE_PRECEDENCE.index(p.source), -p.fetched_at.timestamp()))


def is_stale(price: PricePoint, on: date) -> bool:
    """D167 (2): older than seven days. Exactly seven days old is not stale."""
    return (on - price.on).days > STALE_AFTER_DAYS


def to_eur(amount: Decimal, on: date, rates: Sequence[FxRate]) -> tuple[date, Decimal] | None:
    """(the ECB day used, the EUR figure), or None when no rate is on or before `on`."""
    eligible = [r for r in rates if r.on <= on]
    if not eligible:
        return None
    rate = max(eligible, key=lambda r: r.on)
    return rate.on, (amount / rate.per_eur).quantize(PRICE_PLACES, ROUND_HALF_UP)


def value(
    position: Position, price: PricePoint | None, on: date, *, cash: bool = False
) -> Valuation:
    """The position's value on `on` by `price` (the row price_on chose)."""
    if position.reconciled_through is not None and on > position.reconciled_through:
        return Valuation(
            position=position,
            on=on,
            price=price,
            stale=price is not None and not cash and is_stale(price, on),
            not_valued_reason=E.VALUATION_NOT_VALUED,
        )
    if cash:
        return Valuation(
            position=position, on=on, price=None, value_eur=position.units.quantize(CENT)
        )
    if price is None:
        return Valuation(position=position, on=on, not_valued_reason=E.PRICE_NO_SOURCE)
    return Valuation(
        position=position,
        on=on,
        price=price,
        value_eur=(position.units * price.price_eur).quantize(CENT, ROUND_HALF_UP),
        stale=is_stale(price, on),
    )


def nav_rows(
    navs: Iterable[NavPoint],
    first_movement_on: date,
    rates: Sequence[FxRate],
    stored_days: Iterable[date] = (),
) -> list[NavRow]:
    """D174 (3): the rows to store from one fetched NAV file, oldest first. A NAV in another
    currency without an ECB rate on or before its day is skipped (stored by a later run once
    the rate is there); the native figure is kept for `raw`."""
    stored = set(stored_days)
    rows: dict[date, NavRow] = {}
    for nav in navs:
        if nav.on < first_movement_on or nav.on in stored or nav.on in rows:
            continue
        if nav.currency == "EUR":
            rows[nav.on] = NavRow(on=nav.on, price_eur=nav.nav, nav=nav.nav, currency="EUR")
            continue
        converted = to_eur(nav.nav, nav.on, rates)
        if converted is None:
            continue
        rate_on, price = converted
        if price <= 0:
            continue
        rows[nav.on] = NavRow(
            on=nav.on, price_eur=price, nav=nav.nav, currency=nav.currency, rate_on=rate_on
        )
    return [rows[day] for day in sorted(rows)]


def failed_days(runs: Iterable[tuple[date, bool]]) -> int:
    """`runs`: (day, failed) per daily run for one asset, in any order; several runs on one
    day count as that day's last-known outcome being a failure only when all of them failed.
    The count of consecutive calendar days, ending at the latest run, on which it failed."""
    by_day: dict[date, bool] = {}
    for day, failed in runs:
        by_day[day] = by_day.get(day, True) and failed
    if not by_day:
        return 0
    day = max(by_day)
    count = 0
    while by_day.get(day) is True:
        count += 1
        day -= timedelta(days=1)
    return count


def source_failed(runs: Iterable[tuple[date, bool]]) -> bool:
    return failed_days(runs) >= SOURCE_FAILED_AFTER_DAYS
