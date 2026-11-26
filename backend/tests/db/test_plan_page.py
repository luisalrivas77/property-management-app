"""„Euer Plan“ (phase 10 §7.11 (h), D306) on the database: services.plan_page over the default
scenario's goal, the reads beside the goal's (the reserve's accounts, the property's living
area, the running month's investments, the adopted budget) and that its cards come from the
same answer GET /household-goal gives. The cards' arithmetic is tests/fixtures/phase10/
plan_page.yaml (tests/rules/test_phase10_plan_page.py)."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_household_goal import _goal, _projected_world, _save, _sold_in_2028
from db.test_household_plan import _plan
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import property_ as property_table
from hausbuch.schemas.goal import GoalWord
from hausbuch.schemas.health import HealthTodoCode
from hausbuch.schemas.plan import GoalChoice, GoalSpending
from hausbuch.schemas.plan_page import PlanStepKind, PlanSwitches, PlanView
from hausbuch.services import goal as goal_service
from hausbuch.services import plan_page as service
from hausbuch.services.scenarios import default_scenario_on

pytestmark = pytest.mark.db


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _page(world: World, view: PlanView = PlanView.OWN, **switches):
    return world.query(service.plan_page, TODAY, view, PlanSwitches(**switches))


def _feasible(world: World):
    """A goal 100 € above the plan's own point of 31 Dec 2028: „Machbar“, its balanced route
    cutting the restaurant (a want)."""
    _projected_world(world, salary="1000.00")
    row = _save(world, _goal("10000000.00"))  # the plan's levers (rates, horizon) first
    points = {p.on: p.total for p in _plan(world).projection.points}
    return _save(world, _goal(str(points[date(2028, 12, 31)] + 100)), supersedes=row.id)


def test_without_a_goal_the_word_is_none(world):
    got = _page(world)
    assert (got.word, got.goal, got.heute, got.todos) == (GoalWord.NONE, None, None, [])
    shared = _page(world, PlanView.SHARE)
    assert shared.word is GoalWord.NONE and shared.switches == PlanSwitches()


def test_below_three_closed_months_the_page_has_the_goals_to_dos(world):
    _save(world, _goal("100000.00"))
    got = _page(world)
    assert got.word is None and got.goal is None
    assert [t.code for t in got.todos] == [HealthTodoCode.TOO_FEW_MONTHS]


def test_the_cards_follow_the_goals_answer(world):
    """The goal card, today's figures and the route are those of GET /household-goal; a flat
    without its living area gets the repair's to-do „Für euch“ and no repair; the shared view
    keeps the to-do out."""
    _feasible(world)
    goal = world.query(goal_service.goal, TODAY)
    got = _page(world)
    assert got.word is goal.word is GoalWord.FEASIBLE
    assert got.goal is not None and got.goal.target == goal.target
    assert got.goal.value_today == goal.value_today
    assert got.heute is not None and got.heute.net_worth == goal.value_today
    assert got.heute.window_to == "2027-03"
    assert got.plan is not None and got.plan.reached == goal.balanced.reached
    assert got.risks is not None and got.risks.repair.reason == "no_living_area"
    assert [t.code for t in got.risks.todos] == [HealthTodoCode.LIVING_AREA_MISSING]
    assert got.risks.todos[0].href == f"/properties/{world.property_id}"
    [flat] = got.assumptions.properties
    assert flat.key == str(world.property_id) and flat.label is not None
    shared = _page(world, PlanView.SHARE)
    assert "todos" not in shared.model_dump()["risks"]

    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(property_table)
            .where(property_table.c.id == world.property_id)
            .values(living_area_m2=Decimal("62.00"))
        )
    repaired = _page(world).risks
    assert repaired.repair.available and repaired.todos == []
    assert repaired.repair.by_property == {str(world.property_id): repaired.repair.amount}


def test_this_month_reads_the_adopted_budget(world):
    """The wants step is done once the balanced route's changes are the default scenario's
    („Als Budget übernehmen“; 19f's button for the balanced route is not built yet, so the
    choice here is „Eigene Aufteilung“ with the route's changes). The balanced route also
    invests more each month, so the goal stays „Machbar“ and the invest step stays open: one
    of two steps done."""
    row = _feasible(world)
    got = _page(world)
    wants = [s for s in got.this_month.steps if s.kind is PlanStepKind.WANTS_BUDGET]
    assert [s.done for s in wants] == [False]
    balanced = world.query(goal_service.goal, TODAY).balanced
    _save(
        world,
        _goal(str(got.goal.target)),
        supersedes=row.id,
        spending_changes=balanced.spending_changes,
        goal_choice=GoalChoice(spending=GoalSpending.OWN, spending_chosen_on=TODAY),
    )
    after = _page(world)
    assert after.word is GoalWord.FEASIBLE
    done = {s.kind: s.done for s in after.this_month.steps}
    assert done == {PlanStepKind.INVEST: False, PlanStepKind.WANTS_BUDGET: True}
    assert (after.this_month.done, after.this_month.of) == (1, 2)


def test_heute_carries_the_plans_money_above_the_reserve(world):
    """The on-track plan card's „Jetzt anlegen“ (Luis, 26 Nov 2026: „it doesn't say what the
    plan is“): `heute.above_target` is GET /household-plan's own `above_target` (Übersicht's
    „Anlegbar jetzt“), never a second computation; „Zum Teilen“ rounds it like the reserve."""
    _projected_world(world, groceries=True, salary="1000.00")
    flat = world.query(default_scenario_on, world.property_id, TODAY)
    _sold_in_2028(world, flat.id)
    _save(world, _goal("10000000.00"), horizon_months=45)
    above = _plan(world).above_target
    assert above is not None and above > 0
    got = _page(world)
    assert got.heute is not None and got.heute.above_target == above
    shared = _page(world, PlanView.SHARE).heute
    assert shared is not None and shared.above_target is not None
    assert shared.above_target % 1000 == 0 and abs(shared.above_target - above) <= 500
    exact = _page(world, PlanView.SHARE, round_amounts=False).heute
    assert exact is not None and exact.above_target == above
