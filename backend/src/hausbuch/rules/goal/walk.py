"""Phase 9's net-worth projection month by month (docs/phases/phase-10.md §7.9 (d) 2).

Phase 9 shows the net worth at each 31 December (rules/plan/projection.py, §7.5); a goal is due
in any month. This walk repeats phase 9's steps for every month with phase 9's own functions
and changes none of them: the bank row and the reserve's as-is row move by the month's
„Anlegbar“ less the savings plans (`month_at`), funds and crypto grow by `stretch` from the last
31 December on the household's typed rates, and a property's value and loans, which phase 4
projects per year, run in a straight line between the stored month-end and each 31 December.
At every 31 December the walk equals phase 9's point (tests/rules/test_phase10_goal.py).
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction

from hausbuch.rules.plan.levers import MonthBasis, add_months, flat_by_year, month_at, months_after
from hausbuch.rules.plan.projection import (
    CLASSES,
    ClassPlan,
    FlatScenario,
    ProjectionStart,
    stretch,
)
from hausbuch.rules.plan.surplus import ZERO, cent
from hausbuch.schemas.plan import HouseholdLevers, SpendingChange

# Without a property the walk stops this many years after the newest closed month; with one
# it stops at the last 31 December before the earliest sale year (phase 4 projects no further).
WALK_YEARS = 40


@dataclass(frozen=True)
class WalkMonth:
    month: str  # YYYY-MM
    net_worth: Decimal
    reserve: Decimal  # phase 9's as-is reserve row (§7.4)


@dataclass(frozen=True)
class Walk:
    months: tuple[WalkMonth, ...]
    used_up: str | None = None  # the first month the as-is reserve falls below 0, not walked

    def at(self, month: str) -> WalkMonth | None:
        for m in self.months:
            if m.month == month:
                return m
        return None

    @property
    def last(self) -> str | None:
        return self.months[-1].month if self.months else None


@dataclass(frozen=True)
class Projection:
    """What phase 9's projection reads (services.plan), for walking it again with other
    spending changes."""

    basis: MonthBasis  # its spending changes are replaced by each walk's
    start: ProjectionStart
    reserve_start: Decimal
    plans: Sequence[ClassPlan]
    flats: Mapping[Hashable, FlatScenario]  # the properties' default scenarios, none missing
    levers: HouseholdLevers


def last_month(p: Projection) -> str:
    """The last month a walk reaches: December before the earliest sale year, or WALK_YEARS."""
    newest = p.basis.newest_closed
    if p.flats:
        sale = min(f.sale_year for f in p.flats.values())
        years = [y for y in flat_by_year({k: f.years for k, f in p.flats.items()}) if y < sale]
        return f"{max(years)}-12" if years else newest
    return add_months(newest, 12 * WALK_YEARS)


def walk(
    p: Projection, spending_changes: Sequence[SpendingChange] = (), until: str | None = None
) -> Walk:
    """Month by month from the month after the newest closed one to `until` (default the
    last month), with `spending_changes` in force (D263) and nothing else changed."""
    basis = replace(p.basis, spending_changes=tuple(spending_changes))
    newest = basis.newest_closed
    end = last_month(p) if until is None else min(until, last_month(p))
    scenarios = dict(p.flats)
    flat_month = flat_by_year({k: f.years for k, f in scenarios.items()})
    anchors = _property_anchors(p, flat_month)
    rates = {c: getattr(p.levers.value_change, c.value) for c in CLASSES}
    by_class = {c: sum((x.monthly for x in p.plans if x.asset_class is c), ZERO) for c in CLASSES}
    continuing = sum((x.monthly for x in p.plans), ZERO)
    bank, as_is = Fraction(p.start.bank), p.reserve_start
    values = {c: Fraction(p.start.of(c)) for c in CLASSES}
    out: list[WalkMonth] = []
    k = payments = 0
    while add_months(newest, k) < end:
        k += 1
        month = month_at(basis, k, flat_month, p.levers.income_changes, p.levers.one_offs)
        bank += Fraction(month.investable - continuing)
        as_is += month.investable - continuing
        if as_is < 0:
            return Walk(months=tuple(out), used_up=month.month)
        payments += 1
        now = {}
        for c, rate in rates.items():
            if rate is None:
                now[c] = values[c]  # nothing held and nothing paid in (projection_todos)
                continue
            paid = [Fraction(by_class[c])] * payments
            now[c] = stretch(values[c], Fraction(rate), paid, payments)
        if month.month.endswith("-12"):
            values, payments = now, 0
        value, loans = _property_at(anchors, k)
        total = (
            _cent(bank)
            + sum((_cent(v) for v in now.values()), ZERO)
            + cent(value)
            - cent(loans)
            - p.start.other_liabilities
        )
        out.append(WalkMonth(month=month.month, net_worth=total, reserve=as_is))
    return Walk(months=tuple(out))


def _property_anchors(
    p: Projection, flat_month: Mapping[int, Decimal]
) -> list[tuple[int, Fraction, Fraction]]:
    """(k, value, loans) at the stored month-end (k = 0) and at each projected 31 December."""
    anchors = [(0, Fraction(p.start.property), Fraction(p.start.loans))]
    if not p.flats:
        return anchors
    newest = p.basis.newest_closed
    sale = min(f.sale_year for f in p.flats.values())
    for year in sorted(y for y in flat_month if y < sale):
        k = months_after(newest, f"{year}-12")
        if k <= 0:
            continue
        figures = [f.years[year] for f in p.flats.values()]
        value = sum((f.value or ZERO for f in figures), ZERO)
        loans = sum((f.loan_balance or ZERO for f in figures), ZERO)
        anchors.append((k, Fraction(value), Fraction(loans)))
    return anchors


def _property_at(
    anchors: Sequence[tuple[int, Fraction, Fraction]], k: int
) -> tuple[Decimal, Decimal]:
    """A straight line between the two anchors around month k; the last anchor after it."""
    before = anchors[0]
    for anchor in anchors[1:]:
        if anchor[0] >= k:
            k0, v0, l0 = before
            k1, v1, l1 = anchor
            share = Fraction(k - k0, k1 - k0)
            return _dec(v0 + (v1 - v0) * share), _dec(l0 + (l1 - l0) * share)
        before = anchor
    return _dec(before[1]), _dec(before[2])


def _dec(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


def _cent(value: Fraction) -> Decimal:
    return cent(_dec(value))
