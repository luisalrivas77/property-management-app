"""Prices and valuations (docs/phases/phase-6.md D167, D174, §9; fixtures
tests/fixtures/phase6/valuation.yaml and issuer_nav.yaml). Built in phase 6 step 5b; step 6
sums the valuations into the net worth.

- `price_on(prices, on)`: the price an asset is valued at on a day, date first: the latest
  day on or before `on` that has a price, and among that day's rows the source highest in
  SOURCE_PRECEDENCE (D167 revised: a trade beats a CoinGecko price on the same day, a NAV
  beats a report value, but yesterday's NAV beats last quarter's trade price; D167 amended
  13 Nov 2026: a typed user price wins on its own day, the one way to correct a fetched
  price, which is unique per day in an append-only table), then the latest `fetched_at`
  within one source. Older than STALE_AFTER_DAYS -> stale. A chain asset (phase 7 D180 (3),
  §10) passes its `priced_as` asset's rows too: both read together, date first, then
  SOURCE_PRECEDENCE, and on one day and source the chain asset's own row before the symbol's,
  so a price typed on the chain asset wins its day and the symbol's newer rows value the days
  after it (step 0's reading, §15.1 item 18). Without `priced_as` rows nothing changes.
  Phase 10 D291 (§7.7): a `spot` row (a current price read on demand) is read only when the
  caller passes `today` and values that very day (`on == today`, the Berlin day); every other
  reader, and every past day, never sees one, so the month-end, the key figures, the lots,
  „steuerfrei ab“ and the 31 Dec statement keep the daily closes. On today `spot` ranks after
  a typed price and a trade but before the daily feeds (TODAY_PRECEDENCE): CoinGecko's daily
  row of today is its 00:00 UTC snapshot, older than any price read since; the newest spot row
  wins (`fetched_at` is the moment it was read). Tomorrow the day's close replaces it.
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
- `nav_check(navs, rates, reports)`: the first-read check of a new NAV source (D174 amended
  19 Nov 2026 (g)): the newest NAV day the depot report also priced, in euros, against the
  report's price; more than NAV_CHECK_TOLERANCE apart refuses the file.
- `failed_days(runs)`: how many daily runs in a row, ending at the latest, failed for one
  asset (D174 (4): seven raise price.source_failed); a success or a day without a run ends
  the count.
- `report_gap(prices)`: D264 (7): on the newest day a depot report priced a fund, its
  issuer_nav row, else its exchange_close row, more than REPORT_GAP_TOLERANCE from the
  report's price; None when a person typed a price for that day. exchange_close (D264) is a
  fund's daily exchange price, below a NAV and a report's own day in SOURCE_PRECEDENCE.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
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

# One day's rows, best first (D167 (2) revised, D174 (3)). A person's typed price first
# (D167 amended 13 Nov 2026: it corrects that day's fetched price); ecb_fx prices a
# currency, never a security, so it is last.
SOURCE_PRECEDENCE: tuple[PriceSourceKind, ...] = (
    PriceSourceKind.USER,
    PriceSourceKind.TRADE,
    PriceSourceKind.ISSUER_NAV,
    PriceSourceKind.STATEMENT,
    PriceSourceKind.EXCHANGE_CLOSE,
    PriceSourceKind.KRAKEN_DAILY,
    PriceSourceKind.COINGECKO_DAILY,
    # Phase 10 D291: a current price read on demand, the lowest for a coin; read for today
    # only, and there by TODAY_PRECEDENCE (see price_on).
    PriceSourceKind.SPOT,
    PriceSourceKind.ECB_FX,
)
# Phase 10 D291: on today a price read in the last minutes beats the daily feeds' rows of the
# same day (CoinGecko's is its 00:00 UTC snapshot), never a typed price or a trade's own.
TODAY_PRECEDENCE: tuple[PriceSourceKind, ...] = (
    PriceSourceKind.USER,
    PriceSourceKind.TRADE,
    PriceSourceKind.SPOT,
    *(
        s
        for s in SOURCE_PRECEDENCE
        if s not in (PriceSourceKind.USER, PriceSourceKind.TRADE, PriceSourceKind.SPOT)
    ),
)
STALE_AFTER_DAYS = 7
# D174 amended 19 Nov 2026 (g): a first read whose NAV, in euros, differs from the depot
# report's price of the same day by more than this share of the report's price stores nothing.
NAV_CHECK_TOLERANCE = Decimal("0.02")
SOURCE_FAILED_AFTER_DAYS = 7  # D174: seven failed days raise price.source_failed

if set(SOURCE_PRECEDENCE) != set(PriceSourceKind):  # pragma: no cover
    raise RuntimeError("every price source needs a place in the precedence")

CENT = Decimal("0.01")
PRICE_PLACES = Decimal("0.00000001")  # price.price_eur numeric(20,8)


def reads_spot(on: date, today: date | None) -> bool:
    """Phase 10 D291: whether a valuation of `on` may read a `spot` row: only a caller that
    passes `today` and values that very day."""
    return today is not None and on == today


def eligible(p: PricePoint, on: date, today: date | None = None) -> bool:
    """Whether `p` may value the day `on` at all (on or before it; a `spot` row only for
    today, D291)."""
    if p.on > on:
        return False
    if p.source is PriceSourceKind.SPOT:
        return reads_spot(on, today) and p.on == today
    return True


def price_on(
    prices: Sequence[PricePoint],
    on: date,
    *,
    priced_as: Sequence[PricePoint] = (),
    today: date | None = None,
) -> PricePoint | None:
    """The row an asset is valued at on `on`, or None when no row is on or before it.
    `priced_as`: a chain asset's symbol-level rows (D180 (3)); the row returned keeps its own
    asset_id, so the caller can tell which asset priced it. `today` (phase 10 D291): only a
    live read passes it; a `spot` row is read when `on == today` and the row is of today,
    never otherwise (None: never)."""
    rows_in = [(p, False) for p in prices if eligible(p, on, today)]
    rows_in += [(p, True) for p in priced_as if eligible(p, on, today)]
    if not rows_in:
        return None
    day = max(p.on for p, _ in rows_in)
    rows = [(p, twin) for p, twin in rows_in if p.on == day]
    order = TODAY_PRECEDENCE if reads_spot(on, today) else SOURCE_PRECEDENCE
    # Best source first; the asset's own row before its symbol's; within a source the latest
    # fetched_at (a correction appended later; for spot, the newest price read).
    best, _ = min(
        rows,
        key=lambda pt: (
            order.index(pt[0].source),
            pt[1],
            -pt[0].fetched_at.timestamp(),
        ),
    )
    return best


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
            position=position,
            on=on,
            price=None,
            value_eur=position.units.quantize(CENT, ROUND_HALF_UP),
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


@dataclass(frozen=True)
class NavCheck:
    on: date  # the day compared
    refused: bool  # the NAV in euros is more than NAV_CHECK_TOLERANCE from the report


def nav_check(
    navs: Iterable[NavPoint], rates: Sequence[FxRate], reports: Mapping[date, Decimal]
) -> NavCheck | None:
    """D174 amended 19 Nov 2026 (g): the first read of a fund from a new source is compared
    with the depot report's price on the newest NAV day that the report also priced and,
    for a NAV not in EUR, that has an ECB rate on or before it. `rates` are the NAV
    currency's, `reports` the report price in EUR per day. None when no day can be compared
    (a latest-NAV-only source before the next report): the rows are stored."""
    for point in sorted(navs, key=lambda n: n.on, reverse=True):
        report = reports.get(point.on)
        if report is None or report <= 0:
            continue
        if point.currency == "EUR":
            eur = point.nav
        else:
            converted = to_eur(point.nav, point.on, rates)
            if converted is None:
                continue
            eur = converted[1]
        gap = abs(eur - report) / report
        return NavCheck(on=point.on, refused=gap > NAV_CHECK_TOLERANCE)
    return None


# D264 (7): a stored daily price this share or less from the report's price raises no card.
REPORT_GAP_TOLERANCE = NAV_CHECK_TOLERANCE


@dataclass(frozen=True)
class ReportGap:
    on: date  # the newest day a depot report priced the fund
    source: PriceSourceKind  # issuer_nav or exchange_close
    report_price: Decimal
    price: Decimal
    gap_percent: Decimal  # one place, half up


def _latest(rows: Sequence[PricePoint]) -> PricePoint:
    return max(rows, key=lambda p: p.fetched_at)


def report_gap(prices: Sequence[PricePoint]) -> ReportGap | None:
    """D264 (7): one fund's rows. The newest `statement` day is compared with that day's
    issuer_nav row (the latest fetched), else its exchange_close row; a gap of more than
    REPORT_GAP_TOLERANCE of the report's price is returned. A `user` row of that day ends
    it, and only the newest report day is ever compared."""
    reports = [p for p in prices if p.source is PriceSourceKind.STATEMENT]
    if not reports:
        return None
    on = max(p.on for p in reports)
    day = [p for p in prices if p.on == on]
    if any(p.source is PriceSourceKind.USER for p in day):
        return None
    report = _latest([p for p in day if p.source is PriceSourceKind.STATEMENT]).price_eur
    if report <= 0:
        return None
    for source in (PriceSourceKind.ISSUER_NAV, PriceSourceKind.EXCHANGE_CLOSE):
        rows = [p for p in day if p.source is source]
        if not rows:
            continue
        price = _latest(rows).price_eur
        gap = abs(price - report) / report
        if gap <= REPORT_GAP_TOLERANCE:
            return None
        return ReportGap(
            on=on,
            source=source,
            report_price=report,
            price=price,
            gap_percent=(gap * 100).quantize(Decimal("0.1"), ROUND_HALF_UP),
        )
    return None


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
