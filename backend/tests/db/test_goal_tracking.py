"""Phase 10 §7.9 (e) on the database: GET /household-goal/tracking's service („Für euer
Ziel“) over the default scenario's goal and its followed return, the counted accounts (a
depot ticked into the reserve left out, D304), their payments after the day chosen, and the
check of `goal_choice.investment` on save. The arithmetic is tests/fixtures/phase10/
goal_invest.yaml (tests/rules/test_phase10_goal_invest.py). Rows INVENTED, round values."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_household_goal import _goal
from db.test_plan_inputs import _projected
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_GOAL_INVALID
from hausbuch.rules.goal import series
from hausbuch.schemas.goal import (
    GoalInvestment,
    GoalTracking,
    GoalTrackingReason,
    GoalTrackingShort,
    GoalTrackingWord,
)
from hausbuch.schemas.plan import GoalChoice, HouseholdScenarioCreate, ReserveAccounts
from hausbuch.schemas.portfolio import NoRateReason
from hausbuch.services import goal as goal_service
from hausbuch.services import goal_tracking, household_scenarios, reference_returns
from hausbuch.services.errors import RuleViolationError

pytestmark = pytest.mark.db

CHOSEN = date(2027, 1, 1)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _series(world: World) -> None:
    rows = {y: (Decimal("6.00"), Decimal("2.00")) for y in range(1991, max(series.fx()) + 1)}
    world.cmd(lambda ctx: reference_returns.store(ctx.conn, world.ws, rows, date(2027, 3, 1)))


def _follow(world: World, route: str | None, chosen: date | None = CHOSEN, **more):
    current = world.query(household_scenarios.default_scenario)
    assert current is not None
    choice = GoalChoice(investment=route, investment_chosen_on=chosen if route else None)
    return world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            supersedes_id=current.id,
            levers=current.levers.model_copy(update={"goal_choice": choice, **more}),
        ),
    )


def _read(world: World):
    return world.query(goal_tracking.goal_tracking, TODAY)


def _followed_world(world: World):
    """The goal world of test_plan_inputs._projected (the depot fed 50, 60 and 50 € on 15
    January, February and March 2027), the funds series stored so route 3 has a return, and
    a goal of 330,000 € by December 2028 that the projection misses (route 3 and the
    balanced route both computed)."""
    _projected(world)
    _series(world)
    _follow(world, None, goal=_goal("330000.00"))
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(account_table.c.id).where(account_table.c.kind == "depot")
        ).scalar_one()


def test_nothing_is_read_before_a_return_is_followed_or_in_generic_mode(world):
    assert _read(world) == GoalTracking(shown=False, reason=GoalTrackingReason.NO_GOAL)
    _followed_world(world)
    assert _read(world) == GoalTracking(shown=False, reason=GoalTrackingReason.NOT_FOLLOWING)
    _follow(world, "more_return")
    assert _read(world).shown
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(workspace_table).values(advice_mode="generic"))
    assert _read(world) == GoalTracking(shown=False, reason=GoalTrackingReason.GENERIC_MODE)


def test_route_3_and_the_balanced_route_against_the_depots_payments(world):
    """The return followed is today's answer of its route; the money put in is the three
    transfers after the day chosen (160 €), the depot's value today its cash; the plan's
    pace is far above, so the word is „dahinter“ on both counts."""
    _followed_world(world)
    answer = world.query(goal_service.goal, TODAY)
    assert answer.more_return is not None and answer.balanced is not None
    _follow(world, "more_return")
    got = _read(world)
    assert (got.shown, got.reason, got.route) == (True, None, GoalInvestment.MORE_RETURN)
    assert (got.chosen_on, got.days) == (CHOSEN, 99)
    assert got.rate == answer.more_return.needed_return
    assert (got.paid_in, got.value_then, got.value_now) == (
        Decimal("160.00"),
        Decimal("0.00"),
        Decimal("160.00"),
    )
    assert got.value_change == Decimal("0.00")
    assert got.planned_per_year is not None and got.planned_per_year > got.paid_in
    assert got.word is GoalTrackingWord.BEHIND
    assert got.short == [GoalTrackingShort.PAID, GoalTrackingShort.VALUE]
    assert (got.yearly_return, got.no_rate) == (None, NoRateReason.SHORT_HISTORY)

    _follow(world, "balanced")
    bal = _read(world)
    assert bal.route is GoalInvestment.BALANCED
    assert bal.rate == answer.balanced.return_rate
    assert bal.paid_in == Decimal("160.00") and bal.word is GoalTrackingWord.BEHIND

    _follow(world, "balanced", chosen=date(2027, 3, 20))  # after the last transfer
    late = _read(world)
    assert (late.paid_in, late.value_then, late.value_now) == (
        Decimal("0.00"),
        Decimal("160.00"),
        Decimal("160.00"),
    )


def test_a_depot_counted_in_the_reserve_is_left_out(world):
    depot = _followed_world(world)
    _follow(world, "more_return", reserve_accounts=ReserveAccounts(added=[depot]))
    got = _read(world)
    assert got.reserve_depots == 1
    assert (got.paid_in, got.value_then, got.value_now) == (
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )


def test_an_unknown_followed_return_is_refused_on_save(world):
    _followed_world(world)
    with pytest.raises(RuleViolationError) as refused:
        _follow(world, "something_else")
    assert refused.value.codes == (HOUSEHOLD_SCENARIO_GOAL_INVALID,)
    row = _follow(world, "balanced")
    assert row.levers.goal_choice == GoalChoice(investment="balanced", investment_chosen_on=CHOSEN)
