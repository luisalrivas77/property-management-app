"""The month function under the scenario's levers (docs/phases/phase-9.md §7.1, §7.3, D247,
D248). Fixture: tests/fixtures/phase9/plan_levers.yaml.

For the k-th month after the newest closed one:

1. flat(k) = the window's mean C while the month is in the newest closed month's calendar year;
   otherwise Σ over the properties of (net_cash + tax effect) ÷ 12, each to the cent, of that
   projected year of each property's default scenario (phase 4), for the years every property
   projects; a year not every property projects keeps the measured mean (the reading of
   fixture_models_phase9.flat_by_year: the window gives one household mean, not one per
   property, so a year is replaced only as a whole). A year whose tax effect is None takes its
   net_cash as it is.
2. free(k) = mean income − mean regular spending + flat(k) + the income changes active in k + the
   one-offs dated in k. A one-off in the levers counts: a proposed one (phase 4b's refund or
   back-payment) is in the levers only once a member accepted it (D247).
3. investable(k) = free(k) − free(k) × buffer ÷ 100 (to the cent, half-up) when free(k) > 0,
   else free(k).

Pure: no I/O, no tax computed (the flat's tax effect is phase 4's, read as it is).
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from hausbuch.schemas.plan import IncomeChange, OneOff, PlanMonth

from .surplus import ZERO, buffer_of, cent


@dataclass(frozen=True)
class MonthBasis:
    """The window's measured means (§6.1) and the buffer the month function starts from."""

    newest_closed: str  # YYYY-MM
    mean_income: Decimal
    mean_regular_spending: Decimal
    mean_rental_cash: Decimal
    buffer_percent: Decimal


@dataclass(frozen=True)
class FlatYear:
    """One projected year of a property's default scenario (phase 4's YearFigures)."""

    net_cash: Decimal
    tax_effect: Decimal | None  # YearFigures.tax_effect.effect, None when phase 4 has none
    value: Decimal | None = None
    loan_balance: Decimal | None = None


def add_months(month: str, k: int) -> str:
    index = int(month[:4]) * 12 + int(month[5:]) - 1 + k
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months_after(first: str, last: str) -> int:
    """How many months `last` lies after `first` (YYYY-MM each)."""
    return (int(last[:4]) - int(first[:4])) * 12 + int(last[5:]) - int(first[5:])


def flat_by_year(flats: Mapping[Hashable, Mapping[int, FlatYear]]) -> dict[int, Decimal]:
    """Σ over the properties of cent((net_cash + tax effect) ÷ 12) for each year every
    property's scenario projects; {} without a property."""
    if not flats:
        return {}
    years = set.intersection(*(set(by_year) for by_year in flats.values()))
    return {
        year: sum(
            (cent((f[year].net_cash + (f[year].tax_effect or ZERO)) / 12) for f in flats.values()),
            ZERO,
        )
        for year in sorted(years)
    }


def month_at(
    basis: MonthBasis,
    k: int,
    flat_years: Mapping[int, Decimal] | None,
    income_changes: Sequence[IncomeChange] = (),
    one_offs: Sequence[OneOff] = (),
) -> PlanMonth:
    """§7.1 for the k-th month after the newest closed month (k ≥ 1)."""
    ym = add_months(basis.newest_closed, k)
    year = int(ym[:4])
    flat = basis.mean_rental_cash
    if flat_years and year != int(basis.newest_closed[:4]) and year in flat_years:
        flat = flat_years[year]
    free = basis.mean_income - basis.mean_regular_spending + flat
    for change in income_changes:
        if change.from_month <= ym and (change.to_month is None or ym <= change.to_month):
            free += change.monthly_net
    for one_off in one_offs:
        if one_off.month == ym:
            free += one_off.amount
    investable = free - buffer_of(free, basis.buffer_percent) if free > 0 else free
    return PlanMonth(month=ym, flat=flat, free=free, investable=investable)


def months(
    basis: MonthBasis,
    horizon_months: int,
    flat_years: Mapping[int, Decimal] | None,
    income_changes: Sequence[IncomeChange] = (),
    one_offs: Sequence[OneOff] = (),
) -> list[PlanMonth]:
    """flat(k), free(k) and investable(k) for each month k = 1 … horizon_months."""
    return [
        month_at(basis, k, flat_years, income_changes, one_offs)
        for k in range(1, horizon_months + 1)
    ]


def one_offs_outside(
    one_offs: Sequence[OneOff], newest_closed: str, horizon_months: int
) -> list[OneOff]:
    """§7.3: a one-off must lie inside the horizon, the months 1 … horizon after the newest
    closed month; those that do not (the scenario was saved months ago) are returned."""
    last = add_months(newest_closed, horizon_months)
    return [o for o in one_offs if not (newest_closed < o.month <= last)]
