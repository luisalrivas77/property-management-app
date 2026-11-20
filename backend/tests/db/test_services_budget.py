"""services.budget under hausbuch_app and RLS (docs/phases/phase-10.md §6, D273): the Budget
read from the same window and the same household months as GET /health, so Sparen ÷ E is the
Sparquote /health shows, and the scenario's targets and class overrides are the ones read.
Invented amounts only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_private_matching import Private
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import statement_import as import_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.budget import BudgetLineKey as L
from hausbuch.schemas.budget import BudgetTargets
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import HouseholdLevers, HouseholdScenarioCreate
from hausbuch.services import allocations, health, household_scenarios
from hausbuch.services.budget import household_budget
from hausbuch.services.errors import NotFoundError

pytestmark = pytest.mark.db

C = AllocationCategory
NOW = datetime(2027, 4, 10, 8, 0, tzinfo=UTC)
TODAY = date(2027, 4, 10)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    w = World(app_engine, fresh_workspaces[0], now=NOW)
    with workspace_transaction(w.engine, w.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == w.import_id)
            .values(first_booking_on=date(2026, 1, 1), last_booking_on=date(2027, 3, 31))
        )
    return w


def _private(world: World) -> Private:
    private = Private(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == private.import_id)
            .values(first_booking_on=date(2026, 1, 1), last_booking_on=date(2027, 3, 31))
        )
    return private


def _booked(world: World, booked: date, amount: str, category: C, on=None) -> None:
    """A line of the rental account, or of `on` (a Private account), booked and confirmed."""
    tx = (on or world).line(booked, amount)
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
    )


def test_the_budget_reads_health_s_window_and_rate(world):
    private = _private(world)
    for month, rent, repair, salary, groceries, restaurant in (
        (1, "950.00", "-120.37", "3100.00", "-412.19", "-61.40"),
        (2, "950.00", "0.00", "3100.00", "-388.05", "0.00"),
        (3, "975.00", "-333.33", "3150.00", "-455.71", "-98.30"),
    ):
        _booked(world, date(2027, month, 3), rent, C.RENT_COLD)
        if repair != "0.00":
            _booked(world, date(2027, month, 15), repair, C.REPAIRS)
        _booked(world, date(2027, month, 27), salary, C.HH_SALARY, on=private)
        _booked(world, date(2027, month, 1), "-1100.00", C.HH_RENT, on=private)
        _booked(world, date(2027, month, 9), groceries, C.HH_GROCERIES, on=private)
        if restaurant != "0.00":
            _booked(world, date(2027, month, 20), restaurant, C.HH_RESTAURANT, on=private)

    view = world.query(household_budget, TODAY)
    figures = world.query(health.health, TODAY)
    assert view.window == figures.window and view.window.months[-1] == "2027-03"
    assert view.scenario_id is None and view.targets == BudgetTargets()
    assert view.income is not None and view.income.total > 0
    saving = next(li for li in view.lines if li.key is L.SAVING)
    assert figures.savings_rate is not None and figures.savings_rate.rate is not None
    assert saving.share == figures.savings_rate.rate  # D273
    assert saving.total == figures.savings_rate.saved
    assert view.income.total == figures.savings_rate.income
    need = next(li for li in view.lines if li.key is L.NEED)
    assert need.total > 0 and view.housing is not None and view.housing.total > 0
    for row in view.months:  # the identity per month
        assert row.need + row.want + row.one_off + row.uncategorized + row.saving == row.income
    assert all(li.mark is None for li in view.lines)  # no target saved, no mark (D274)


def test_the_scenario_s_targets_and_overrides(world):
    private = _private(world)
    for month in (1, 2, 3):
        _booked(world, date(2027, month, 3), "950.00", C.RENT_COLD)
        _booked(world, date(2027, month, 27), "3100.00", C.HH_SALARY, on=private)
        _booked(world, date(2027, month, 1), "-1100.00", C.HH_RENT, on=private)
        _booked(world, date(2027, month, 9), "-400.00", C.HH_GROCERIES, on=private)
    levers = HouseholdLevers(
        budget_targets=BudgetTargets(needs_max=Decimal("50.0"), saving_min=Decimal("20.0")),
        budget_class_overrides={C.HH_GROCERIES: BudgetClass.WANT},
    )
    row = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(name="Basis", is_default=True, levers=levers),
    )
    for named in (None, row.id):
        view = world.query(household_budget, TODAY, named)
        assert view.scenario_id == row.id and view.targets == levers.budget_targets
        want = next(li for li in view.lines if li.key is L.WANT)
        groceries = next(c for c in want.codes if c.category is C.HH_GROCERIES)
        assert groceries.overridden and groceries.budget_class is BudgetClass.WANT
        marked = {li.key: li.mark for li in view.lines}
        assert marked[L.NEED] is not None and marked[L.SAVING] is not None
        assert marked[L.WANT] is None  # no target for wants
        assert {m.colour.value for m in marked.values() if m} <= {"green", "amber"}


def test_an_unknown_or_foreign_scenario_is_not_found(world, app_engine, fresh_workspaces):
    row = world.cmd(
        household_scenarios.create_scenario, HouseholdScenarioCreate(name="Basis", is_default=True)
    )
    other = World(app_engine, fresh_workspaces[1], now=NOW)
    with pytest.raises(NotFoundError):
        other.query(household_budget, TODAY, row.id)
