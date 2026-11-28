"""„Euer Plan“ (docs/phases/phase-10.md §7.11 (h), D306): the seven cards from the goal's one
walk and the three risks of „Wenn es schlechter läuft“. Pure functions over what the goal reads
(rules/goal/goal.GoalInputs) and its answer; no database, no I/O but the packaged risks.yaml.
Outside rules/health/ and rules/plan/, so services.health.rule_version() does not change with it.

As pinned by tests/fixtures/phase10/plan_page.yaml (its header's readings):

1. The base run is the plan's route: the balanced route when the word is not on track (its cuts
   and its saving extra from next month, at route 3's median when that decides, else at the
   typed rates); on track, the walk without change, or with the adopted budget when only that
   reaches the target. Its first month at or above the target is the plan's „Am Ziel“.
2. Each risk is that run with one change; its row shows the run's first month at or above the
   target and the months from the base run's month to it, or, when it never gets there within
   the walk, `not_until` = the walk's last month.
3. Weak markets: per kind the worst window of the goal's years left (route 3's window) in its
   own full history, annualised as route 3 does, never above the rate typed for it.
4. Income gap: six months without the household's income from the walk's first month; the
   route's extra pauses; the reserve's shortfall is drawn from the invested money
   (`walk(draw_invested=True)`).
5. Repair: per property with a living area, area × the year's maintenance rate × 10 years, paid
   once in the walk's first month; never drawn from the invested money.
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from functools import cache
from importlib.resources import files

import yaml

from hausbuch.rules.plan.levers import add_months, months_after
from hausbuch.rules.plan.projection import Projection, last_month, walk
from hausbuch.rules.plan.surplus import ZERO
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.goal import (
    GoalLeftOut,
    GoalRouteReason,
    GoalSpending,
    GoalWord,
    HouseholdGoal,
)
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import Goal, GoalKind, IncomeChange, OneOff, PlanClass, SpendingChange

from . import history as H
from .goal import GoalInputs, household_goal, reached_in

CENT = Decimal("0.01")
MONTHS_PLACES = Decimal("0.1")
CLASSES = (PlanClass.DEPOT, PlanClass.CRYPTO)


# --- risks.yaml -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanSource:
    name: str
    url: str
    read_on: str


@dataclass(frozen=True)
class PlanRules:
    """rules/goal/risks.yaml: the risks' sizes and the shared view's rounding steps."""

    income_gap_months: int
    repair_years: int
    per_m2: Mapping[int, Decimal]  # the maintenance rate per m² and year, by year
    sources: tuple[PlanSource, ...]
    rounding: Mapping[str, Decimal]  # net_worth, reserve, monthly, once


@cache
def plan_rules() -> PlanRules:
    text = (files("hausbuch.rules.goal") / "risks.yaml").read_text("utf-8")
    data = yaml.safe_load(text)
    repair = data["repair"]
    return PlanRules(
        income_gap_months=int(data["income_gap"]["months"]),
        repair_years=int(repair["years"]),
        per_m2={int(y): Decimal(str(v)) for y, v in repair["per_m2"].items()},
        sources=tuple(
            PlanSource(name=s["name"], url=s["url"], read_on=str(s["read_on"]))
            for s in repair["sources"]
        ),
        rounding={k: Decimal(str(v)) for k, v in data["rounding"].items()},
    )


def maintenance_per_m2(year: int, rules: PlanRules | None = None) -> Decimal | None:
    """The year's maintenance rate per m², else the latest earlier year's; None before the first."""
    rules = rules or plan_rules()
    known = [y for y in rules.per_m2 if y <= year]
    return rules.per_m2[max(known)] if known else None


# --- Inputs -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanProperty:
    """A property of the walk (keyed as `Projection.flats`) with what the page reads besides:
    its stored living area in m² (None: not stored) and its scenario's yearly value change."""

    key: Hashable
    living_area: Decimal | None = None
    value_change: Decimal | None = None
    href: str | None = None  # where its living area is entered; default /properties/<key>


@dataclass(frozen=True)
class PlanInputs:
    goal: GoalInputs
    net_worth_today: Decimal
    reserve_today: Decimal
    reserve_accounts: Sequence[str] = ()  # labels with their provider, „Für euch“ only
    properties: Sequence[PlanProperty] = ()
    invested_this_month: Decimal = ZERO  # the running month's booked investment transfers
    budget_adopted: bool = False  # the route's wants budget is the default scenario's
    # „Anlegbar jetzt“ (§7.11 (d)): the plan's own `above_target`, as GET /household-plan
    # serves it; shown on the on-track plan card („Jetzt anlegen“). None when not read.
    above_target: Decimal | None = None


# --- The route --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    """The plan's run: the projection it walks (its rates included), its spending changes and
    its extra saving a month from `extra_from` on."""

    projection: Projection
    spending_changes: tuple[SpendingChange, ...] = ()
    extra: Decimal = ZERO
    extra_from: str | None = None

    def walk(self, draw_invested: bool = False):
        return walk(
            self.projection,
            self.spending_changes,
            extra=self.extra,
            extra_from=self.extra_from,
            draw_invested=draw_invested,
        )


def _rated(p: Projection, rates: Mapping[PlanClass, Decimal]) -> Projection:
    """The projection with these yearly rates for funds and crypto (not validated, like route
    3's: a calculation, never a lever)."""
    vc = p.levers.value_change.model_copy(update={c.value: r for c, r in rates.items()})
    return replace(p, levers=p.levers.model_copy(update={"value_change": vc}))


def route_of(i: GoalInputs, answer: HouseholdGoal) -> Route | None:
    """Reading 1: the plan's route, None without a projection."""
    p = i.projection
    if p is None:
        return None
    goal = i.goal
    if goal.kind is not GoalKind.NET_WORTH:
        p = replace(p, split=None)  # as household_goal: nothing moves out of the reserve
    if answer.word is GoalWord.ON_TRACK:
        if answer.reached is not None and answer.reached <= goal.due_month:
            return Route(projection=p)
        return Route(projection=p, spending_changes=tuple(i.household_changes))
    b = answer.balanced
    if b is None or b.saving_extra is None:
        return Route(projection=p)
    if b.return_rate is not None:
        p = _rated(p, {c: b.return_rate for c in CLASSES})
    first = add_months(_current(i), 1)
    return Route(
        projection=p,
        spending_changes=tuple(b.spending_changes),
        extra=b.saving_extra,
        extra_from=first,
    )


def _current(i: GoalInputs) -> str:
    return f"{i.today.year:04d}-{i.today.month:02d}"


# --- The risks --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskRun:
    """A run's first month at or above the target and its delay after the base run's; when it
    never gets there within the walk, `not_until` is the walk's last month („bis … nicht“)."""

    reached: str | None
    delay_months: int | None
    not_until: str | None
    used_up: str | None = None  # the month the reserve ran out, which ends the run


def _run(goal: Goal, target: Decimal, base_reached: str | None, w, p: Projection) -> RiskRun:
    got = reached_in(goal, w, target)
    if got is None:
        return RiskRun(reached=None, delay_months=None, not_until=last_month(p), used_up=w.used_up)
    delay = None if base_reached is None else months_after(base_reached, got)
    return RiskRun(reached=got, delay_months=delay, not_until=None, used_up=w.used_up)


@dataclass(frozen=True)
class WeakMarkets:
    available: bool
    reason: GoalRouteReason | None = None
    window_years: int | None = None
    rates: dict[str, Decimal] = field(default_factory=dict)  # walked, per kind
    worst: dict[str, Decimal] = field(default_factory=dict)  # the worst window, per kind
    capped: list[str] = field(default_factory=list)  # kinds kept at their typed rate
    left_out: list[GoalLeftOut] = field(default_factory=list)
    reached: str | None = None
    delay_months: int | None = None
    not_until: str | None = None


def weak_rates(
    typed: Mapping[PlanClass, Decimal | None],
    invested: H.Invested,
    history: H.History,
    years: int,
    mode: AdviceMode = AdviceMode.OWN_USE,
) -> WeakMarkets:
    """Reading 3 without the walk: per kind walked (a typed rate) its worst window of `years`
    in its own history, annualised (floored to 0.1 %), capped at the typed rate."""
    if mode is not AdviceMode.OWN_USE:
        return WeakMarkets(available=False, reason=GoalRouteReason.GENERIC_MODE)
    rates: dict[str, Decimal] = {}
    worst: dict[str, Decimal] = {}
    left_out: list[GoalLeftOut] = []
    depot = typed.get(PlanClass.DEPOT)
    if depot is not None:
        if history.funds is None:
            return WeakMarkets(available=False, reason=GoalRouteReason.SERIES_MISSING)
        products = H.window_products(history.funds.returns, years)
        if not products:
            return WeakMarkets(available=False, reason=GoalRouteReason.HISTORY_SHORT)
        worst[PlanClass.DEPOT.value] = H.annualised(min(products), years)
    crypto = typed.get(PlanClass.CRYPTO)
    if crypto is not None:
        mix = H.mix_of(
            H.Invested(funds=ZERO, coins=dict(invested.coins)),
            None,
            H.History(funds=None, coins=history.coins),
            years,
        )
        if isinstance(mix, H.NoCount):
            if mix.reason is not GoalRouteReason.NOTHING_INVESTED:
                return WeakMarkets(available=False, reason=mix.reason, left_out=list(mix.left_out))
            # no coin held: nothing of the crypto to weigh, its typed rate is kept
        else:
            products = H.window_products(mix.returns, years)
            worst[PlanClass.CRYPTO.value] = H.annualised(min(products), years)
            left_out = list(mix.left_out)
    capped = []
    for c, rate in ((PlanClass.DEPOT, depot), (PlanClass.CRYPTO, crypto)):
        if rate is None:
            continue
        w = worst.get(c.value)
        if w is None or w > rate:
            rates[c.value] = rate
            if w is not None:
                capped.append(c.value)
        else:
            rates[c.value] = w
    return WeakMarkets(
        available=True,
        window_years=years,
        rates=rates,
        worst=worst,
        capped=capped,
        left_out=left_out,
    )


def weak_markets(
    i: GoalInputs, route: Route, target: Decimal, base_reached: str | None, months_left: int
) -> WeakMarkets:
    p = i.projection
    assert p is not None
    vc = p.levers.value_change
    typed = {c: getattr(vc, c.value) for c in CLASSES}
    out = weak_rates(typed, i.invested, i.history, H.window_years(months_left), i.mode)
    if not out.available:
        return out
    walked = _rated(route.projection, {PlanClass(k): v for k, v in out.rates.items()})
    run = _run(i.goal, target, base_reached, replace(route, projection=walked).walk(), walked)
    return replace(out, reached=run.reached, delay_months=run.delay_months, not_until=run.not_until)


@dataclass(frozen=True)
class IncomeGap:
    available: bool
    months: int
    reached: str | None = None
    delay_months: int | None = None
    not_until: str | None = None
    drawn: Decimal = ZERO
    used_up: str | None = None


def gap_changes(
    mean_income: Decimal, typed: Sequence[IncomeChange], first: str, months: int
) -> list[IncomeChange]:
    """The income changes that set the household's income to 0 from `first` for `months`: the
    mean income taken off, and each typed change cancelled where it overlaps."""
    last = add_months(first, months - 1)
    out = [IncomeChange(monthly_net=-mean_income, from_month=first, to_month=last)]
    for c in typed:
        start = max(c.from_month, first)
        end = last if c.to_month is None else min(c.to_month, last)
        if start <= end and c.monthly_net != 0:
            out.append(IncomeChange(monthly_net=-c.monthly_net, from_month=start, to_month=end))
    return out


def income_gap(
    i: GoalInputs, route: Route, target: Decimal, base_reached: str | None, rules: PlanRules
) -> IncomeGap:
    p = route.projection
    first = add_months(p.basis.newest_closed, 1)
    n = rules.income_gap_months
    changes = gap_changes(p.basis.mean_income, p.levers.income_changes, first, n)
    levers = p.levers.model_copy(update={"income_changes": [*p.levers.income_changes, *changes]})
    gapped = replace(p, levers=levers)
    after = add_months(first, n)
    extra_from = None if route.extra_from is None else max(route.extra_from, after)
    w = replace(route, projection=gapped, extra_from=extra_from).walk(draw_invested=True)
    run = _run(i.goal, target, base_reached, w, gapped)
    return IncomeGap(
        available=True,
        months=n,
        reached=run.reached,
        delay_months=run.delay_months,
        not_until=run.not_until,
        drawn=w.drawn,
        used_up=run.used_up,
    )


class RepairReason(StrEnum):
    NO_PROPERTY = "no_property"  # no property in the walk
    NO_LIVING_AREA = "no_living_area"  # no property with a stored living area
    NO_RATE = "no_rate"  # risks.yaml holds no rate for the year or before it


@dataclass(frozen=True)
class PlanTodo:
    """A to-do of the page (the property's living area); a plain code, never a HealthTodo, so
    no schema moves for it."""

    code: str
    property: Hashable
    action: str
    href: str


LIVING_AREA_MISSING = "plan.living_area_missing"


@dataclass(frozen=True)
class Repair:
    available: bool
    reason: RepairReason | None = None
    per_m2: Decimal | None = None
    years: int | None = None
    by_property: dict[Hashable, Decimal] = field(default_factory=dict)
    amount: Decimal | None = None
    reached: str | None = None
    delay_months: int | None = None
    not_until: str | None = None
    used_up: str | None = None


def repair_amounts(
    properties: Sequence[PlanProperty], per_m2: Decimal, years: int
) -> tuple[dict[Hashable, Decimal], list[PlanTodo]]:
    """Per property with a living area, area × rate × years to the cent; a to-do for one
    without."""
    amounts: dict[Hashable, Decimal] = {}
    todos = []
    for prop in properties:
        if prop.living_area is None or prop.living_area <= 0:
            todos.append(
                PlanTodo(
                    code=LIVING_AREA_MISSING,
                    property=prop.key,
                    action="Wohnfläche eintragen",
                    href=prop.href or f"/properties/{prop.key}",
                )
            )
        else:
            amounts[prop.key] = (prop.living_area * per_m2 * years).quantize(CENT, ROUND_HALF_UP)
    return amounts, todos


def repair(
    i: PlanInputs, route: Route, target: Decimal, base_reached: str | None, rules: PlanRules
) -> tuple[Repair, list[PlanTodo]]:
    p = route.projection
    first = add_months(p.basis.newest_closed, 1)
    year = int(first[:4])
    per_m2 = maintenance_per_m2(year, rules)
    years = rules.repair_years
    owned = [
        prop
        for prop in i.properties
        if prop.key in p.flats
        and (p.flats[prop.key].sale_year is None or p.flats[prop.key].sale_year >= year)
    ]
    if not owned:
        return Repair(
            available=False, reason=RepairReason.NO_PROPERTY, per_m2=per_m2, years=years
        ), []
    if per_m2 is None:
        return Repair(available=False, reason=RepairReason.NO_RATE, years=years), []
    amounts, todos = repair_amounts(owned, per_m2, years)
    if not amounts:
        return Repair(
            available=False, reason=RepairReason.NO_LIVING_AREA, per_m2=per_m2, years=years
        ), todos
    amount = sum(amounts.values(), ZERO)
    one_offs = [*p.levers.one_offs, OneOff(month=first, amount=-amount)]
    paid = replace(p, levers=p.levers.model_copy(update={"one_offs": one_offs}))
    run = _run(i.goal.goal, target, base_reached, replace(route, projection=paid).walk(), paid)
    return Repair(
        available=True,
        per_m2=per_m2,
        years=years,
        by_property=amounts,
        amount=amount,
        reached=run.reached,
        delay_months=run.delay_months,
        not_until=run.not_until,
        used_up=run.used_up,
    ), todos


# --- The seven cards --------------------------------------------------------------------------


@dataclass(frozen=True)
class GoalCard:
    kind: GoalKind
    target: Decimal | None
    due_month: str
    word: GoalWord | None
    value_today: Decimal | None
    percent: Decimal | None


@dataclass(frozen=True)
class HeuteCard:
    net_worth: Decimal
    reserve: Decimal
    reserve_months: Decimal | None  # the reserve in months of its need, to 0.1 half-up
    reserve_full: bool | None  # at or above its target
    saved_mean: Decimal
    reserve_accounts: list[str]
    above_target: Decimal | None = None  # the plan's „Anlegbar jetzt“, read, never computed here
    # §7.13 (d) 2: „X € von Y €“ with „erreicht“ or „noch Z € bis zum Ziel“; None without a target
    reserve_target: Decimal | None = None
    reserve_gap: Decimal | None = None  # max(0, target − reserve)


@dataclass(frozen=True)
class WantRow:
    code: AllocationCategory
    from_: Decimal  # its mean („from“)
    to: Decimal  # its budget on the route


@dataclass(frozen=True)
class PlanCard:
    on_track: bool
    reached: str | None
    without_route: Decimal | None
    gap: Decimal | None
    saving_extra: Decimal | None
    by_class: dict[str, Decimal]
    cut: Decimal | None
    wants: list[WantRow]


@dataclass(frozen=True)
class PropertyAssumption:
    key: Hashable
    kept: bool
    sale_year: int | None
    value_change: Decimal | None


@dataclass(frozen=True)
class AssumptionsCard:
    rates: dict[str, Decimal | None]  # depot, crypto, reserve as the route walks them
    properties: list[PropertyAssumption]


@dataclass(frozen=True)
class RisksCard:
    base_reached: str | None
    weak_markets: WeakMarkets
    income_gap: IncomeGap
    repair: Repair
    todos: list[PlanTodo]


@dataclass(frozen=True)
class OtherRoutes:
    more_saving: Decimal | None  # route 2's extra
    needed_return: Decimal | None  # route 3's return, when it is available
    less_spending: Decimal | None  # route 4's cut (the chosen scenario, else „Nur Wünsche“)


class StepKind(StrEnum):
    INVEST = "invest"  # „mehr anlegen“
    WANTS_BUDGET = "wants_budget"  # „Wünsche-Budget“


@dataclass(frozen=True)
class Step:
    kind: StepKind
    amount: Decimal
    done: bool


@dataclass(frozen=True)
class ThisMonth:
    steps: list[Step]
    done: int
    of: int


@dataclass(frozen=True)
class PlanCards:
    """„Euer Plan“ „Für euch“: the answer's issue says why only the goal card is there."""

    goal: GoalCard
    heute: HeuteCard | None = None
    plan: PlanCard | None = None
    assumptions: AssumptionsCard | None = None
    risks: RisksCard | None = None
    other_routes: OtherRoutes | None = None
    this_month: ThisMonth | None = None


def split_amount(amount: Decimal, shares: Mapping[PlanClass, Decimal]) -> dict[str, Decimal]:
    """Reading 6: amount × share ÷ 100 to the cent half-up, the last class with a share taking
    the rest; a class with share 0 is not named."""
    named = [(c, s) for c, s in shares.items() if s > 0]
    out: dict[str, Decimal] = {}
    rest = amount
    for n, (c, s) in enumerate(named):
        if n == len(named) - 1:
            out[c.value] = rest
        else:
            part = (amount * s / 100).quantize(CENT, ROUND_HALF_UP)
            out[c.value] = part
            rest -= part
    return out


def _wants_total(i: GoalInputs) -> Decimal:
    return sum(
        (m for c, m in i.code_means.items() if m > 0 and i.classes.get(c) is BudgetClass.WANT),
        ZERO,
    )


def plan_cards(i: PlanInputs, answer: HouseholdGoal | None = None) -> PlanCards:
    gi = i.goal
    answer = household_goal(gi) if answer is None else answer
    goal = gi.goal
    goal_card = GoalCard(
        kind=goal.kind,
        target=answer.target,
        due_month=goal.due_month,
        word=answer.word,
        value_today=answer.value_today,
        percent=answer.percent,
    )
    route = route_of(gi, answer)
    if answer.word is None or route is None or answer.target is None:
        return PlanCards(goal=goal_card)
    target = answer.target
    rules = plan_rules()
    p = route.projection
    reserve_target = p.target
    heute = HeuteCard(
        net_worth=i.net_worth_today,
        reserve=i.reserve_today,
        reserve_months=(
            None
            if gi.need <= 0
            else (i.reserve_today / gi.need).quantize(MONTHS_PLACES, ROUND_HALF_UP)
        ),
        reserve_full=None if reserve_target is None else i.reserve_today >= reserve_target,
        reserve_target=reserve_target,
        reserve_gap=(
            None if reserve_target is None else max(ZERO, reserve_target - i.reserve_today)
        ),
        saved_mean=gi.heute,
        reserve_accounts=list(i.reserve_accounts),
        above_target=i.above_target,
    )
    base = route.walk()
    base_reached = reached_in(goal, base, target)
    on_track = answer.word is GoalWord.ON_TRACK
    b = None if on_track else answer.balanced
    extra = None if b is None else b.saving_extra
    shares = (
        {} if p.split is None or extra is None else {c: getattr(p.split, c.value) for c in CLASSES}
    )
    plan = PlanCard(
        on_track=on_track,
        reached=base_reached,
        without_route=answer.projected,
        gap=(
            None if on_track or answer.projected is None else max(ZERO, target - answer.projected)
        ),
        saving_extra=extra,
        by_class={} if extra is None else split_amount(extra, shares),
        cut=None if b is None else b.cut,
        wants=(
            []
            if b is None
            else [WantRow(code=x.code, from_=x.mean, to=x.budget) for x in b.budgets]
        ),
    )
    vc = p.levers.value_change
    assumptions = AssumptionsCard(
        rates={"depot": vc.depot, "crypto": vc.crypto, "reserve": vc.reserve},
        properties=[
            PropertyAssumption(
                key=key,
                kept=f.kept,
                sale_year=f.sale_year,
                value_change=next((x.value_change for x in i.properties if x.key == key), None),
            )
            for key, f in p.flats.items()
        ],
    )
    months_left = answer.months_left or 0
    rep, todos = repair(i, route, target, base_reached, rules)
    risks = RisksCard(
        base_reached=base_reached,
        weak_markets=weak_markets(gi, route, target, base_reached, months_left),
        income_gap=income_gap(gi, route, target, base_reached, rules),
        repair=rep,
        todos=todos,
    )
    chosen = None if answer.choice is None else answer.choice.spending
    scenarios = {s.kind: s for s in answer.scenarios}
    less = scenarios.get(chosen) or scenarios.get(GoalSpending.WANTS_ONLY)
    more_return = answer.more_return
    other = OtherRoutes(
        more_saving=None if answer.more_saving is None else answer.more_saving.extra,
        needed_return=(
            more_return.needed_return if more_return is not None and more_return.available else None
        ),
        less_spending=None if less is None else less.cut,
    )
    steps = []
    if b is not None:
        if extra is not None and extra > 0:
            steps.append(
                Step(kind=StepKind.INVEST, amount=extra, done=i.invested_this_month >= extra)
            )
        budget = _wants_total(gi) - b.cut
        if b.cut > 0:
            steps.append(Step(kind=StepKind.WANTS_BUDGET, amount=budget, done=i.budget_adopted))
    this_month = ThisMonth(steps=steps, done=sum(1 for s in steps if s.done), of=len(steps))
    return PlanCards(
        goal=goal_card,
        heute=heute,
        plan=plan,
        assumptions=assumptions,
        risks=risks,
        other_routes=other,
        this_month=this_month,
    )


__all__ = [
    "LIVING_AREA_MISSING",
    "AssumptionsCard",
    "GoalCard",
    "HeuteCard",
    "IncomeGap",
    "OtherRoutes",
    "PlanCard",
    "PlanCards",
    "PlanInputs",
    "PlanProperty",
    "PlanRules",
    "PlanTodo",
    "PropertyAssumption",
    "Repair",
    "RepairReason",
    "RiskRun",
    "RisksCard",
    "Route",
    "Step",
    "StepKind",
    "ThisMonth",
    "WantRow",
    "WeakMarkets",
    "gap_changes",
    "income_gap",
    "maintenance_per_m2",
    "plan_cards",
    "plan_rules",
    "repair",
    "repair_amounts",
    "route_of",
    "split_amount",
    "weak_markets",
    "weak_rates",
]
