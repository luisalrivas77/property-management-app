"""The household goal's figures, word and spending scenarios (docs/phases/phase-10.md §7.9 (d),
D289, D298). Pure functions over what services.plan computes for phase 9's projection; nothing
here calls a model, and nothing proposes a goal, a date or an amount (D289 (1)).

- „Heute im Monat“ is the mean monthly saved over the closed window (phase 9's household
  month), passed in.
- The value at a month comes from rules/goal/walk.py: the net worth on phase 9's projection
  (D247) for a net-worth goal, the reserve's as-is row (D237) for a sum saved and a reserve in
  months; a reserve goal's target is its months × the monthly need.
- „Nötig im Monat“ = Heute + (target − the value at the due month without change) ÷ the months
  left, the months from the current month to the due month, to the cent half-up.
- A spending scenario cuts, from next month to the due month, gap ÷ months left ÷ (1 − buffer)
  rounded up to the cent, one cent more when a buffer is kept (phase 9 keeps the buffer back
  from every positive free month, so a cut reaches the projection only at 1 − buffer). Each
  cut code gives the same share of its mean, rounded up to the cent; when the codes cannot
  give the cut, each goes to 0 and the rest is named. It closes the gap when the walk with its
  spending changes reaches the target by the due month.
- „Eigene Wahl“ (D289 (3)) is the household's own spending in play (D263) on the Ziel tab; once
  adopted with `goal_choice.spending = own`, it is read like any adopted budget.
- The word: „Auf Kurs“ when the walk without change, or with the budget the household
  adopted, reaches the target by the due month; „Dahinter“ when a spending scenario closes the
  gap; „So nicht erreichbar“ otherwise (also when the gap is smaller than the month's regular
  spending but no scenario reaches the projection in time: the word never says „Dahinter“
  without a scenario that closes it).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from hausbuch.rules.household.taxonomy import FALLBACK_CODES, fallback_of
from hausbuch.rules.plan.levers import add_months, months_after
from hausbuch.rules.plan.spending import mean_of, playable
from hausbuch.rules.plan.surplus import ZERO
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.goal import (
    GoalBudget,
    GoalIssue,
    GoalScenario,
    GoalUnreachable,
    GoalWord,
    HouseholdGoal,
)
from hausbuch.schemas.health import HealthTodo
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import Goal, GoalChoice, GoalKind, GoalSpending, SpendingChange

from .walk import Projection, Walk, last_month, walk

CENT = Decimal("0.01")
TENTH = Decimal("0.1")


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


def percent(value: Decimal, target: Decimal) -> Decimal:
    if target <= 0:
        return Decimal("0.0")
    share = max(ZERO, value) * 100 / target
    return share.quantize(TENTH, ROUND_HALF_UP)


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
    if goal.due_month > last_month(p):
        return HouseholdGoal(word=None, issue=GoalIssue.BEYOND_PROJECTION, **known)
    as_is = walk(p)
    due = as_is.at(goal.due_month)
    if due is None:  # the reserve runs out on the way
        return HouseholdGoal(word=None, issue=GoalIssue.USED_UP, used_up=as_is.used_up, **known)
    projected = value_of(goal, due)
    gap = target - projected
    known |= {
        "projected": projected,
        "noetig": (i.heute + gap / left).quantize(CENT, ROUND_HALF_UP),
        "reached": reached_in(goal, as_is, target),
        "used_up": as_is.used_up,
    }
    with_budget = None
    if i.choice is not None and i.choice.spending is not None and i.household_changes:
        adopted = walk(p, i.household_changes, until=goal.due_month)
        with_budget = reached_in(goal, adopted, target, until=goal.due_month)
        if with_budget is None:  # the adopted budget is walked to the due month only
            with_budget = reached_in(goal, walk(p, i.household_changes), target)
    known["reached_with_budget"] = with_budget
    on_track = projected >= target or (with_budget is not None and with_budget <= goal.due_month)
    scenarios = [] if gap <= 0 else _scenarios(i, p, target, gap, left)
    if on_track:
        word = GoalWord.ON_TRACK
    elif any(s.closes for s in scenarios):
        word = GoalWord.BEHIND
    else:
        word = GoalWord.UNREACHABLE
    unreachable = (
        GoalUnreachable(later=known["reached"], reaches_by_due=projected)
        if word is GoalWord.UNREACHABLE
        else None
    )
    return HouseholdGoal(word=word, scenarios=scenarios, unreachable=unreachable, **known)


# --- The spending scenarios ---------------------------------------------------------------


def _scenarios(
    i: GoalInputs, p: Projection, target: Decimal, gap: Decimal, left: int
) -> list[GoalScenario]:
    buffer = p.basis.buffer_percent
    cut = Fraction(gap) / left / (1 - Fraction(buffer) / 100)
    wanted = _ceil_cent(cut) + (CENT if buffer > 0 else ZERO)
    first = add_months(current_month(i.today), 1)
    codes = {c: m for c, m in i.code_means.items() if m > 0 and playable(c) and c in i.classes}
    out = []
    for kind, chosen in (
        (GoalSpending.WANTS_ONLY, {c for c in codes if i.classes[c] is BudgetClass.WANT}),
        (GoalSpending.ALL_EQUAL, set(codes)),
    ):
        out.append(_cut(i, p, target, kind, {c: codes[c] for c in chosen}, wanted, first))
    return out


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
    return Decimal(math.ceil(value * 100)) / 100


__all__ = [
    "GoalInputs",
    "current_month",
    "household_goal",
    "none_set",
    "target_of",
]
