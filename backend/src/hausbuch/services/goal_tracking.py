"""GET /household-goal/tracking (docs/phases/phase-10.md §7.9 (e), „(e) as decided for the
build“ 3; D298 (5)–(7)): „Für euer Ziel“ on Anlagen › Übersicht. The arithmetic and its
readings are rules/goal/tracker.py; this module reads:

- the mode (D251), the default scenario's goal and `goal_choice.investment`: in `generic` mode,
  without a goal or without a return followed nothing else is read;
- the goal's answer and its walk (services.goal.goal_inputs, the same reads GET /household-goal
  keeps), for the route followed today and its pace;
- the counted accounts: every depot, exchange and wallet (ASSET_ACCOUNT_KINDS) less a depot the
  default scenario counts in the reserve (`counts_to_reserve`, D304);
- their value at the end of the day chosen (GET /assets/overview on that day, the daily
  closes) and today (the live day, as Anlagen shows it), and their payments in and out after
  the day chosen as GET /assets/performance lists them (rules/performance), less a move
  between two counted accounts; a move to or from a reserve depot counts.

For the path adopted (§7.11 (i) items 6, 12; route `path` with the lever `goal_path`), one row
and one chart point per closed month-end since the day adopted (every closed month from that
day's month on, beyond the key figures' window of 12): the month's saved and its purchases per
kind (rules/investment_split.py, a reserve depot left out), its wants (the month's regular
spending on the codes the Budget counts as Wünsche), the stored month-end net worth (the newest
run; a month whose month-end is not stored yet gets no row) against the path's own walk, and the
counted accounts' value at that month-end (GET /assets/overview on that day) with their flows
after the day adopted up to it. A flow without euros counts nothing there (the summary above
names it as unpriced).

Residual: a depot whose cash statements begin after the day chosen while it already held funds
then counts its first statement's value (its START) as money in, as Anlagen's own figures do
for the days before the app knew it.
"""

import calendar
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules import investment_split as X
from hausbuch.rules.goal import household_goal
from hausbuch.rules.goal import path as PA
from hausbuch.rules.goal import tracker as T
from hausbuch.rules.plan import spending as SP
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.goal import (
    GoalInvestment,
    GoalPathPoint,
    GoalPathRow,
    GoalTracking,
    GoalTrackingReason,
    HouseholdGoal,
)
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import GoalPathLever, HouseholdLevers, PlanClass
from hausbuch.schemas.portfolio import AssetsPerformance
from hausbuch.schemas.prices import AssetsOverview

from .goal import GoalReads, _reserve_depots, goal_inputs
from .health import MonthRead, levers_one_off_codes, run_totals
from .plan import _scenario, advice_mode, plan_lines
from .plan_inputs import PlanInputs
from .portfolio_performance import assets_performance
from .prices.overview import ASSET_ACCOUNT_KINDS, assets_overview

ZERO = Decimal("0.00")
_KINDS = {k.value for k in ASSET_ACCOUNT_KINDS}


def goal_tracking(conn: Connection, today: date) -> GoalTracking:
    inputs = PlanInputs(conn, today)
    mode = inputs.part(("advice_mode",), advice_mode)
    if mode is AdviceMode.GENERIC:
        return GoalTracking(shown=False, reason=GoalTrackingReason.GENERIC_MODE)
    row = _scenario(conn, None, inputs)
    levers = None if row is None else row.levers
    goal = None if levers is None else levers.goal
    choice = None if levers is None else levers.goal_choice
    route = T.route_of(choice)
    if levers is None or goal is None or route is None or choice is None:
        return T.tracking(
            T.TrackerInputs(today, mode, goal, choice, None, projection=None, actual=None)
        )
    reads = inputs.ended_reads(levers_one_off_codes(levers))
    built = goal_inputs(conn, today, row, levers, reads, inputs)
    if isinstance(built, HouseholdGoal):
        followed: T.Followed | GoalTrackingReason = GoalTrackingReason.GOAL_ISSUE
        projection = None
    else:
        followed = T.followed_of(household_goal(built.goal), route, levers.goal_path)
        projection = built.goal.projection
    chosen = choice.investment_chosen_on
    assert chosen is not None  # GoalChoice requires the day with the route
    actual = _actual(conn, today, min(chosen, today))
    answer = T.tracking(T.TrackerInputs(today, mode, goal, choice, followed, projection, actual))
    lever = levers.goal_path
    if route is not GoalInvestment.PATH or lever is None or isinstance(built, HouseholdGoal):
        return answer
    rows, points = _path_rows(conn, today, lever, levers, built, reads)
    return answer.model_copy(update={"rows": rows, "points": points})


def _path_rows(
    conn: Connection,
    today: date,
    lever: GoalPathLever,
    levers: HouseholdLevers,
    built: GoalReads,
    reads: dict[str, MonthRead],
) -> tuple[list[GoalPathRow], list[GoalPathPoint]]:
    """§7.11 (i) items 6, 12: one row and one point per closed month-end since the day the
    path was adopted (the module doc)."""
    day = lever.adopted_on
    first = day.strftime("%Y-%m")
    # every closed month since the day, not only the key figures' window of at most 12
    months = sorted(m for m, r in reads.items() if m >= first and r.closed.closed)
    stored = {t.month_end: t.total for t in run_totals(conn)}
    ends = {m: _month_end(m) for m in months}
    months = [m for m in months if ends[m] in stored and ends[m] < today]
    if not months:
        return [], []
    lines = plan_lines(conn, months)
    reserve = _reserve_depots(built.parts.reserve)
    walk = {p.month: p.net_worth for p in lever.months}
    classes = built.goal.classes
    then = assets_overview(conn, day)
    performance = assets_performance(conn, today)
    overviews = {m: assets_overview(conn, ends[m]) for m in months}
    members = _members([then, *overviews.values()], performance)
    split = levers.investment_split
    out: list[PA.PathMonth] = []
    for month in months:
        end = ends[month]
        r = reads[month]
        bought = X.bought(lines, [month], reserve)
        codes = SP.month_spending(
            r.private.cashflow, r.private.lines, r.one_off_ids, r.one_off_codes
        )
        wants = sum((v for c, v in codes.items() if classes.get(c) is BudgetClass.WANT), ZERO)
        flows = [
            (f.on, f.amount_eur)
            for p in performance.portfolios
            if p.account_id in members
            for f in p.flows
            if day < f.on <= end
            and f.counterpart_account_id not in members
            and f.amount_eur is not None
        ]
        out.append(
            PA.PathMonth(
                month_end=end,
                saved=r.household.saved,
                principal=r.household.principal,
                invested_property=r.household.invested_property,
                invested=sum(bought.values(), ZERO),
                crypto_bought=bought.get(PlanClass.CRYPTO, ZERO),
                wants=wants,
                net_worth=stored[end],
                path_net_worth=walk.get(month),
                value_now=_value(overviews[month], members)[0],
                flows=sorted(flows, key=lambda f: f[0]),
            )
        )
    return PA.track(
        day,
        lever.monthly,
        lever.wants_budget_total,
        lever.rate,
        None if split is None else split.crypto,
        _value(then, members)[0],
        out,
        _invest_now(lever),
    )


def _invest_now(lever: GoalPathLever) -> tuple[Decimal, Decimal] | None:
    """„Jetzt anlegen“ as adopted (§7.11 (k)): the amount and its crypto part, or None."""
    if lever.above_target is None or lever.above_target <= 0:
        return None
    return lever.above_target, lever.above_target_crypto or ZERO


def _members(overviews: list[AssetsOverview], performance: AssetsPerformance) -> set[UUID]:
    """The counted accounts as `_actual` counts them, over every day read."""
    reserve = {a.account_id for o in overviews for a in o.accounts if a.counts_to_reserve}
    members = {
        p.account_id
        for p in performance.portfolios
        if p.kind in ASSET_ACCOUNT_KINDS and p.account_id not in reserve
    }
    for o in overviews:
        members |= _counted(o, reserve)
    return members


def _month_end(month: str) -> date:
    year, number = int(month[:4]), int(month[5:7])
    return date(year, number, calendar.monthrange(year, number)[1])


def _actual(conn: Connection, today: date, chosen: date) -> T.Actual:
    now = assets_overview(conn, today, today=today)
    then = assets_overview(conn, chosen)
    reserve = {a.account_id for a in now.accounts if a.counts_to_reserve}
    reserve |= {a.account_id for a in then.accounts if a.counts_to_reserve}
    performance = assets_performance(conn, today)
    members = {
        p.account_id
        for p in performance.portfolios
        if p.kind in ASSET_ACCOUNT_KINDS and p.account_id not in reserve
    }
    members |= _counted(now, reserve) | _counted(then, reserve)
    value_then, unvalued_then = _value(then, members)
    value_now, unvalued_now = _value(now, members)
    flows: list[tuple[date, Decimal | None]] = [
        (f.on, f.amount_eur)
        for p in performance.portfolios
        if p.account_id in members
        for f in p.flows
        if f.on > chosen and f.counterpart_account_id not in members
    ]
    return T.Actual(
        value_then=value_then,
        flows=sorted(flows, key=lambda f: f[0]),
        value_now=value_now,
        unvalued=unvalued_then + unvalued_now,
        reserve_depots=len(reserve),
    )


def _counted(overview: AssetsOverview, reserve: set[UUID]) -> set[UUID]:
    return {
        a.account_id for a in overview.accounts if a.kind in _KINDS and a.account_id not in reserve
    }


def _value(overview: AssetsOverview, members: set[UUID]) -> tuple[Decimal, int]:
    accounts = [a for a in overview.accounts if a.account_id in members]
    total = sum((a.total_eur for a in accounts), ZERO)
    unvalued = sum(1 for a in accounts for p in a.positions if p.value_eur is None)
    return total, unvalued
