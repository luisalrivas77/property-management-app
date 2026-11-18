"""The net worth in some years (docs/phases/phase-9.md §7.5, D247 amended). Fixture:
tests/fixtures/phase9/net_worth_projection.yaml.

A pure function beside the paths, which it does not change:

1. Instead of the figures, a to-do when an input is missing: a stored run at the newest closed
   month-end (`month_end_missing`), a default scenario for every property
   (`no_default_scenario`), a typed yearly change for each of depot and crypto that has a value
   at the start or receives a savings plan (`rate_missing`). (Three closed months are the
   caller's: without them there is no month basis at all.)
2. The start: the classes and liabilities of that run.
3. The points: 31 December of each year every property's default scenario projects, before the
   earliest sale year, at most `net_worth_years` of them (`ends_before_sale_year` when the sale
   year cut them). Without any property: the years after the newest closed month's year.
4. Bank, month by month: bank(k) = bank(k−1) + investable(k) − every plan (§7.1 with the
   scenario's income changes and one-offs), over the projection's own length; so bank(k) −
   bank(0) = reserve_as_is(k) − reserve(0) for every month.
5. Funds and crypto per class with its typed rate r, over a stretch of m months ending on a
   31 December: V × (1 + r·m/12) + Σ_j c_j × (1 + r·(m − j)/12), in exact fractions, rounded to
   the cent half-up only in the output (Q-O147).
6. The flat's value and loan balance from each year's scenario figures; other liabilities stay
   at their start value.
7. Each point's change since the start in five rows that sum to it.
8. It ends early when the as_is reserve falls below 0: no point from that month on.

The arithmetic is fixture_models_phase9.project's, written once more here.
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Protocol

from hausbuch.schemas.health import HealthTodo, HealthTodoCode
from hausbuch.schemas.plan import (
    HouseholdLevers,
    NetWorthProjection,
    PlanClass,
    ProjectionChange,
    ProjectionPoint,
)

from .levers import FlatYear, MonthBasis, add_months, flat_by_year, month_at
from .surplus import ZERO, cent

CLASSES = (PlanClass.DEPOT, PlanClass.CRYPTO)
PLAN_HREF = "/planning?tab=haushalt"
PROPERTY_HREF = "/planning"


class ClassPlan(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def monthly(self) -> Decimal: ...

    @property
    def asset_class(self) -> PlanClass: ...


@dataclass(frozen=True)
class ProjectionStart:
    """The stored run at the newest closed month-end, by class (§7.5 (2))."""

    month_end: date
    bank: Decimal
    depot: Decimal
    crypto: Decimal
    property: Decimal
    loans: Decimal
    other_liabilities: Decimal

    def of(self, plan_class: PlanClass) -> Decimal:
        return self.depot if plan_class is PlanClass.DEPOT else self.crypto

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
    """A property's default scenario: its sale year and its projected years."""

    sale_year: int
    years: Mapping[int, FlatYear]


@dataclass(frozen=True)
class ProjectionRun:
    """The projection and what it passed on the way (the fixture checks these too)."""

    projection: NetWorthProjection
    decembers: dict[int, dict[str, Decimal]] = field(default_factory=dict)
    as_is: dict[str, Decimal] = field(default_factory=dict)  # the as_is reserve per month
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
    return HealthTodo(
        code=HealthTodoCode.NO_DEFAULT_SCENARIO,
        text=(
            "Für die Wohnung ist noch kein Standard-Szenario gespeichert; ohne es kennt die "
            "Rechnung weder ihren Wert noch ihre Zahlungen in den kommenden Jahren."
        ),
        action="Szenario für die Wohnung anlegen",
        href=PROPERTY_HREF,
    )


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


def projection_todos(
    start: ProjectionStart | None,
    month_end: date,
    plans: Sequence[ClassPlan],
    flats: Mapping[Hashable, FlatScenario | None],
    levers: HouseholdLevers,
) -> list[HealthTodo]:
    """§7.5 (1): what stands in the card's place, in this order."""
    todos = []
    if start is None:
        todos.append(_month_end_todo(month_end))
    if any(f is None for f in flats.values()):
        todos.append(scenario_todo())
    held = {c for c in CLASSES if start is not None and start.of(c) != 0}
    held |= {p.asset_class for p in plans}
    missing = [c for c in CLASSES if c in held and getattr(levers.value_change, c.value) is None]
    if missing:
        todos.append(_rate_todo(missing))
    return todos


def project_net_worth(
    basis: MonthBasis,
    start: ProjectionStart | None,
    month_end: date,
    reserve_start: Decimal,
    plans: Sequence[ClassPlan],
    flats: Mapping[Hashable, FlatScenario | None],
    levers: HouseholdLevers,
) -> ProjectionRun:
    """From the stored run at the newest closed month-end (`month_end`), on the household's
    typed rates; the app proposes none. `reserve_start` is the paths' start (the as_is row)."""
    todos = projection_todos(start, month_end, plans, flats, levers)
    if todos:
        return ProjectionRun(projection=NetWorthProjection(todos=todos))
    assert start is not None
    scenarios = {k: f for k, f in flats.items() if f is not None}
    newest = basis.newest_closed
    rates = {c: getattr(levers.value_change, c.value) for c in CLASSES}
    flat_month = flat_by_year({k: f.years for k, f in scenarios.items()})
    if scenarios:
        sale = min(f.sale_year for f in scenarios.values())
        candidates = sorted(y for y in flat_month if y < sale)
        years = candidates[: levers.net_worth_years]
        ends_before = sale if len(candidates) < levers.net_worth_years else None
    else:
        first = int(newest[:4]) + 1
        years = list(range(first, first + levers.net_worth_years))
        ends_before = None
    by_class = {c: sum((p.monthly for p in plans if p.asset_class is c), ZERO) for c in CLASSES}
    continuing = sum((p.monthly for p in plans), ZERO)
    bank, as_is = Fraction(start.bank), reserve_start
    values = {c: Fraction(start.of(c)) for c in CLASSES}
    decembers: dict[int, dict[str, Decimal]] = {}
    as_is_by_month: dict[str, Decimal] = {}
    bank_by_day: dict[date, Decimal] = {}
    used: str | None = None
    k = payments = 0
    while years and add_months(newest, k) < f"{years[-1]}-12":
        k += 1
        month = month_at(basis, k, flat_month, levers.income_changes, levers.one_offs)
        bank += Fraction(month.investable - continuing)
        as_is += month.investable - continuing
        as_is_by_month[month.month] = as_is
        if used is None and as_is < 0:
            used = month.month
        payments += 1
        if month.month.endswith("-12"):
            for c, rate in rates.items():
                if rate is None:
                    continue  # nothing held and nothing paid in
                monthly = Fraction(by_class[c])
                values[c] = stretch(values[c], Fraction(rate), [monthly] * payments, payments)
            payments = 0
            year = int(month.month[:4])
            decembers[year] = {
                "bank": cent(Decimal(bank.numerator) / Decimal(bank.denominator)),
                **{c.value: _cent(values[c]) for c in CLASSES},
            }
            bank_by_day[date(year, 12, 31)] = decembers[year]["bank"]
    points = []
    for year in years:
        if used is not None and f"{year}-12" >= used:
            break
        classes = decembers[year]
        figures = [f.years[year] for f in scenarios.values()]
        value = sum((f.value or ZERO for f in figures), ZERO)
        loans = sum((f.loan_balance or ZERO for f in figures), ZERO)
        total = sum(classes.values(), ZERO) + value - loans - start.other_liabilities
        paid = {c: paid_until(by_class[c], newest, year) for c in CLASSES}
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
        projection=NetWorthProjection(
            start=start.point(),
            points=points,
            ends_before_sale_year=ends_before,
            used_up=used,
        ),
        decembers=decembers,
        as_is=as_is_by_month,
        bank=bank_by_day,
    )


def _cent(value: Fraction) -> Decimal:
    return cent(Decimal(value.numerator) / Decimal(value.denominator))
