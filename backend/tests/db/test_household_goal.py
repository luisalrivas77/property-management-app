"""Phase 10 §7.9 (d) on the database: GET /household-goal's service over the default household
scenario's goal, each word, the goal's checks on save and „Als Budget übernehmen“ as a
supersede. The arithmetic is tests/fixtures/phase10/goal.yaml (tests/rules/
test_phase10_goal.py); this file checks the reads around it and that the goal walks the same
projection the plan shows."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_household_plan import _bought_in_2026, _flat_scenario, _plan, _savings_history
from db.test_services_health import _book
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import func, select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import household_scenario as scenario_table
from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_GOAL_INVALID
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.goal import GoalIssue, GoalSpending, GoalWord
from hausbuch.schemas.health import HealthTodoCode
from hausbuch.schemas.plan import (
    Goal,
    GoalChoice,
    GoalKind,
    HouseholdLevers,
    HouseholdScenarioCreate,
)
from hausbuch.services import goal as goal_service
from hausbuch.services import household_scenarios, net_worth, plan
from hausbuch.services.errors import RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory
RATES = {"depot": Decimal("0.030")}
DUE = "2028-12"  # a 31 December, so the goal's walk meets one of the plan's points


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _projected_world(world: World, *, groceries: bool = False):
    """_savings_history (twelve closed months to March 2027, a private account and a depot),
    300 € a month at restaurants in January to March 2027 (a want, D272), the flat bought in
    2026 with its default scenario and the stored month-end of 31 March 2027: phase 9's
    projection has points and no to-do. `groceries` adds 120 € a month at a market in the
    same months (a need, so the reserve's need is not 0, phase 10 D305)."""
    private, _ = _savings_history(world)
    for month in (1, 2, 3):
        line = private.line(date(2027, month, 18), "-300.00", name="Trattoria Muster")
        _book(world, line, "-300.00", C.HH_RESTAURANT)
        if groceries:
            line = private.line(date(2027, month, 8), "-120.00", name="Markt Muster")
            _book(world, line, "-120.00", C.HH_GROCERIES)
    _bought_in_2026(world)
    _flat_scenario(world, None, NOW)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))


def _goal(target: str, due: str = DUE, kind: GoalKind = GoalKind.NET_WORTH) -> Goal:
    return Goal(kind=kind, target=Decimal(target), due_month=due, set_on=TODAY)


def _save(world: World, goal: Goal | None, supersedes=None, **levers):
    return world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            supersedes_id=supersedes,
            levers=HouseholdLevers(value_change=RATES, net_worth_years=3, goal=goal, **levers),
        ),
    )


def _read(world: World):
    return world.query(goal_service.goal, TODAY)


def test_without_a_scenario_or_a_goal_the_word_is_none(world):
    assert _read(world).word is GoalWord.NONE
    _projected_world(world)
    _save(world, None)
    got = _read(world)
    assert (got.word, got.goal, got.target) == (GoalWord.NONE, None, None)


def test_below_three_closed_months_the_goal_has_the_plans_to_dos(world):
    """No closed month: the goal is set but has no word; the card shows the plan's to-do."""
    _save(world, _goal("100000.00"))
    got = _read(world)
    assert (got.word, got.issue) == (None, GoalIssue.NO_PROJECTION)
    assert got.goal is not None and got.goal.target == Decimal("100000.00")
    assert [t.code for t in got.todos] == [HealthTodoCode.TOO_FEW_MONTHS]


def test_each_word_on_the_plans_own_projection(world):
    """A goal far above the projection is „So nicht erreichbar“, its value at the due month
    the plan's point of 31 December 2028; the same value as the target is „Auf Kurs“; 100 €
    more is „Dahinter“, a wants scenario closing it."""
    _projected_world(world)
    row = _save(world, _goal("10000000.00"))
    points = {p.on: p.total for p in _plan(world).projection.points}
    far = _read(world)
    assert far.word is GoalWord.UNREACHABLE
    projected = points[date(2028, 12, 31)]
    assert far.projected == projected
    assert far.unreachable is not None and far.unreachable.reaches_by_due == projected
    assert far.months_left == 20  # April 2027 to December 2028
    assert far.value_today is not None and far.heute is not None
    assert far.noetig == (far.heute + (Decimal("10000000.00") - projected) / 20).quantize(
        Decimal("0.01")
    )

    row = _save(world, _goal(str(projected)), supersedes=row.id)
    exact = _read(world)
    assert exact.word is GoalWord.ON_TRACK and exact.scenarios == []
    assert exact.reached is not None and exact.reached <= DUE

    row = _save(world, _goal(str(projected + 100)), supersedes=row.id)
    behind = _read(world)
    assert behind.word is GoalWord.BEHIND
    wants = next(s for s in behind.scenarios if s.kind is GoalSpending.WANTS_ONLY)
    assert wants.closes
    assert [b.code for b in wants.budgets] == [C.HH_RESTAURANT]
    assert wants.budgets[0].mean == Decimal("300.00")
    [change] = wants.spending_changes
    assert (change.code, change.from_month, change.to_month) == (C.HH_RESTAURANT, "2027-05", DUE)
    assert change.monthly == Decimal("300.00") - wants.cut

    # „Als Budget übernehmen“: a supersede with the scenario's changes and the choice; the
    # adopted budget reaches the target by the due month, so the word is „Auf Kurs“.
    _save(
        world,
        _goal(str(projected + 100)),
        supersedes=row.id,
        spending_changes=wants.spending_changes,
        goal_choice=GoalChoice(spending=GoalSpending.WANTS_ONLY, spending_chosen_on=TODAY),
    )
    adopted = _read(world)
    assert adopted.word is GoalWord.ON_TRACK
    assert adopted.reached_with_budget is not None and adopted.reached_with_budget <= DUE
    assert adopted.choice is not None and adopted.choice.spending is GoalSpending.WANTS_ONLY


def test_a_reserve_goal_reads_the_reserve(world):
    """A sum saved is measured on the reserve accounts: its value today is the plan's
    reserve; a reserve in months is the months × the plan's monthly need (phase 10 D305:
    need-class spending only, so groceries give the world's restaurants-only month a need)."""
    _projected_world(world, groceries=True)
    row = _save(world, _goal("1.00", kind=GoalKind.SAVED_SUM))
    reserve = _plan(world).reserve
    got = _read(world)
    assert got.value_today == reserve.amount and got.word is GoalWord.ON_TRACK
    _save(world, _goal("6", kind=GoalKind.RESERVE_MONTHS), supersedes=row.id)
    got = _read(world)
    assert reserve.need == Decimal("120.00")  # the groceries alone, not the restaurants
    assert got.target == (6 * reserve.need).quantize(Decimal("0.01"))
    assert got.word in {GoalWord.ON_TRACK, GoalWord.BEHIND, GoalWord.UNREACHABLE}


def test_a_goal_not_due_after_the_current_month_is_refused_unless_carried(world):
    """The due month must lie after the current month (April 2027) for a goal new or altered
    on save and in the preview; a goal carried unaltered is never refused, and once its month
    has passed it reads with `issue` due_passed and no word."""
    for due in ("2027-04", "2027-03"):
        with pytest.raises(RuleViolationError) as refused:
            _save(world, _goal("1000.00", due=due))
        assert refused.value.codes == (HOUSEHOLD_SCENARIO_GOAL_INVALID,)
        with pytest.raises(RuleViolationError):
            world.query(plan.preview_plan, TODAY, HouseholdLevers(goal=_goal("1.00", due=due)))
    with workspace_transaction(world.engine, world.ws) as conn:
        assert conn.execute(select(func.count()).select_from(scenario_table)).scalar_one() == 0
    row = _save(world, _goal("1000.00", due="2027-05"))
    later = date(2027, 6, 2)
    with workspace_transaction(world.engine, world.ws) as conn:
        assert (
            household_scenarios.check_levers(  # carried unaltered after its month
                conn, row.levers, later, previous_goal=row.levers.goal
            )
            == {}
        )
    with (
        pytest.raises(RuleViolationError),
        workspace_transaction(world.engine, world.ws) as conn,
    ):
        household_scenarios.check_levers(conn, row.levers, later)
    got = world.query(goal_service.goal, later)
    assert (got.word, got.issue) == (None, GoalIssue.DUE_PASSED)


def test_spending_changes_without_a_choice_are_not_the_goals_projection(world):
    """goal.yaml defines `projected` as the value without any change, and counts the saved
    spending changes (D263) only once a budget is adopted (`goal_choice.spending`). Changes
    played on Haushalt without a choice move the plan's own point, not the goal's."""
    _projected_world(world)
    row = _save(world, _goal("10000000.00"))
    without = _read(world).projected
    assert without == {p.on: p.total for p in _plan(world).projection.points}[date(2028, 12, 31)]
    cut = {"code": C.HH_RESTAURANT, "monthly": "0.00", "from_month": "2027-05", "to_month": DUE}
    _save(world, _goal("10000000.00"), supersedes=row.id, spending_changes=[cut])
    plan_point = {p.on: p.total for p in _plan(world).projection.points}[date(2028, 12, 31)]
    got = _read(world)
    assert plan_point > without  # the plan counts the changes
    assert got.projected == without and got.reached_with_budget is None
