"""Route 3's reference series and its count (docs/phases/phase-10.md §7.11 (c) 3, „Route 3 as
decided for the build“; D303, D310 amended). Pure functions, exact fractions; nothing here
reads the database, the network or a model, and nothing proposes a rate or a product.

- **Euro returns** from what the sources publish: U_Y, US dollars per euro at the end of year
  Y, is the ECB's year-end reference rate from 1999 and before it 1.95583 ÷ the Bundesbank's
  year-end DM per US dollar (the fixed DM per euro, „eigene Berechnung“). The funds (Kenneth
  French's Developed market, annual, USD, percent): (1 + (Mkt−RF + RF) ÷ 100) × U_{Y−1} ÷ U_Y
  − 1. A coin (Coin Metrics' PriceUSD on 31 Dec): (P_Y ÷ U_Y) ÷ (P_{Y−1} ÷ U_{Y−1}) − 1. A US
  dollar stablecoin U_{Y−1} ÷ U_Y − 1; euro (a euro stablecoin, an exchange's euro cash) 0. A
  year without both rates, or a coin without both prices, has no return.
- **The mix** (1, 2): today's values weigh funds against each coin (with nothing held, the
  saved split; crypto in a split with no coin held is left out as `no_coin`, no coin is
  assumed). A part without a series (a coin) or with fewer yearly returns than the window is
  left out and named with its share, its weight going to the rest pro rata; under half of the
  value covered, or no window in the years every series left has, gives no count. Each
  year's mix return is Σ weight × return over those years (rebalanced to today's weights
  every year).
- **The windows** (3): every run of `years` consecutive years in the mix; a window's product
  Π (1 + return); the median is the sorted products' element M ÷ 2 (the upper middle of an
  even count, so at least half the windows reached it), the best the largest; each shown
  annualised, floored to 0.1 % (exact: (1 + shown)^years ≤ product). Under 5 windows is thin.
- **The verdict** of a needed return n: (1 + n)^years against the products, exactly: at or
  below the median `within_history`, at or below the best `good_times`, else `never`; N of M
  counts the products at or above it.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

from hausbuch.schemas.goal import (
    GoalLeftOut,
    GoalLeftOutKind,
    GoalLeftOutReason,
    GoalRouteReason,
    GoalVerdict,
)
from hausbuch.schemas.plan import InvestmentSplit

ZERO = Decimal("0.00")
DEM_PER_EUR = Decimal("1.95583")  # the fixed conversion rate of 31 Dec 1998
EURO_FROM = 1999  # the ECB's reference rates; the Bundesbank's DM rates before
THIN_BELOW = 5  # a count on fewer windows never decides the word
COVERED_MIN = Fraction(1, 2)
FUNDS = "funds"  # the funds series' key
USD_PEGGED = frozenset({"USDT", "USDC"})
EUR_PEGGED = frozenset({"EUR", "EURC", "EURT"})
SHARE_PLACES = Decimal("0.1")
RETURN_PLACES = Decimal("0.001")


# --- Euro returns ----------------------------------------------------------------------------


def usd_per_eur(
    ecb: Mapping[int, Decimal], dem_per_usd: Mapping[int, Decimal] | None = None
) -> dict[int, Fraction]:
    """U_Y per year: the ECB's from 1999, before it 1.95583 ÷ the Bundesbank's DM per USD."""
    out = {
        y: Fraction(DEM_PER_EUR) / Fraction(v)
        for y, v in (dem_per_usd or {}).items()
        if y < EURO_FROM and v > 0
    }
    out |= {y: Fraction(v) for y, v in ecb.items() if y >= EURO_FROM and v > 0}
    return dict(sorted(out.items()))


def funds_returns(
    rows: Mapping[int, tuple[Decimal, Decimal]], fx: Mapping[int, Fraction]
) -> dict[int, Fraction]:
    """French's annual (Mkt−RF, RF) in percent, in USD, as yearly returns in euros."""
    out = {}
    for y, (mkt_rf, rf) in sorted(rows.items()):
        if y - 1 in fx and y in fx:
            usd = 1 + Fraction(mkt_rf + rf) / 100
            out[y] = usd * fx[y - 1] / fx[y] - 1
    return out


def coin_returns(prices: Mapping[int, Decimal], fx: Mapping[int, Fraction]) -> dict[int, Fraction]:
    """A coin's 31 December prices in USD as yearly returns in euros."""
    out = {}
    for y, price in sorted(prices.items()):
        before = prices.get(y - 1)
        if before is None or before <= 0 or price <= 0 or y - 1 not in fx or y not in fx:
            continue
        out[y] = (Fraction(price) / fx[y]) / (Fraction(before) / fx[y - 1]) - 1
    return out


def pegged_returns(currency: str, fx: Mapping[int, Fraction]) -> dict[int, Fraction]:
    """A stablecoin's yearly return in euros: the US dollar's against the euro, or 0."""
    out = {}
    for y in sorted(fx):
        if y - 1 in fx:
            out[y] = fx[y - 1] / fx[y] - 1 if currency == "USD" else Fraction(0)
    return out


# --- The mix ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """One reference series in euros: yearly returns by year, and what the answer names."""

    key: str  # "funds", or a coin's symbol
    returns: Mapping[int, Fraction]
    source: str
    url: str
    licence: str
    attribution: str
    read_on: date | None = None


@dataclass(frozen=True)
class History:
    """The series route 3 may use: the funds series (None until downloaded) and one per coin
    symbol (a USD or euro stablecoin included)."""

    funds: Series | None = None
    coins: Mapping[str, Series] = field(default_factory=dict)


@dataclass(frozen=True)
class Invested:
    """Today's invested values: the depot class without the depots ticked into the reserve
    (D304), and the crypto class per coin symbol (a token priced as a coin under the coin's)."""

    funds: Decimal = ZERO
    coins: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return max(ZERO, self.funds) + sum((v for v in self.coins.values() if v > 0), ZERO)


@dataclass(frozen=True)
class Mix:
    series: tuple[Series, ...]  # funds first, then coins by symbol
    weights: tuple[Fraction, ...]  # adding up to 1
    returns: dict[int, Fraction]  # Σ weight × return over the years every series has
    left_out: tuple[GoalLeftOut, ...]


@dataclass(frozen=True)
class NoCount:
    reason: GoalRouteReason
    left_out: tuple[GoalLeftOut, ...] = ()


def _share(value: Fraction) -> Decimal:
    return Decimal(value.numerator / Decimal(value.denominator)).quantize(
        SHARE_PLACES, ROUND_HALF_UP
    )


def mix_of(
    invested: Invested, split: InvestmentSplit | None, history: History, years: int
) -> Mix | NoCount:
    """The invested mix for a window of `years` (1, 2), or why there is no count."""
    # (kind, symbol, value): today's values, else the saved split's shares
    parts: list[tuple[GoalLeftOutKind, str | None, Fraction]] = []
    if invested.total > 0:
        if invested.funds > 0:
            parts.append((GoalLeftOutKind.FUNDS, None, Fraction(invested.funds)))
        parts += [
            (GoalLeftOutKind.COIN, s, Fraction(v))
            for s, v in sorted(invested.coins.items())
            if v > 0
        ]
    elif split is not None:
        if split.depot > 0:
            parts.append((GoalLeftOutKind.FUNDS, None, Fraction(split.depot)))
        if split.crypto > 0:  # no coin held: no coin is assumed
            parts.append((GoalLeftOutKind.COIN, None, Fraction(split.crypto)))
    if not parts:
        return NoCount(GoalRouteReason.NOTHING_INVESTED)
    total = sum(v for _, _, v in parts)
    kept: list[tuple[Series, Fraction]] = []
    left_out = []
    for kind, symbol, value in parts:
        if kind is GoalLeftOutKind.FUNDS:
            if history.funds is None:  # the screen says why; nothing stands in for it
                return NoCount(GoalRouteReason.SERIES_MISSING)
            series, reason = history.funds, None
        elif symbol is None:
            series, reason = None, GoalLeftOutReason.NO_COIN
        else:
            series = history.coins.get(symbol)
            reason = GoalLeftOutReason.NO_SERIES if series is None else None
        if series is not None and len(series.returns) < years:
            reason = GoalLeftOutReason.TOO_SHORT
        if reason is None:
            assert series is not None
            kept.append((series, value))
        else:
            left_out.append(
                GoalLeftOut(
                    kind=kind, symbol=symbol, share=_share(value * 100 / total), reason=reason
                )
            )
    named = tuple(left_out)
    covered = sum((v for _, v in kept), Fraction(0))
    if covered / total < COVERED_MIN:
        return NoCount(GoalRouteReason.HISTORY_SHORT, named)
    weights = tuple(v / covered for _, v in kept)
    common = set.intersection(*(set(s.returns) for s, _ in kept))
    returns = {
        y: sum(w * s.returns[y] for (s, _), w in zip(kept, weights, strict=True))
        for y in sorted(common)
    }
    if not window_products(returns, years):
        return NoCount(GoalRouteReason.HISTORY_SHORT, named)
    return Mix(series=tuple(s for s, _ in kept), weights=weights, returns=returns, left_out=named)


# --- The windows and the verdict --------------------------------------------------------------


def window_years(months_left: int) -> int:
    """The goal's months left in whole years, at least 1 (3)."""
    return max(1, months_left // 12)


def window_products(returns: Mapping[int, Fraction], years: int) -> list[Fraction]:
    """Π (1 + return) over every run of `years` consecutive years, by first year."""
    ys = sorted(returns)
    out = []
    for i in range(len(ys) - years + 1):
        run = ys[i : i + years]
        if run[-1] - run[0] == years - 1:
            out.append(math.prod((1 + returns[y] for y in run), start=Fraction(1)))
    return out


def median_of(products: Sequence[Fraction]) -> Fraction:
    ordered = sorted(products)
    return ordered[len(ordered) // 2]


def annualised(product: Fraction, years: int) -> Decimal:
    """product^(1/years) − 1, floored to 0.1 % exactly: (1 + shown)^years ≤ product."""
    if product <= 0:
        return Decimal("-1.000")
    with localcontext() as c:
        c.prec = 40
        d = Decimal(product.numerator) / Decimal(product.denominator)
        approx = (d.ln() / years).exp() - 1
        k = int((approx * 1000).to_integral_value(ROUND_FLOOR))
    while (1 + Fraction(k + 1, 1000)) ** years <= product:
        k += 1
    while k > -1000 and (1 + Fraction(k, 1000)) ** years > product:
        k -= 1
    return (Decimal(k) / 1000).quantize(RETURN_PLACES)


def verdict_of(needed: Decimal, products: Sequence[Fraction], years: int) -> GoalVerdict:
    wanted = (1 + Fraction(needed)) ** years
    if wanted <= median_of(products):
        return GoalVerdict.WITHIN_HISTORY
    if wanted <= max(products):
        return GoalVerdict.GOOD_TIMES
    return GoalVerdict.NEVER


def reached(needed: Decimal, products: Sequence[Fraction], years: int) -> int:
    wanted = (1 + Fraction(needed)) ** years
    return sum(1 for p in products if p >= wanted)


__all__ = [
    "FUNDS",
    "THIN_BELOW",
    "USD_PEGGED",
    "EUR_PEGGED",
    "History",
    "Invested",
    "Mix",
    "NoCount",
    "Series",
    "annualised",
    "coin_returns",
    "funds_returns",
    "median_of",
    "mix_of",
    "pegged_returns",
    "reached",
    "usd_per_eur",
    "verdict_of",
    "window_products",
    "window_years",
]
