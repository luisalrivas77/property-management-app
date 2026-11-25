"""The net worth in some years as one month walk (docs/phases/phase-9.md §7.5, D247 amended;
docs/phases/phase-10.md §7.11 (a), D301). Fixtures: tests/fixtures/phase9/net_worth_projection.yaml,
tests/fixtures/phase10/reserve_above.yaml and goal_routes.yaml.

One walk feeds Anlagen's „Vermögen im Verlauf“ (its 31 December points), the household goal and
Durchspielen (phase 10 D301), so the three never show two futures. A pure function beside the
paths, which it does not change:

1. Instead of the figures, a to-do when an input is missing: a stored run at the newest closed
   month-end (`month_end_missing`), a typed yearly change for each of depot and crypto that is
   invested at the start, receives a savings plan or has a share in the investment split
   (`rate_missing`). A property without a default scenario is no longer one: it is kept (5).
2. The start: the classes and liabilities of that run; a depot ticked „Zählt zur Reserve“
   (D304) is in the reserve and stays at its start value in the depot row.
3. Each month k after the newest closed one, in this order: the reserve row R earns the typed
   reserve rate for the month, R × r ÷ 12 to the cent half-up (only while R > 0); R += the
   month's „Anlegbar“ (§7.1, the buffer kept back) − every savings plan; in December of a
   property's sale year R += its net proceeds (counted once); R < 0 ends the walk (`used_up`);
   what lies above the reserve target moves out of R into the classes in the split's shares
   (no split or no target: nothing moves; nothing ever moves back).
4. Funds and crypto per class with its typed rate r, over a stretch of m months ending on a 31
   December: V × (1 + r·m/12) + Σ_j c_j × (1 + r·(m − j)/12), in exact fractions, rounded to
   the cent half-up only in the output (Q-O147); a month inside a year reads the stretch over
   the months so far.
5. A property with a default scenario is walked to its own sale year: phase 4's yearly cash
   (0 from the January after), its value and loans in a straight line between the stored
   month-end and each 31 December, 0 from December of its sale year (its proceeds are on the
   reserve). A property without one is kept (D301 (a) 5): its value today for ever, its loans
   along their schedules, its cash the measured mean.
6. The points: 31 December of each year after the newest closed month's year,
   `net_worth_years` of them, past any sale; each with its change since the start in five rows.
7. The bank row = the start's bank + (R − R at the start).

The arithmetic is fixture_models_phase9.project's, written once more here.
"""

import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Protocol

from hausbuch.schemas.health import HealthTodo, HealthTodoCode
from hausbuch.schemas.plan import (
    HouseholdLevers,
    InvestmentSplit,
    NetWorthProjection,
    PlanClass,
    ProjectionChange,
    ProjectionPoint,
    SpendingChange,
)

from .levers import FlatYear, MonthBasis, add_months, month_at, months_after
from .surplus import ZERO, cent

CLASSES = (PlanClass.DEPOT, PlanClass.CRYPTO)
PLAN_HREF = "/planning?tab=haushalt"
PROPERTY_HREF = "/planning"

# The longest walk: forty years after the newest closed month (phase 10 §7.11 (a) 7).
WALK_YEARS = 40


class ClassPlan(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def monthly(self) -> Decimal: ...

    @property
    def asset_class(self) -> PlanClass: ...


@dataclass(frozen=True)
class ProjectionStart:
    """The stored run at the newest closed month-end, by class (§7.5 (2)). `reserve_depot`:
    the part of `depot` on depots ticked „Zählt zur Reserve“ (phase 10 D304)."""

    month_end: date
    bank: Decimal
    depot: Decimal
    crypto: Decimal
    property: Decimal
    loans: Decimal
    other_liabilities: Decimal
    reserve_depot: Decimal = ZERO

    def of(self, plan_class: PlanClass) -> Decimal:
        return self.depot if plan_class is PlanClass.DEPOT else self.crypto

    def invested(self, plan_class: PlanClass) -> Decimal:
        """What the walk grows at the class's rate: a ticked depot is the reserve's."""
        if plan_class is PlanClass.DEPOT:
            return self.depot - self.reserve_depot
        return self.crypto

    @property
    def total(self) -> Decimal:
        assets = self.bank + self.depot + self.crypto + self.property
        return assets - self.loans - self.other_liabilities

    def point(self) -> ProjectionPoint:
        return ProjectionPoint(
            on=self.month_end,
            bank=self.bank,
            depot=self.depot,
            crypto=self.crypto,
            property=self.property,
            loans=self.loans,
            other_liabilities=self.other_liabilities,
            total=self.total,
        )


@dataclass(frozen=True)
class FlatScenario:
    """A property in the walk. With a default scenario: its sale year, phase 4's projected
    years and the sale's net proceeds (`SaleResult.net_proceeds`). Without one (`sale_year`
    None, phase 10 D301 (a) 5): kept, its value today (`kept_value`) and its loans' balance
    per 31 December along their schedules (`kept_loans`), its cash the measured mean.
    `start_value` and `start_loans`: its value and loans in the stored run, the straight
    line's first point (None: the start's totals, for a household with one property)."""

    sale_year: int | None
    years: Mapping[int, FlatYear] = field(default_factory=dict)
    net_proceeds: Decimal = ZERO
    kept_value: Decimal | None = None
    kept_loans: Mapping[int, Decimal] = field(default_factory=dict)
    start_value: Decimal | None = None
    start_loans: Decimal | None = None

    @property
    def kept(self) -> bool:
        return self.sale_year is None


@dataclass(frozen=True)
class ProjectionRun:
    """The projection and what it passed on the way (the fixture checks these too)."""

    projection: NetWorthProjection
    decembers: dict[int, dict[str, Decimal]] = field(default_factory=dict)
    as_is: dict[str, Decimal] = field(default_factory=dict)  # the reserve row per month
    bank: dict[date, Decimal] = field(default_factory=dict)  # the bank row per 31 December


def stretch(value: Fraction, rate: Fraction, payments: Sequence[Fraction], months: int) -> Fraction:
    """§7.5 (5): V × (1 + r·m/12) + Σ_j c_j × (1 + r·(m − j)/12), j = 1 … m."""
    out = value * (1 + rate * Fraction(months, 12))
    for j, paid in enumerate(payments, start=1):
        out += paid * (1 + rate * Fraction(months - j, 12))
    return out


def paid_until(monthly: Decimal, newest_closed: str, year: int) -> Decimal:
    """The plan's payments from the month after the newest closed one to December of `year`."""
    months = (year - int(newest_closed[:4])) * 12 + 12 - int(newest_closed[5:])
    return monthly * months


def walk_flat_years(
    flats: Mapping[Hashable, FlatScenario], first: int, last: int
) -> dict[int, Decimal]:
    """D301: per year from `first` to `last`, Σ over the properties of the scenario's
    (net_cash + tax_effect) ÷ 12, each to the cent, and 0 for a property after its sale year.
    A year some property's scenario does not project keeps the measured mean (left out of the
    map). Empty, so the measured mean throughout, when no property has a scenario or one is
    kept without one."""
    if not flats or any(f.kept for f in flats.values()):
        return {}
    out: dict[int, Decimal] = {}
    for year in range(first, last + 1):
        parts = []
        for f in flats.values():
            assert f.sale_year is not None
            if year > f.sale_year:
                parts.append(ZERO)
            elif year in f.years:
                y = f.years[year]
                parts.append(cent((y.net_cash + (y.tax_effect or ZERO)) / 12))
            else:
                break
        else:
            out[year] = sum(parts, ZERO)
    return out


# --- To-dos --------------------------------------------------------------------------------


def _month_end_todo(month_end: date) -> HealthTodo:
    return HealthTodo(
        code=HealthTodoCode.MONTH_END_MISSING,
        text=(
            f"Die Rechnung beginnt beim Monatsabschluss vom {month_end.strftime('%d.%m.%Y')}; "
            "er ist noch nicht gespeichert. Hausbuch hält ihn am nächsten Morgen selbst fest, "
            "mit „Monatsabschluss festhalten“ sofort."
        ),
        action="Monatsabschluss festhalten",
        href=f"/?month_end={month_end.isoformat()}",
    )


def scenario_todo() -> HealthTodo:
    """The property page's to-do (phase 10 D301 (a) 5: no longer the projection's or the
    goal's, which keep a property without a scenario)."""
    return HealthTodo(
        code=HealthTodoCode.NO_DEFAULT_SCENARIO,
        text=(
            "Für die Wohnung ist noch kein Standard-Szenario gespeichert; ohne es kennt die "
            "Rechnung weder ihren Wert noch ihre Zahlungen in den kommenden Jahren."
        ),
        action="Szenario für die Wohnung anlegen",
        href=PROPERTY_HREF,
    )


def flat_todos(flats: Mapping[Hashable, FlatScenario | None]) -> list[HealthTodo]:
    """The plan's „Szenario für die Wohnung anlegen“, once, while a property is kept without a
    default scenario (D247; shown on the property page, not in the projection's place)."""
    if any(f is not None and f.kept for f in flats.values()):
        return [scenario_todo()]
    return []


def above_target(amount: Decimal, target: Decimal) -> Decimal:
    """Phase 10 §7.11 (d), „Anlegbar jetzt“: the reserve above its target today, which the walk
    invests in its first month (D301 (a) 3); 0 at or below it."""
    return max(ZERO, amount - target)


CLASS_WORDS = {PlanClass.DEPOT: "Fonds und Wertpapiere", PlanClass.CRYPTO: "Krypto"}


def _rate_todo(missing: Sequence[PlanClass]) -> HealthTodo:
    words = " und ".join(CLASS_WORDS[c] for c in missing)
    return HealthTodo(
        code=HealthTodoCode.RATE_MISSING,
        text=(
            f"Für {words} fehlt eure angenommene Wertänderung pro Jahr. Hausbuch nimmt keinen "
            "Wert an; tragt ihr eine ein, rechnet die Karte mit ihr."
        ),
        action="Annahme eintragen",
        href=PLAN_HREF,
    )


def _shares(split: InvestmentSplit | None) -> dict[PlanClass, Fraction]:
    if split is None:
        return {}
    return {c: Fraction(getattr(split, c.value)) / 100 for c in CLASSES}


def projection_todos(
    start: ProjectionStart | None,
    month_end: date,
    plans: Sequence[ClassPlan],
    levers: HouseholdLevers,
    split: InvestmentSplit | None = None,
) -> list[HealthTodo]:
    """§7.5 (1): what stands in the card's place, in this order."""
    todos = []
    if start is None:
        todos.append(_month_end_todo(month_end))
    held = {c for c in CLASSES if start is not None and start.invested(c) != 0}
    held |= {p.asset_class for p in plans}
    held |= {c for c, share in _shares(split).items() if share > 0}
    missing = [c for c in CLASSES if c in held and getattr(levers.value_change, c.value) is None]
    if missing:
        todos.append(_rate_todo(missing))
    return todos


# --- The walk ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Projection:
    """What the walk reads (services.plan), for walking it again with an extra saving or
    other spending changes (the goal, Durchspielen). `target`: the reserve target, fixed for
    the walk (months × today's need, D237, D238); `split`: the effective investment split
    (None: nothing moves out of the reserve)."""

    basis: MonthBasis  # its spending changes are replaced by a walk's when it names some
    start: ProjectionStart
    reserve_start: Decimal
    plans: Sequence[ClassPlan]
    flats: Mapping[Hashable, FlatScenario]
    levers: HouseholdLevers
    target: Decimal | None = None
    split: InvestmentSplit | None = None


@dataclass(frozen=True)
class WalkMonth:
    month: str  # YYYY-MM
    net_worth: Decimal
    reserve: Decimal  # the reserve row at the month's end, after what moved (D301)


@dataclass(frozen=True)
class Walk:
    months: tuple[WalkMonth, ...]
    used_up: str | None = None  # the first month the reserve row falls below 0, not walked

    def at(self, month: str) -> WalkMonth | None:
        for m in self.months:
            if m.month == month:
                return m
        return None

    @property
    def last(self) -> str | None:
        return self.months[-1].month if self.months else None


@dataclass
class _Run:
    decembers: dict[int, dict[str, Decimal]] = field(default_factory=dict)
    as_is: dict[str, Decimal] = field(default_factory=dict)
    bank: dict[date, Decimal] = field(default_factory=dict)
    paid_by_year: dict[int, dict[PlanClass, Fraction]] = field(default_factory=dict)
    months: list[WalkMonth] = field(default_factory=list)
    used_up: str | None = None


def last_month(p: Projection) -> str:
    """The last month a walk reaches: WALK_YEARS after the newest closed month, past any sale
    (phase 10 §7.11 (a) 7)."""
    return add_months(p.basis.newest_closed, 12 * WALK_YEARS)


def walk(
    p: Projection,
    spending_changes: Sequence[SpendingChange] = (),
    until: str | None = None,
    extra: Decimal = ZERO,
    extra_from: str | None = None,
) -> Walk:
    """Month by month from the month after the newest closed one to `until` (default the
    last month), with `spending_changes` in force and no other (D263) and `extra`
    added to each month's „Anlegbar“ from `extra_from` on (the goal's more saving, D302)."""
    end = last_month(p) if until is None else min(until, last_month(p))
    run = _walk(p, end, spending_changes, extra, extra_from, monthly=True)
    return Walk(months=tuple(run.months), used_up=run.used_up)


def _walk(
    p: Projection,
    end: str,
    spending_changes: Sequence[SpendingChange] | None,
    extra: Decimal,
    extra_from: str | None,
    monthly: bool,
) -> _Run:
    basis = p.basis
    if spending_changes is not None:
        basis = replace(basis, spending_changes=tuple(spending_changes))
    start, levers = p.start, p.levers
    newest = basis.newest_closed
    vc = levers.value_change
    rates = {c: getattr(vc, c.value) for c in CLASSES}
    shares = _shares(p.split) if p.target is not None else {}
    flat_month = walk_flat_years(p.flats, int(newest[:4]) + 1, int(end[:4]))
    lines = _property_lines(p) if monthly else []
    by_class = {c: sum((x.monthly for x in p.plans if x.asset_class is c), ZERO) for c in CLASSES}
    continuing = sum((x.monthly for x in p.plans), ZERO)
    proceeds: dict[int, Fraction] = {}
    for f in p.flats.values():
        if f.sale_year is not None:
            proceeds[f.sale_year] = proceeds.get(f.sale_year, Fraction(0)) + Fraction(
                f.net_proceeds
            )
    target = None if p.target is None else Fraction(p.target)
    r0 = Fraction(p.reserve_start)
    reserve = r0
    values = {c: Fraction(start.invested(c)) for c in CLASSES}
    pays: dict[PlanClass, list[Fraction]] = {c: [] for c in CLASSES}
    paid = {c: Fraction(0) for c in CLASSES}
    out = _Run()
    k = 0
    while add_months(newest, k) < end:
        k += 1
        month = month_at(basis, k, flat_month, levers.income_changes, levers.one_offs)
        ym = month.month
        if vc.reserve is not None and reserve > 0:
            # the month's interest to the cent, half-up, as a bank credits it (exact fractions
            # would compound a denominator over up to 480 months)
            reserve += Fraction(cent(_dec(reserve) * vc.reserve / 12))
        reserve += Fraction(month.investable - continuing)
        if extra and extra_from is not None and ym >= extra_from:
            reserve += Fraction(extra)
        december = ym.endswith("-12")
        if december:
            reserve += proceeds.get(int(ym[:4]), Fraction(0))
        if reserve < 0:
            out.as_is[ym] = _cent(reserve)
            out.used_up = ym
            break
        moved = Fraction(0)
        if shares and target is not None and reserve > target:
            moved = reserve - target
            reserve -= moved
        out.as_is[ym] = _cent(reserve)
        for c in CLASSES:
            pay = Fraction(by_class[c]) + moved * shares.get(c, Fraction(0))
            pays[c].append(pay)
            paid[c] += pay
        bank = _cent(Fraction(start.bank) + reserve - r0)
        if monthly:
            now = {
                c: values[c]
                if rate is None
                else stretch(values[c], Fraction(rate), pays[c], len(pays[c]))
                for c, rate in rates.items()
            }
            value, loans = _property_at(lines, k)
            total = (
                bank
                + sum((_cent(v) for v in now.values()), ZERO)
                + start.reserve_depot
                + _cent(value)
                - _cent(loans)
                - start.other_liabilities
            )
            out.months.append(WalkMonth(month=ym, net_worth=total, reserve=_cent(reserve)))
        if december:
            for c, rate in rates.items():
                if rate is not None:  # nothing held and nothing paid in (§15.1)
                    values[c] = stretch(values[c], Fraction(rate), pays[c], len(pays[c]))
                pays[c] = []
            year = int(ym[:4])
            out.decembers[year] = {
                "bank": bank,
                "depot": _cent(values[PlanClass.DEPOT]) + start.reserve_depot,
                "crypto": _cent(values[PlanClass.CRYPTO]),
            }
            out.paid_by_year[year] = dict(paid)
            out.bank[date(year, 12, 31)] = bank
    return out


# One property's straight line: (k, value, loans) at the stored month-end and at each 31
# December it is known for, and the k from which it counts 0 (December of its sale year).
_Line = tuple[list[tuple[int, Fraction, Fraction]], int | None]


def _property_lines(p: Projection) -> list[_Line]:
    """Per property its line. The first point is its value and loans in the stored run
    (`start_value`, `start_loans`); one property without them takes the run's totals; with
    several, a property without them starts flat at its first 31 December. What the run holds
    beyond the walked properties stays as it is."""
    newest = p.basis.newest_closed
    first, last = int(newest[:4]), int(last_month(p)[:4])
    flats = list(p.flats.values())
    known = all(f.start_value is not None for f in flats)
    lines: list[_Line] = []
    for f in flats:
        anchors: list[tuple[int, Fraction, Fraction]] = []
        for year in range(first, last + 1):
            k = months_after(newest, f"{year}-12")
            if k <= 0:
                continue
            if f.kept:
                value, loans = f.kept_value or ZERO, f.kept_loans.get(year, ZERO)
            elif year <= (f.sale_year or 0) and year in f.years:
                y = f.years[year]
                value, loans = y.value or ZERO, y.loan_balance or ZERO
            else:
                continue
            anchors.append((k, Fraction(value), Fraction(loans)))
        if f.start_value is not None:
            v0, l0 = Fraction(f.start_value), Fraction(f.start_loans or ZERO)
        elif len(flats) == 1:
            v0, l0 = Fraction(p.start.property), Fraction(p.start.loans)
        elif anchors:
            v0, l0 = anchors[0][1], anchors[0][2]
        else:
            v0 = l0 = Fraction(0)
        gone = None if f.sale_year is None else months_after(newest, f"{f.sale_year}-12")
        lines.append(([(0, v0, l0), *anchors], gone))
    if known:  # also with no property: the run's totals stay
        rest_value = p.start.property - sum((f.start_value or ZERO for f in flats), ZERO)
        rest_loans = p.start.loans - sum((f.start_loans or ZERO for f in flats), ZERO)
        if rest_value or rest_loans:
            lines.append(([(0, Fraction(rest_value), Fraction(rest_loans))], None))
    return lines


def _property_at(lines: Sequence[_Line], k: int) -> tuple[Fraction, Fraction]:
    value = loans = Fraction(0)
    for anchors, gone in lines:
        if gone is not None and k >= gone:
            continue  # sold on 31 December of its sale year
        v, l_ = _on_line(anchors, k)
        value += v
        loans += l_
    return value, loans


def _on_line(
    anchors: Sequence[tuple[int, Fraction, Fraction]], k: int
) -> tuple[Fraction, Fraction]:
    """A straight line between the two anchors around month k; the last anchor after it."""
    before = anchors[0]
    for anchor in anchors[1:]:
        if anchor[0] >= k:
            k0, v0, l0 = before
            k1, v1, l1 = anchor
            share = Fraction(k - k0, k1 - k0)
            return v0 + (v1 - v0) * share, l0 + (l1 - l0) * share
        before = anchor
    return before[1], before[2]


# --- The points ----------------------------------------------------------------------------


def project_net_worth(
    basis: MonthBasis,
    start: ProjectionStart | None,
    month_end: date,
    reserve_start: Decimal,
    plans: Sequence[ClassPlan],
    flats: Mapping[Hashable, FlatScenario | None],
    levers: HouseholdLevers,
    target: Decimal | None = None,
    split: InvestmentSplit | None = None,
) -> ProjectionRun:
    """From the stored run at the newest closed month-end (`month_end`), on the household's
    typed rates; the app proposes none. `reserve_start` is the paths' start (the as_is row),
    `target` the reserve target and `split` the effective investment split (D301)."""
    todos = projection_todos(start, month_end, plans, levers, split)
    if todos:
        return ProjectionRun(projection=NetWorthProjection(todos=todos))
    assert start is not None
    p = Projection(
        basis=basis,
        start=start,
        reserve_start=reserve_start,
        plans=tuple(plans),
        flats={key: f for key, f in flats.items() if f is not None},
        levers=levers,
        target=target,
        split=split,
    )
    return points_of(p)


def points_of(p: Projection) -> ProjectionRun:
    """The walk's 31 December points, `net_worth_years` of them."""
    start, levers, newest = p.start, p.levers, p.basis.newest_closed
    first = int(newest[:4]) + 1
    years = list(range(first, first + levers.net_worth_years))
    run = _walk(p, f"{years[-1]}-12", None, ZERO, None, monthly=False)
    rates = {c: getattr(levers.value_change, c.value) for c in CLASSES}
    lines = _property_lines(p)
    points = []
    for year in years:
        if year not in run.decembers:
            break
        classes = run.decembers[year]
        value, loans = (_cent(x) for x in _property_at(lines, months_after(newest, f"{year}-12")))
        total = sum(classes.values(), ZERO) + value - loans - start.other_liabilities
        paid = {c: _cent(run.paid_by_year[year][c]) for c in CLASSES}
        by_assumption = {
            c: classes[c.value] - start.of(c) - paid[c] for c in CLASSES if rates[c] is not None
        }
        change = ProjectionChange(
            on_accounts=classes["bank"] - start.bank,
            paid_in=sum(paid.values(), ZERO),
            from_assumptions=sum(by_assumption.values(), ZERO),
            from_assumptions_by_class=by_assumption,
            flat_value=value - start.property,
            loan_repaid=start.loans - loans,
            total=total - start.total,
        )
        points.append(
            ProjectionPoint(
                on=date(year, 12, 31),
                bank=classes["bank"],
                depot=classes["depot"],
                crypto=classes["crypto"],
                property=value,
                loans=loans,
                other_liabilities=start.other_liabilities,
                total=total,
                change=change,
            )
        )
    return ProjectionRun(
        projection=NetWorthProjection(start=start.point(), points=points, used_up=run.used_up),
        decembers=run.decembers,
        as_is=run.as_is,
        bank=run.bank,
    )


def _dec(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


def _cent(value: Fraction) -> Decimal:
    return cent(_dec(value))


def ceil_cent(value: Fraction) -> Decimal:
    return Decimal(math.ceil(value * 100)) / 100
