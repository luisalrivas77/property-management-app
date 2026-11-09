"""Returns on the equity: CAGR and XIRR (docs/phases/phase-4.md §5.5, D89). Pure, Decimal.

- cagr(E₀, profit, years) = ((E₀ + profit) ÷ E₀)^(1 ÷ years) − 1, with years = the days from
  the purchase date to the sale ÷ 365.25 (`years_between`). None when E₀ ≤ 0 (nothing was put
  in) or E₀ + profit ≤ 0 (nothing is left, so no rate exists).
- xirr(flows): the rate r with Σ amount ÷ (1 + r)^(days since the first flow ÷ 365) = 0
  (Actual/365), found by bisection. None when the flows do not change sign (no rate exists).
  D89 asks for 1e-6; the bisection runs to 1e-9 so the six-decimal result is stable in its
  last place (the fixtures were generated that way; tests allow ±0.000001).

Both results are rounded half-up to six decimals (§10.4). Fractional powers are taken as
exp(t × ln x) on a positive base, in a fixed 40-digit context, so the result does not depend
on the caller's decimal context.
"""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

ZERO = Decimal("0")
ONE = Decimal("1")
SIX = Decimal("0.000001")
DAYS_PER_YEAR_CAGR = Decimal("365.25")
DAYS_PER_YEAR_IRR = Decimal("365")
_CONTEXT = Context(prec=40)
_TOLERANCE = Decimal("1e-9")
_LOW = Decimal("-0.999999")  # a rate of −100 % makes every later flow infinite
_HIGH_LIMIT = Decimal("1000")  # +100,000 % a year: beyond it, no rate is reported


def _round6(value: Decimal) -> Decimal:
    return value.quantize(SIX, rounding=ROUND_HALF_UP) + 0


def years_between(start: date, end: date) -> Decimal:
    """D89: the CAGR's years, days ÷ 365.25."""
    return Decimal((end - start).days) / DAYS_PER_YEAR_CAGR


def cagr(initial_equity: Decimal, profit: Decimal, years: Decimal) -> Decimal | None:
    if initial_equity <= 0 or years <= 0:
        return None
    end = initial_equity + profit
    if end <= 0:
        return None
    with localcontext(_CONTEXT):
        growth = ((end / initial_equity).ln() / years).exp()
        return _round6(growth - ONE)


def _npv(rate: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    """Σ amount × (1 + rate)^(−t), t in years of 365 days; 1 + rate > 0."""
    log_base = (ONE + rate).ln()
    return sum(((amount * (-t * log_base).exp()) for t, amount in flows), ZERO)


def xirr(flows: Sequence[tuple[date, Decimal]]) -> Decimal | None:
    """The annual rate of dated flows (Actual/365 from the first flow); None without a sign
    change or without a rate between −99.9999 % and +100,000 %."""
    if not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    start = min(d for d, _ in flows)
    with localcontext(_CONTEXT):
        timed = [(Decimal((d - start).days) / DAYS_PER_YEAR_IRR, a) for d, a in flows]
        low, high = _LOW, ONE
        f_low = _npv(low, timed)
        f_high = _npv(high, timed)
        while (f_low > 0) == (f_high > 0) and f_high != 0:
            if high >= _HIGH_LIMIT:
                return None
            low, f_low = high, f_high
            high = high * 2
            f_high = _npv(high, timed)
        if f_high == 0:
            return _round6(high)
        while high - low > _TOLERANCE:
            mid = (low + high) / 2
            f_mid = _npv(mid, timed)
            if f_mid == 0:
                return _round6(mid)
            if (f_mid > 0) == (f_low > 0):
                low, f_low = mid, f_mid
            else:
                high = mid
        return _round6((low + high) / 2)
