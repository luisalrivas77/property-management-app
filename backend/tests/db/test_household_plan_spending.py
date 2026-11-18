"""Phase 9 step 3b (D263) on the database: spending changes through the household plan, the
preview that stores nothing, `mean_at_save` written by the service and the comparison with
the real month. The arithmetic is spending_play.yaml and spending_comparison.yaml
(tests/rules/test_phase9_spending.py); this file checks the reads and the writes around it."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_household_plan import _plan, _private_and_depot
from db.test_services_health import _book
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import func, select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import household_scenario as scenario_table
from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_SPENDING_INVALID
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.household import HouseholdGroup
from hausbuch.schemas.plan import (
    ComparisonWords,
    HouseholdLevers,
    HouseholdScenarioCreate,
    SpendingChange,
)
from hausbuch.services import household_scenarios, plan
from hausbuch.services.errors import RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _supermarket(world, private) -> None:
    """1,200 € at the supermarket in each of January to March 2027, the plan's window (the
    private account's months), so its mean is 1,200 €; 100 € in the running April."""
    for month in (1, 2, 3, 4):
        amount = "-100.00" if month == 4 else "-1200.00"
        line = private.line(date(2027, month, 12), amount, name="Supermarkt")
        _book(world, line, amount, C.HH_SUPERMARKET)


def _change(**kw) -> SpendingChange:
    return SpendingChange(**({"code": C.HH_SUPERMARKET, "from_month": "2027-04"} | kw))


def _save(world, *changes, **levers):
    return world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(spending_changes=list(changes), **levers),
        ),
    )


def _counts(world) -> tuple[int, int]:
    with workspace_transaction(world.engine, world.ws) as conn:
        rows = conn.execute(select(func.count()).select_from(scenario_table)).scalar_one()
        events = conn.execute(select(func.count()).select_from(event_table)).scalar_one()
    return rows, events


def test_a_spending_change_moves_the_plan_and_its_mean_is_written_on_save(world):
    """§7.6: the codes' means come from the window (1,200 € at the supermarket); a planned
    1,000 € from April lowers spending by 200 €, so free cash and Anlegbar rise by it, the
    play shows the group with its mean and the rate with the change; `mean_at_save` is the
    service's, whatever the request carried."""
    private, _ = _private_and_depot(world)
    _supermarket(world, private)
    before = _plan(world)
    row = _save(world, _change(monthly=Decimal("1000.00"), mean_at_save=Decimal("1.00")))
    assert row.levers.spending_changes[0].mean_at_save == Decimal("1200.00")
    after = _plan(world)
    assert after.scenario_id == row.id
    first, was = after.months[0], before.months[0]
    assert first.month == "2027-04" and first.spending_delta == Decimal("-200.00")
    assert first.free == was.free + Decimal("200.00")
    play = after.spending_play
    assert play is not None and before.spending_play is not None
    assert play.mean_regular_spending == Decimal("1200.00")
    groceries = next(g for g in play.groups if g.group is HouseholdGroup.GROCERIES)
    assert groceries.mean == Decimal("1200.00")
    (row_super,) = groceries.rows
    assert (row_super.planned_first, row_super.change_first) == (
        Decimal("1000.00"),
        Decimal("-200.00"),
    )
    assert [s.rate for s in before.spending_play.rates] == [before.spending_play.rate_regular_now]
    assert play.rates[0].rate > play.rate_regular_now
    # Each stretch carries its planned spending and its Anlegbar without one-offs (§7.1 with
    # the flat at its mean); with no one-off and no flat scenario that is the month's own.
    assert play.rates[0].regular_spending == Decimal("1000.00")
    assert play.rates[0].investable == first.investable
    assert before.spending_play.rates[0].investable == was.investable


def test_the_preview_computes_the_typed_levers_and_stores_nothing(world):
    """D263 (c): POST /household-plan/preview's service gives the plan for the typed levers,
    on the saved default's projection, and writes no row and no event; the saved plan is
    unchanged."""
    private, _ = _private_and_depot(world)
    _supermarket(world, private)
    saved = _save(world)
    counts = _counts(world)
    typed = HouseholdLevers(spending_changes=[_change(percent=Decimal("-0.50"))])
    got = world.query(plan.preview_plan, TODAY, typed)
    assert _counts(world) == counts
    assert got.scenario_id == saved.id and got.levers == typed
    assert got.months[0].spending_delta == Decimal("-600.00")
    assert _plan(world).months[0].spending_delta == Decimal("0.00")


def test_a_percent_of_nothing_is_refused_in_the_preview_and_on_save(world):
    """§7.6 (1): a percent change of a code with no measured spending is refused, by the
    preview and by the save alike; a monthly amount for it is a new expense."""
    private, _ = _private_and_depot(world)
    _supermarket(world, private)
    nothing = _change(code=C.HH_CHILDCARE, percent=Decimal("0.10"))
    with pytest.raises(RuleViolationError) as refused:
        world.query(plan.preview_plan, TODAY, HouseholdLevers(spending_changes=[nothing]))
    assert refused.value.codes == (HOUSEHOLD_SCENARIO_SPENDING_INVALID,)
    with pytest.raises(RuleViolationError):
        _save(world, nothing)
    row = _save(world, _change(code=C.HH_CHILDCARE, monthly=Decimal("250.00")))
    assert row.levers.spending_changes[0].mean_at_save == Decimal("0.00")
    assert _plan(world).months[0].spending_delta == Decimal("250.00")


def test_the_comparison_reads_the_scenario_in_force_on_the_month(world):
    """§7.7: the running April beside the scenario saved on 10 April: 100 € spent against
    1,000 € planned is „900 € weniger als geplant“, „bisher“ (not final), the line its source;
    March ended before the scenario was saved, so it has no rows; a month before any scenario
    has none either."""
    private, _ = _private_and_depot(world)
    _supermarket(world, private)
    row = _save(world, _change(monthly=Decimal("1000.00")))
    april = world.query(plan.spending_comparison, TODAY, "2027-04")
    assert april.scenario_id == row.id and not april.final
    (got,) = april.rows
    assert (got.code, got.planned, got.actual, got.difference) == (
        C.HH_SUPERMARKET,
        Decimal("1000.00"),
        Decimal("100.00"),
        Decimal("-900.00"),
    )
    assert got.words is ComparisonWords.LESS and got.text == "900 € weniger als geplant"
    assert len(got.sources) == 1
    march = world.query(plan.spending_comparison, TODAY, "2027-03")
    assert march.scenario_id is None and march.rows == []
    assert march.final
