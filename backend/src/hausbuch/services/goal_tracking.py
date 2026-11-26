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

Residual: a depot whose cash statements begin after the day chosen while it already held funds
then counts its first statement's value (its START) as money in, as Anlagen's own figures do
for the days before the app knew it.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules.goal import household_goal
from hausbuch.rules.goal import tracker as T
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.goal import GoalTracking, GoalTrackingReason, HouseholdGoal
from hausbuch.schemas.prices import AssetsOverview

from .goal import goal_inputs
from .health import levers_one_off_codes
from .plan import _scenario, advice_mode
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
        followed = T.followed_of(household_goal(built.goal), route)
        projection = built.goal.projection
    chosen = choice.investment_chosen_on
    assert chosen is not None  # GoalChoice requires the day with the route
    actual = _actual(conn, today, min(chosen, today))
    return T.tracking(T.TrackerInputs(today, mode, goal, choice, followed, projection, actual))


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
