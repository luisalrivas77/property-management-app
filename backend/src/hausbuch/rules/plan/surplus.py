"""Free cash and „Anlegbar pro Monat“ (docs/phases/phase-9.md §6.1, D244). Fixture:
tests/fixtures/phase9/surplus.yaml.

Over the window's closed months: the mean private income, the mean regular private spending and
the mean cash of the flat after interest and repayment (C, signed), each to the cent half-up;
free cash = their sum; the buffer = free cash × buffer_percent ÷ 100 to the cent, 0 when free
cash is not positive; investable = max(0, free cash − buffer). Beside the mean, the median and
the lowest month's free cash F (= income − regular spending + C); the one-offs are left out of
the means and named as their sum. Each row carries the allocations behind it (principle 2).

Step 0's readings (the fixture's header): the median of an even count is the mean of the middle
two, half-up to the cent; of two months with the same lowest F, the earlier one.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from hausbuch.schemas.health import HouseholdMonth
from hausbuch.schemas.plan import Surplus, SurplusKey, SurplusRow

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def mean_cent(values: Sequence[Decimal]) -> Decimal:
    return cent(sum(values, ZERO) / len(values))


def buffer_of(free: Decimal, buffer_percent: Decimal) -> Decimal:
    """The part of a positive free cash kept back, to the cent half-up; 0 otherwise."""
    return cent(free * buffer_percent / 100) if free > 0 else ZERO


def _sources(months: Sequence[HouseholdMonth], *fields: str) -> list[UUID]:
    seen: dict[UUID, None] = {}
    for m in months:
        for name in fields:
            for source in m.sources.get(name, []):
                seen.setdefault(source)
    return list(seen)


def surplus(window_months: Sequence[HouseholdMonth], buffer_percent: Decimal) -> Surplus:
    """The rows of „So entsteht die Zahl“ over the window's months (at least one)."""
    if not window_months:
        raise ValueError("the surplus is taken over at least one closed month")
    k = SurplusKey
    income = mean_cent([m.private_income for m in window_months])
    regular = mean_cent([m.regular_spending for m in window_months])
    flat = mean_cent([m.rental_cash for m in window_months])
    free = income - regular + flat
    buffer = buffer_of(free, buffer_percent)
    per_month = [(m.free_cash, m.month) for m in window_months]
    ordered = sorted(f for f, _ in per_month)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[middle]
    else:
        median = cent((ordered[middle - 1] + ordered[middle]) / 2)
    lowest = min(per_month, key=lambda fm: (fm[0], fm[1]))
    lowest_month = next(m for m in window_months if m.month == lowest[1])
    all_free = _sources(window_months, "free_cash")
    return Surplus(
        rows=[
            SurplusRow(
                key=k.MEAN_INCOME,
                amount=income,
                sources=_sources(window_months, "private_income"),
            ),
            SurplusRow(
                key=k.MEAN_REGULAR_SPENDING,
                amount=regular,
                sources=_sources(window_months, "regular_spending"),
            ),
            SurplusRow(
                key=k.MEAN_RENTAL_CASH,
                amount=flat,
                sources=_sources(window_months, "rental_cash"),
            ),
            SurplusRow(key=k.FREE_CASH, amount=free, sources=all_free),
            SurplusRow(key=k.BUFFER, amount=buffer),
            SurplusRow(key=k.INVESTABLE, amount=max(ZERO, free - buffer), sources=all_free),
            SurplusRow(key=k.MEDIAN_FREE_CASH, amount=median, sources=all_free),
            SurplusRow(
                key=k.LOWEST_FREE_CASH,
                amount=lowest[0],
                month=lowest[1],
                sources=_sources([lowest_month], "free_cash"),
            ),
            SurplusRow(
                key=k.ONE_OFF_SPENDING_LEFT_OUT,
                amount=sum((m.one_off_spending for m in window_months), ZERO),
                sources=_sources(window_months, "one_off_spending"),
            ),
        ]
    )
