"""„Für euer Ziel“ (docs/phases/phase-10.md §7.9 (e), „(e) as decided for the build“ 3; D298
(5)–(7)): the card on Anlagen › Übersicht that follows the return the household chose on
Haushalt › Ziel („Diesem Weg folgen“). Pure, Decimal; services/goal_tracking.py reads the
accounts. The readings, pinned by tests/fixtures/phase10/goal_invest.yaml:

1. Shown in own use only, with a goal and `goal_choice.investment` set; nothing is computed in
   `generic` mode (D289 (7)).
2. The return followed is today's answer of its route, never a stored rate: route 3's needed
   return while it is available, the balanced route's median return while it walks one and
   closes. Otherwise no planned figure and no word (`route_gone`, `goal_issue`).
3. Planned per year: what the walk of that route pays into funds and crypto in the twelve
   months after the current month, the regular pace without the money already above the
   reserve target that its first month moves at once. The walk's payments do not depend on the
   classes' rates, so they are the difference of two walks of the route, at 0 % and with no
   rate (the class then kept at its start). Planned by now = per year × days ÷ 365, to the
   cent.
4. Money in: the external flows of the counted accounts after the day chosen, net, as
   rules/performance counts them; the service leaves a depot counted in the reserve out (D304).
5. Value change = value now − value at the end of the day chosen − money in; implied change =
   Σ a × ((1 + r)^n × (1 + r × rest ÷ 365) − 1) over the value then and each flow, n whole years
   of 365 days and rest the days over, as the walk credits a year's growth at its end.
6. „auf dem Pfad“ when money in ≥ planned by now and value change ≥ implied change, else
   „dahinter“ naming what fell short; a flow without euros or a position without a value
   leaves out what it touches, and the word.
7. A yearly rate only after 365 days (rules/performance MIN_DAYS), money-weighted.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction

from hausbuch.rules.performance import portfolio as R
from hausbuch.rules.plan.levers import add_months
from hausbuch.rules.plan.surplus import ZERO, cent
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.goal import (
    GoalInvestment,
    GoalTracking,
    GoalTrackingReason,
    GoalTrackingShort,
    GoalTrackingWord,
    HouseholdGoal,
)
from hausbuch.schemas.plan import Goal, GoalChoice, GoalPathLever, SpendingChange
from hausbuch.schemas.portfolio import NoRateReason

from .goal import current_month
from .walk import Projection, walk

YEAR_DAYS = 365
Reason = GoalTrackingReason


@dataclass(frozen=True)
class Followed:
    """The route followed as it stands today: its return and what it changes from next month
    (the balanced route's saving extra and spending changes; route 3 changes nothing)."""

    route: GoalInvestment
    rate: Decimal
    spending_changes: tuple[SpendingChange, ...] = ()
    extra: Decimal = ZERO


@dataclass(frozen=True)
class Actual:
    """The counted accounts (funds and crypto, no reserve depot): their value at the end of
    the day chosen and today, their external flows after the day chosen (+ in, − out; None:
    no euros that day), and the positions then or today without a value."""

    value_then: Decimal
    flows: Sequence[tuple[date, Decimal | None]]
    value_now: Decimal
    unvalued: int = 0
    reserve_depots: int = 0


@dataclass(frozen=True)
class TrackerInputs:
    today: date
    mode: AdviceMode
    goal: Goal | None
    choice: GoalChoice | None
    followed: Followed | GoalTrackingReason | None  # followed_of(...); None when not following
    projection: Projection | None
    actual: Actual | None


def route_of(choice: GoalChoice | None) -> GoalInvestment | None:
    """The route the household follows; a stored value this build does not know is none."""
    if choice is None or choice.investment is None:
        return None
    try:
        return GoalInvestment(choice.investment)
    except ValueError:
        return None


def followed_of(
    answer: HouseholdGoal, route: GoalInvestment, lever: GoalPathLever | None = None
) -> Followed | GoalTrackingReason:
    """Today's return of the route followed, from the goal's answer (rules.goal.household_goal).
    The balanced route walks the typed rates since D311, so its rate is the typed rates weighted
    by the split (`goal_path.typed_rate`). The path adopted (`lever`, §7.11 (i)) is followed as
    stored: its rate (none counts 0) and its extra, walked with no spending change, as the path
    itself was found."""
    if answer.issue is not None or answer.word is None:
        return Reason.GOAL_ISSUE
    if route is GoalInvestment.MORE_RETURN:
        m = answer.more_return
        if m is None or not m.available or m.needed_return is None:
            return Reason.ROUTE_GONE
        return Followed(route=route, rate=m.needed_return)
    if route is GoalInvestment.PATH:
        if lever is None:
            return Reason.ROUTE_GONE
        return Followed(route=route, rate=lever.rate or ZERO, extra=lever.extra)
    b = answer.balanced
    rate = b.return_rate if b is not None else None
    if rate is None and answer.goal_path is not None:
        rate = answer.goal_path.typed_rate
    if b is None or rate is None or b.saving_extra is None:
        return Reason.ROUTE_GONE
    return Followed(
        route=route,
        rate=rate,
        spending_changes=tuple(b.spending_changes),
        extra=b.saving_extra,
    )


def planned_per_year(p: Projection, f: Followed, today: date) -> Decimal | None:
    """What the walk of the route followed pays into funds and crypto in the twelve months
    after the current month (reading 3); None when the walk does not reach them."""
    now = current_month(today)
    first, end = add_months(now, 1), add_months(now, 12)

    def worth(rate: Decimal | None) -> tuple[Decimal, Decimal] | None:
        vc = p.levers.value_change.model_copy(update={"depot": rate, "crypto": rate})
        q = replace(p, levers=p.levers.model_copy(update={"value_change": vc}))
        w = walk(q, f.spending_changes, until=end, extra=f.extra, extra_from=first)
        a, b = w.at(now), w.at(end)
        return None if a is None or b is None else (a.net_worth, b.net_worth)

    paid, kept = worth(ZERO), worth(None)
    if paid is None or kept is None:
        return None
    return max(ZERO, (paid[1] - kept[1]) - (paid[0] - kept[0]))


def implied(rate: Decimal, items: Sequence[tuple[date, Decimal]], today: date) -> Decimal:
    """The change `rate` implies on each amount from its day to today (reading 5)."""
    r = Fraction(rate)
    total = Fraction(0)
    for on, amount in items:
        years, rest = divmod(max(0, (today - on).days), YEAR_DAYS)
        total += Fraction(amount) * ((1 + r) ** years * (1 + r * Fraction(rest, YEAR_DAYS)) - 1)
    return _cent(total)


def tracking(i: TrackerInputs) -> GoalTracking:
    if i.mode is AdviceMode.GENERIC:
        return GoalTracking(shown=False, reason=Reason.GENERIC_MODE)
    if i.goal is None:
        return GoalTracking(shown=False, reason=Reason.NO_GOAL)
    route = route_of(i.choice)
    if route is None or i.choice is None or i.choice.investment_chosen_on is None:
        return GoalTracking(shown=False, reason=Reason.NOT_FOLLOWING)
    chosen = i.choice.investment_chosen_on
    days = max(0, (i.today - chosen).days)
    out: dict = {"shown": True, "route": route, "chosen_on": chosen, "days": days}
    a = i.actual
    unpriced = a is not None and any(amount is None for _, amount in a.flows)
    unvalued = a is not None and a.unvalued > 0
    paid: Decimal | None = None
    change: Decimal | None = None
    if a is not None:
        out["reserve_depots"] = a.reserve_depots
        flows = [
            R.Flow(portfolio="counted", on=chosen, amount=a.value_then, key="then"),
            *(
                R.Flow(portfolio="counted", on=on, amount=amount, key=k)
                for k, (on, amount) in enumerate(a.flows)
            ),
        ]
        perf = R.performance(flows, a.value_now, i.today, a.unvalued)
        out["yearly_return"] = perf.yearly_return
        out["no_rate"] = None if perf.no_rate is None else NoRateReason(perf.no_rate.value)
        if not unpriced:
            paid = sum((amount for _, amount in a.flows if amount is not None), ZERO)
            out["paid_in"] = paid
        if not unpriced and not unvalued and paid is not None:
            change = a.value_now - a.value_then - paid
            out |= {"value_then": a.value_then, "value_now": a.value_now, "value_change": change}
    f = i.followed
    if f is None:
        f = Reason.ROUTE_GONE
    if isinstance(f, GoalTrackingReason):
        return GoalTracking(reason=f, **out)
    pace = None if i.projection is None else planned_per_year(i.projection, f, i.today)
    if pace is None:
        return GoalTracking(reason=Reason.NO_PLAN, **out)
    planned = _cent(Fraction(pace) * Fraction(days, YEAR_DAYS))
    out |= {"rate": f.rate, "planned_per_year": pace, "planned_by_now": planned}
    if a is None or unpriced:
        return GoalTracking(reason=Reason.UNPRICED if unpriced else Reason.NO_PLAN, **out)
    if unvalued or paid is None or change is None:
        return GoalTracking(reason=Reason.UNVALUED, **out)
    items = [(chosen, a.value_then)]
    items += [(on, amount) for on, amount in a.flows if amount is not None]
    expected = implied(f.rate, items, i.today)
    short = []
    if paid < planned:
        short.append(GoalTrackingShort.PAID)
    if change < expected:
        short.append(GoalTrackingShort.VALUE)
    return GoalTracking(
        implied_change=expected,
        word=GoalTrackingWord.BEHIND if short else GoalTrackingWord.ON_PATH,
        short=short,
        **out,
    )


def _cent(value: Fraction) -> Decimal:
    return cent(Decimal(value.numerator) / Decimal(value.denominator))


__all__ = [
    "Actual",
    "Followed",
    "TrackerInputs",
    "followed_of",
    "implied",
    "planned_per_year",
    "route_of",
    "tracking",
]
