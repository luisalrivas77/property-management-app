"""The household goal's figures, word and routes (docs/phases/phase-10.md §7.9 (d), §7.11 (b),
(c); D289, D298, D301–D303). Pure functions over the one month walk of rules/plan/projection
(D301); nothing here calls a model, and nothing proposes a goal, a date, an amount, a rate or
a product (D247, D251, D289 (1)).

- „Heute im Monat“ is the mean monthly saved over the closed window (phase 9's household
  month), passed in.
- The value at a month comes from the walk: the net worth for a net-worth goal, the reserve
  row for a sum saved and a reserve in months; a reserve goal's target is its months × the
  monthly need, and it is walked with nothing moved out of the reserve (no split), since the
  reserve itself is what it counts.
- „Nötig im Monat“ (D302) = Heute + the smallest whole-euro extra which, added to each
  month's „Anlegbar“ from next month to the due month, makes the walk reach the target at the
  due month; found by bisection between 0 and the mean income, with growth; none above it.
  Route 2 „Mehr anlegen“ is that extra (found without the income's limit) and never decides
  the word.
- Route 4, the spending scenarios: each cuts, from next month to the due month, the extra ÷
  (1 − buffer) rounded up to the cent, one cent more when a buffer is kept (phase 9 keeps the
  buffer back from every positive free month, so a cut reaches the walk only at 1 − buffer).
  Each cut code gives the same share of its mean, rounded up to the cent; when the codes
  cannot give the cut, each goes to 0 and the rest is named. It closes the gap when the walk
  with its spending changes reaches the target by the due month.
- „Ausgewogener Weg“ (D302), shown first whenever the walk without change misses: half the
  extra (rounded up to the cent) cut from the wants by their means, each want cut at most to
  half its mean, or with the household's wants target to that share of the income (the room
  Σ wants − target × income shared by the means, each part to the cent down); then the saving
  extra = the smallest whole euro that with those cuts reaches the target, within the mean
  income; what the limit left open moves to saving and is named.
- Route 3 „Mehr Rendite“ (§7.11 (c) 3, D310 amended; rules/goal/history): the one yearly
  return for funds and crypto together that reaches the target by the due month on the walk
  with nothing else changed, smallest to 0.1 % between −50 % and +100 % (by bisection),
  against every window of the goal's months left in whole years in the invested mix's
  history (the reference series in euros), with the median, the best and N of M reached.
  Not offered in `generic` mode (D289), for a goal that is not a net worth, with nothing
  invested and no split, without the funds series, or on a history covering under half of
  the invested value; out of range above +100 %. Under 5 windows it is thin. Since §7.11 (i)
  (D311) it never decides the word and the balanced route always walks the typed rates.
- „Eigene Wahl“ (D289 (3)) is the household's own spending in play (D263) on the Ziel tab; once
  adopted with `goal_choice.spending = own`, it is read like any adopted budget.
- The path (§7.11 (i), D311; rules/goal/path.py): „Auf Kurs“ when the walk without change, or
  with the budget the household adopted, reaches the target by the due month; step 1, the
  smallest whole-euro extra within the room (the ceiling less Anlegbar) at the typed rates;
  step 2 (own use, a net-worth goal), the return needed with the whole room, reached in at
  least half of the windows of the invested mix's history. The word is the path's state:
  on_track „Auf Kurs“, doable_rates and doable_return „Machbar“, ambitious „Ehrgeizig“
  (never adoptable), hardly „Kaum erreichbar“ with the why. Route 4 and route 3 are still
  shown and decide nothing (amends D303).
- A property without a default scenario is kept (D301 (a) 5); the answer names that
  assumption with the link that replaces it.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from hausbuch.rules.household.taxonomy import FALLBACK_CODES, fallback_of
from hausbuch.rules.plan.levers import add_months, months_after
from hausbuch.rules.plan.spending import mean_of, playable
from hausbuch.rules.plan.surplus import ZERO
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.goal import (
    GoalAssumption,
    GoalBalanced,
    GoalBudget,
    GoalIssue,
    GoalMoreReturn,
    GoalMoreSaving,
    GoalPath,
    GoalPathWhy,
    GoalPoint,
    GoalReferenceSource,
    GoalRouteReason,
    GoalSale,
    GoalScenario,
    GoalUnreachable,
    GoalWord,
    HouseholdGoal,
)
from hausbuch.schemas.health import HealthTodo
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import (
    ADOPTABLE_STATES,
    Goal,
    GoalChoice,
    GoalKind,
    GoalPathState,
    GoalSpending,
    SpendingChange,
)

from . import history as H
from . import path as PA
from .walk import Projection, Walk, last_month, walk

CENT = Decimal("0.01")
TENTH = Decimal("0.1")
# Where a property's default scenario is made (Anlagen › Immobilie, D288).
PROPERTY_SCENARIO_HREF = "/assets?tab=immobilie"
# Route 2's extra, found without the income's limit, stops looking here (a goal no saving
# reaches within the walk).
EXTRA_CEILING = Decimal(10) ** 9


@dataclass(frozen=True)
class GoalInputs:
    """What the goal is computed from. `projection` is None when phase 9's projection has
    to-dos (`todos`); `code_means` are the plan's per-code means (rules/plan/spending), and
    `classes` each code's class as the Budget shows it (D272, the household's moves too)."""

    today: date
    goal: Goal
    choice: GoalChoice | None
    heute: Decimal
    value_today: Decimal  # the net worth today, or the reserve accounts today
    need: Decimal  # the reserve's monthly need (D238)
    code_means: Mapping[AllocationCategory, Decimal]
    classes: Mapping[AllocationCategory, BudgetClass]
    household_changes: Sequence[SpendingChange]  # the default scenario's spending changes
    projection: Projection | None
    todos: Sequence[HealthTodo] = ()
    mean_income: Decimal = ZERO  # the window's mean income, the bisection's upper end (D302)
    wants_max: Decimal | None = None  # the household's wants target, % of income (D274)
    mode: AdviceMode = AdviceMode.OWN_USE
    # Route 3 (D310 amended): today's invested values and the reference series in euros
    invested: H.Invested = H.Invested()
    history: H.History = H.History()


def none_set() -> HouseholdGoal:
    """Without a goal the card reads „Ziel festlegen“."""
    return HouseholdGoal(word=GoalWord.NONE)


def current_month(today: date) -> str:
    return f"{today.year:04d}-{today.month:02d}"


def target_of(goal: Goal, need: Decimal) -> Decimal:
    """Euros: a reserve goal's months × the monthly need, to the cent."""
    if goal.kind is GoalKind.RESERVE_MONTHS:
        return (goal.target * need).quantize(CENT, ROUND_HALF_UP)
    return goal.target


def value_of(goal: Goal, month) -> Decimal:
    return month.net_worth if goal.kind is GoalKind.NET_WORTH else month.reserve


PERCENT_CAP = Decimal("999999.9")


def percent(value: Decimal, target: Decimal) -> Decimal:
    if target <= 0:
        return Decimal("0.0")
    share = max(ZERO, value) * 100 / target
    # a display share: a tiny target (1 €) would pass the schema's 7 integer digits
    return min(share.quantize(TENTH, ROUND_HALF_UP), PERCENT_CAP)


def reached_in(goal: Goal, w: Walk, target: Decimal, until: str | None = None) -> str | None:
    """The first walked month at or above the target (up to `until`), else None."""
    for m in w.months:
        if until is not None and m.month > until:
            return None
        if value_of(goal, m) >= target:
            return m.month
    return None


def household_goal(i: GoalInputs) -> HouseholdGoal:
    goal = i.goal
    head = {"goal": goal, "choice": i.choice}
    if goal.kind is GoalKind.RESERVE_MONTHS and i.need <= 0:
        return HouseholdGoal(word=None, issue=GoalIssue.NO_NEED, **head)
    target = target_of(goal, i.need)
    known = {
        **head,
        "target": target,
        "value_today": i.value_today,
        "percent": percent(i.value_today, target),
        "heute": i.heute,
    }
    left = months_after(current_month(i.today), goal.due_month)
    if left <= 0:
        return HouseholdGoal(word=None, issue=GoalIssue.DUE_PASSED, **known)
    known["months_left"] = left
    p = i.projection
    if p is None:
        return HouseholdGoal(word=None, issue=GoalIssue.NO_PROJECTION, todos=list(i.todos), **known)
    if goal.kind is not GoalKind.NET_WORTH:
        p = replace(p, split=None)  # the reserve itself is counted: nothing moves out of it
    if goal.due_month > last_month(p):  # forty years after the newest closed month
        return HouseholdGoal(word=None, issue=GoalIssue.BEYOND_PROJECTION, **known)
    known["assumptions"] = _assumptions(p)
    as_is = walk(p)
    due = as_is.at(goal.due_month)
    if due is None:  # the reserve runs out on the way
        return HouseholdGoal(word=None, issue=GoalIssue.USED_UP, used_up=as_is.used_up, **known)
    projected = value_of(goal, due)
    first = add_months(current_month(i.today), 1)
    reaches = _reaches(i, p, target, first)
    extra = _smallest(lambda e: reaches(e, ()), EXTRA_CEILING, grow=True)
    noetig = None if extra is None or extra > i.mean_income else i.heute + extra
    known |= {
        "projected": projected,
        "short_at_due": target - projected if projected < target else None,
        "sales": _sales(p),
        "noetig": noetig,
        "reached": reached_in(goal, as_is, target),
        "used_up": as_is.used_up,
        "more_saving": None if extra is None else GoalMoreSaving(extra=extra, noetig=noetig),
    }
    more_return, _median = _more_return(i, p, target, left)
    known["more_return"] = more_return
    with_budget = None
    if i.choice is not None and i.choice.spending is not None and i.household_changes:
        adopted = walk(p, i.household_changes, until=goal.due_month)
        with_budget = reached_in(goal, adopted, target, until=goal.due_month)
        if with_budget is None:  # the adopted budget is walked to the due month only
            with_budget = reached_in(goal, walk(p, i.household_changes), target)
    known["reached_with_budget"] = with_budget
    on_track = projected >= target or (with_budget is not None and with_budget <= goal.due_month)
    scenarios: list[GoalScenario] = []
    balanced_walk: Walk | None = None
    if projected < target:
        scenarios = _scenarios(i, p, target, extra, first)
        # D311: one growth assumption for every goal, the typed rates (route 3 is shown beside)
        bp, balanced = p, _balanced(i, reaches, extra, first)
        if balanced is not None and balanced.saving_extra is not None:
            balanced_walk = walk(
                bp,
                balanced.spending_changes,
                until=goal.due_month,
                extra=balanced.saving_extra,
                extra_from=first,
            )
            balanced = balanced.model_copy(
                update={"reached": reached_in(goal, balanced_walk, target, goal.due_month)}
            )
        known["balanced"] = balanced
    known["path"] = _path(goal, as_is, balanced_walk)
    plan = _plan_path(i, p, target, left, on_track, reaches, first)
    known["goal_path"] = plan
    word = WORD_OF[plan.state]
    unreachable = (
        GoalUnreachable(later=known["reached"], reaches_by_due=projected)
        if word is GoalWord.HARDLY
        else None
    )
    return HouseholdGoal(word=word, scenarios=scenarios, unreachable=unreachable, **known)


# --- The path (§7.11 (i), D311) -------------------------------------------------------------

WORD_OF = {
    GoalPathState.ON_TRACK: GoalWord.ON_TRACK,
    GoalPathState.DOABLE_RATES: GoalWord.FEASIBLE,
    GoalPathState.DOABLE_RETURN: GoalWord.FEASIBLE,
    GoalPathState.AMBITIOUS: GoalWord.AMBITIOUS,
    GoalPathState.HARDLY: GoalWord.HARDLY,
}


def _plan_path(
    i: GoalInputs,
    p: Projection,
    target: Decimal,
    left: int,
    on_track: bool,
    reaches: "Reaches",
    first: str,
) -> GoalPath:
    """Step 1 at the typed rates within the room, else step 2 the return needed with the whole
    room against the invested mix's history, else why (rules/goal/path.py)."""
    b = p.basis
    wants = {c: m for c, m in _codes(i).items() if i.classes[c] is BudgetClass.WANT}
    net = wants_net(i)
    ceiling = PA.ceiling_of(
        b.mean_income,
        b.mean_regular_spending,
        b.mean_rental_cash,
        i.need,
        wants,
        net,
        i.wants_max,
        b.buffer_percent,
    )
    anlegbar = PA.anlegbar_of(
        b.mean_income, b.mean_regular_spending, b.mean_rental_cash, b.buffer_percent
    )
    room = PA.room_of(ceiling, anlegbar)
    typed = PA.typed_rate(p.levers, p.split)
    head = {"ceiling": ceiling, "anlegbar": anlegbar, "room": room, "typed_rate": typed}
    step: dict = {}
    if on_track:
        state, extra = GoalPathState.ON_TRACK, ZERO
    else:
        extra = _smallest(lambda e: reaches(e, ()), Decimal(math.floor(room)))
        state = GoalPathState.DOABLE_RATES
    if extra is None:  # step 1 misses within the room
        state, step = _step_2(i, p, target, left, room, first)
        extra = room if state is GoalPathState.DOABLE_RETURN else None
    if extra is None:
        due = i.goal.due_month
        with_room = walk(p, extra=room, extra_from=first)
        month = with_room.at(due)
        why = GoalPathWhy(
            due_value=ZERO if month is None else value_of(i.goal, month),
            reached=reached_in(i.goal, with_room, target),
        )
        return GoalPath(state=state, adoptable=False, why=why, **head, **step)
    cuts = PA.wants_cuts(wants, PA.cuttable(net, ceiling.wants_kept), extra, b.buffer_percent)
    budgets = [
        GoalBudget(code=c, budget_class=BudgetClass.WANT, mean=wants[c], budget=wants[c] - cuts[c])
        for c in sorted(cuts, key=lambda c: c.value)
    ]
    return GoalPath(
        state=state,
        extra=extra,
        monthly=anlegbar + extra,
        rate=typed if step.get("return_needed") is None else step["return_needed"],
        wants_budget=budgets,
        # never below 0: refunds above the wants of the window leave a budget of 0, not less
        wants_budget_total=max(net, ZERO) - sum(cuts.values(), ZERO),
        spending_changes=_changes(i.code_means, cuts, first, i.goal.due_month),
        adoptable=True,
        **head,
        **step,
    )


def path_months(i: GoalInputs, path: GoalPath) -> tuple[tuple[str, Decimal], ...]:
    """The path's walk to the due month, (YYYY-MM, net worth) per month, for adopting it
    (§7.11 (i) item 6): step 1 at the typed rates with its extra, step 2 at the return needed
    with the whole room, both without spending changes (the extra is the cut, D311); on track
    with the household's adopted budget when it has one. Empty without a projection."""
    p = i.projection
    if p is None or path.state not in ADOPTABLE_STATES:
        return ()
    if i.goal.kind is not GoalKind.NET_WORTH:
        p = replace(p, split=None)
    if path.state is GoalPathState.DOABLE_RETURN:
        assert path.return_needed is not None
        p = _at_rate(p, path.return_needed)
    budget = i.choice is not None and i.choice.spending is not None
    changes = i.household_changes if path.state is GoalPathState.ON_TRACK and budget else ()
    first = add_months(current_month(i.today), 1)
    w = walk(p, changes, until=i.goal.due_month, extra=path.extra or ZERO, extra_from=first)
    return tuple((m.month, m.net_worth) for m in w.months)


def _step_2(
    i: GoalInputs, p: Projection, target: Decimal, left: int, room: Decimal, first: str
) -> tuple[GoalPathState, dict]:
    """Step 2 (own use, a net-worth goal): route 3's needed return with the whole room."""
    if i.mode is AdviceMode.GENERIC or i.goal.kind is not GoalKind.NET_WORTH:
        return GoalPathState.HARDLY, {}
    years = H.window_years(left)
    mix = H.mix_of(i.invested, p.split, i.history, years)
    if isinstance(mix, H.NoCount):
        return GoalPathState.HARDLY, {}
    products = H.window_products(mix.returns, years)
    needed = _needed_return(i, p, target, room, first)
    reached = None if needed is None else H.reached(needed, products, years)
    shown = {
        "return_needed": needed,
        "window_years": years,
        "windows": len(products),
        "reached_windows": reached,
        "short_history": len(products) < H.THIN_BELOW,
        "years": [min(mix.returns), max(mix.returns)] if mix.returns else None,
    }
    return PA.step_2_state(reached, len(products)), shown


# --- Route 3 „Mehr Rendite“ -------------------------------------------------------------------

RATE_LOW, RATE_HIGH = -500, 1000  # thousandths: −50 % … +100 % a year


def _at_rate(p: Projection, rate: Decimal) -> Projection:
    """The walk with one yearly rate for funds and crypto (the reserve's own rate kept). Not
    validated: a rate above the typed lever's ±50 % is a calculation here, never a lever."""
    vc = p.levers.value_change.model_copy(update={"depot": rate, "crypto": rate})
    return replace(p, levers=p.levers.model_copy(update={"value_change": vc}))


def _needed_return(
    i: GoalInputs,
    p: Projection,
    target: Decimal,
    extra: Decimal = ZERO,
    first: str | None = None,
) -> Decimal | None:
    """The smallest rate in thousandths that reaches the target at the due month with nothing
    else changed (or with `extra` a month from `first`, the path's step 2); −50 % when even
    that reaches it, None when +100 % does not."""
    due = i.goal.due_month

    def ok(k: int) -> bool:
        w = walk(_at_rate(p, Decimal(k) / 1000), until=due, extra=extra, extra_from=first)
        month = w.at(due)
        return month is not None and value_of(i.goal, month) >= target

    return smallest_rate(ok)


def smallest_rate(ok: Callable[[int], bool]) -> Decimal | None:
    """The smallest yearly rate in thousandths between −50 % and +100 % for which `ok` holds
    (`ok` rising with the rate), by bisection: −50 % when even that holds, None when +100 %
    does not. Route 3's needed return and the property scenario's needed growth."""
    if ok(RATE_LOW):
        return (Decimal(RATE_LOW) / 1000).quantize(H.RETURN_PLACES)
    if not ok(RATE_HIGH):
        return None
    lo, hi = RATE_LOW, RATE_HIGH
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return (Decimal(hi) / 1000).quantize(H.RETURN_PLACES)


def _more_return(
    i: GoalInputs, p: Projection, target: Decimal, left: int
) -> tuple[GoalMoreReturn, Decimal | None]:
    """Route 3 and the rate the balanced route walks at (its median, only when it decides)."""
    if i.mode is AdviceMode.GENERIC:
        return GoalMoreReturn(reason=GoalRouteReason.GENERIC_MODE), None
    if i.goal.kind is not GoalKind.NET_WORTH:  # a sum saved or the reserve: nothing invested
        return GoalMoreReturn(reason=GoalRouteReason.NOTHING_INVESTED), None
    years = H.window_years(left)
    mix = H.mix_of(i.invested, p.split, i.history, years)
    if isinstance(mix, H.NoCount):
        return GoalMoreReturn(reason=mix.reason, left_out=list(mix.left_out)), None
    products = H.window_products(mix.returns, years)
    median = H.annualised(H.median_of(products), years)
    thin = len(products) < H.THIN_BELOW
    shown = {
        "window_years": years,
        "windows": len(products),
        "median": median,
        "best": H.annualised(max(products), years),
        "thin": thin,
        "left_out": list(mix.left_out),
        "sources": [
            GoalReferenceSource(
                key=s.key,
                source=s.source,
                url=s.url,
                licence=s.licence,
                attribution=s.attribution,
                read_on=s.read_on,
                first_year=min(s.returns),
                last_year=max(s.returns),
            )
            for s in mix.series
        ],
    }
    needed = _needed_return(i, p, target)
    if needed is None:
        return GoalMoreReturn(reason=GoalRouteReason.OUT_OF_RANGE, **shown), None
    route = GoalMoreReturn(
        available=True,
        needed_return=needed,
        reached_windows=H.reached(needed, products, years),
        verdict=H.verdict_of(needed, products, years),
        **shown,
    )
    return route, None if thin else median


def _sales(p: Projection) -> list[GoalSale]:
    """Each property the walk sells, by its sale year (D301 (a) 4)."""
    return sorted(
        (
            GoalSale(year=f.sale_year, net_proceeds=f.net_proceeds)
            for f in p.flats.values()
            if f.sale_year is not None
        ),
        key=lambda s: s.year,
    )


def _path(goal: Goal, as_is: Walk, balanced: Walk | None) -> list[GoalPoint]:
    """The chart's points up to the due month: the first walked month, each December before
    the due month and the due month, from the walk without change and the balanced route's."""
    due = goal.due_month
    months = [
        m
        for k, m in enumerate(as_is.months)
        if m.month <= due and (k == 0 or m.month == due or m.month.endswith("-12"))
    ]
    out: list[GoalPoint] = []
    for m in months:
        other = None if balanced is None else balanced.at(m.month)
        out.append(
            GoalPoint(
                month=m.month,
                as_is=value_of(goal, m).quantize(CENT, ROUND_HALF_UP),
                balanced=None
                if other is None
                else value_of(goal, other).quantize(CENT, ROUND_HALF_UP),
            )
        )
    return out


def _assumptions(p: Projection) -> list[GoalAssumption]:
    if not any(f.kept for f in p.flats.values()):
        return []
    return [
        GoalAssumption(
            text="Annahme: Wohnung wird behalten, Wert unverändert.",
            action="Szenario anlegen",
            href=PROPERTY_SCENARIO_HREF,
        )
    ]


Reaches = Callable[[Decimal, Sequence[SpendingChange]], bool]


def _reaches(i: GoalInputs, p: Projection, target: Decimal, first: str) -> Reaches:
    """Whether the walk with `extra` a month from next month and the spending `changes` reaches
    the target at the due month."""
    due = i.goal.due_month

    def reaches(extra: Decimal, changes: Sequence[SpendingChange]) -> bool:
        w = walk(p, changes, until=due, extra=extra, extra_from=first)
        month = w.at(due)
        return month is not None and value_of(i.goal, month) >= target

    return reaches


def _smallest(fn: Callable[[Decimal], bool], hi: Decimal, grow: bool = False) -> Decimal | None:
    """The smallest whole euro e in [0, hi] with fn(e), fn growing with e; None when fn(hi)
    fails. With `grow`, the upper end starts at 1,000 and doubles up to `hi`."""
    if fn(ZERO):
        return ZERO
    lo = 0  # fn(lo) is false
    top = min(Decimal(1000), hi) if grow else hi
    while not fn(top):
        if not grow or top >= hi:
            return None
        lo = int(top)
        top = min(top * 2, hi)
    up = math.ceil(top)
    while up - lo > 1:
        mid = (lo + up) // 2
        if fn(Decimal(mid)):
            up = mid
        else:
            lo = mid
    return Decimal(up).quantize(CENT)


# --- The spending scenarios ---------------------------------------------------------------


def _scenarios(
    i: GoalInputs, p: Projection, target: Decimal, extra: Decimal | None, first: str
) -> list[GoalScenario]:
    codes = _codes(i)
    if extra is None:  # no saving reaches it within the walk: every code to 0
        wanted = sum(codes.values(), ZERO) + CENT
    else:
        buffer = p.basis.buffer_percent
        wanted = _ceil_cent(Fraction(extra) / (1 - Fraction(buffer) / 100))
        wanted += CENT if buffer > 0 else ZERO
    out = []
    for kind, chosen in (
        (GoalSpending.WANTS_ONLY, {c for c in codes if i.classes[c] is BudgetClass.WANT}),
        (GoalSpending.ALL_EQUAL, set(codes)),
    ):
        out.append(_cut(i, p, target, kind, {c: codes[c] for c in chosen}, wanted, first))
    return out


def wants_net(i: GoalInputs) -> Decimal:
    """The want class's net mean: every want code's mean, a refund (negative) and a code
    netting to 0 included, as the Budget's Wünsche line nets them (rules/budget, D273)."""
    return sum((m for c, m in i.code_means.items() if i.classes.get(c) is BudgetClass.WANT), ZERO)


def _codes(i: GoalInputs) -> dict[AllocationCategory, Decimal]:
    # The means hold no one-off code in force (rules/plan/spending.month_spending), so only the
    # structural test is left here (phase 10 D307: a switch decides the one-offs).
    return {c: m for c, m in i.code_means.items() if m > 0 and playable(c, ()) and c in i.classes}


def _balanced(
    i: GoalInputs, reaches: Reaches, extra: Decimal | None, first: str
) -> GoalBalanced | None:
    """„Ausgewogener Weg“: half of the extra from the wants, within their limit, the rest
    saved."""
    if extra is None:
        return None
    wants = {c: m for c, m in _codes(i).items() if i.classes[c] is BudgetClass.WANT}
    total = sum(wants.values(), ZERO)
    half = _ceil_cent(Fraction(extra) / 2)
    cuts: dict[AllocationCategory, Decimal] = {}
    if total > 0:
        if i.wants_max is None:
            limits = {c: _floor_cent(Fraction(m) / 2) for c, m in wants.items()}
        else:
            room = max(ZERO, total - i.wants_max * i.mean_income / 100)
            limits = {
                c: _floor_cent(Fraction(room) * Fraction(m) / Fraction(total))
                for c, m in wants.items()
            }
        for code, mean in wants.items():
            share = _ceil_cent(Fraction(mean) * Fraction(half) / Fraction(total))
            if (cut := min(limits[code], share)) > 0:
                cuts[code] = cut
    cut = sum(cuts.values(), ZERO)
    changes = _changes(i.code_means, cuts, first, i.goal.due_month)
    saving_extra = _smallest(lambda e: reaches(e, changes), i.mean_income)
    return GoalBalanced(
        budgets=[
            GoalBudget(code=c, budget_class=i.classes[c], mean=wants[c], budget=wants[c] - cuts[c])
            for c in sorted(cuts, key=lambda c: c.value)
        ],
        cut=cut,
        saving_extra=saving_extra,
        moved_to_saving=ZERO if saving_extra is None else max(ZERO, saving_extra - half),
        saving=None if saving_extra is None else i.heute + saving_extra,
        closes=saving_extra is not None,
        spending_changes=changes,
    )


def _cut(
    i: GoalInputs,
    p: Projection,
    target: Decimal,
    kind: GoalSpending,
    means: Mapping[AllocationCategory, Decimal],
    wanted: Decimal,
    first: str,
) -> GoalScenario:
    total = sum(means.values(), ZERO)
    cuts: dict[AllocationCategory, Decimal] = {}
    for code, mean in means.items():
        share = (
            mean
            if wanted >= total
            else _ceil_cent(Fraction(mean) * Fraction(wanted) / Fraction(total))
        )
        cuts[code] = min(mean, share)
    given = sum(cuts.values(), ZERO)
    budgets = [
        GoalBudget(code=c, budget_class=i.classes[c], mean=means[c], budget=means[c] - cuts[c])
        for c in sorted(means, key=lambda c: c.value)
    ]
    changes = _changes(i.code_means, cuts, first, i.goal.due_month)
    return GoalScenario(
        kind=kind,
        closes=_closes(i, p, target, changes),
        cut=given,
        rest=max(ZERO, wanted - given) if wanted > total else ZERO,
        saving=i.heute + given,
        budgets=budgets,
        spending_changes=changes,
    )


def _changes(
    code_means: Mapping[AllocationCategory, Decimal],
    cuts: Mapping[AllocationCategory, Decimal],
    first: str,
    due: str,
) -> list[SpendingChange]:
    """D263 planned amounts from `first` to the due month: one per cut code, or one per group
    whose fallback code has spending of its own (a fallback stands for its whole group, and one
    beside its own subcategory is refused, §7.6 (1)); sorted by code."""
    planned_by: dict[AllocationCategory, Decimal] = {}
    for code, cut in cuts.items():
        group = code if code in FALLBACK_CODES else fallback_of(code)
        if group is not None and code_means.get(group, ZERO) > 0:
            planned_by.setdefault(group, mean_of(group, code_means))
            planned_by[group] -= cut
        else:
            planned_by[code] = code_means[code] - cut
    return [
        SpendingChange(code=code, monthly=amount, from_month=first, to_month=due)
        for code, amount in sorted(planned_by.items(), key=lambda kv: kv[0].value)
    ]


def _closes(
    i: GoalInputs, p: Projection, target: Decimal, changes: Sequence[SpendingChange]
) -> bool:
    w = walk(p, changes, until=i.goal.due_month)
    month = w.at(i.goal.due_month)
    return month is not None and value_of(i.goal, month) >= target


def _ceil_cent(value: Fraction) -> Decimal:
    return (Decimal(math.ceil(value * 100)) / 100).quantize(CENT)


def _floor_cent(value: Fraction) -> Decimal:
    return (Decimal(math.floor(value * 100)) / 100).quantize(CENT)


__all__ = [
    "GoalInputs",
    "current_month",
    "household_goal",
    "none_set",
    "target_of",
]
