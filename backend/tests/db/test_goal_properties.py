"""Phase 10 §7.9 (e) items 1, 2 on the database: GET /household-goal/properties's service (the
property cards on Anlagen › Immobilie) over the default scenario's goal and the flat's default
phase 4 scenario. The arithmetic is tests/fixtures/phase10/goal_property.yaml
(tests/rules/test_phase10_goal_property.py); here the reads: phase 4's values and net proceeds
at another growth equal the plan's at the typed one, the card's growth is the smallest that
reaches, and the cases with no card. Rows INVENTED, round values."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_goal_tracking import _follow, _followed_world
from db.test_household_goal import _goal
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.goal import property as R
from hausbuch.rules.goal import walk
from hausbuch.rules.goal.goal import target_of, value_of
from hausbuch.rules.goal.series import house_price_series
from hausbuch.schemas.goal import GoalProperties, GoalPropertiesReason, HouseholdGoal
from hausbuch.schemas.performance import Levers, ScenarioCreate
from hausbuch.schemas.plan import GoalKind
from hausbuch.services import goal_properties, plan_inputs
from hausbuch.services.goal import goal_inputs
from hausbuch.services.health import levers_one_off_codes
from hausbuch.services.performance import growth_projector
from hausbuch.services.plan import _scenario, flat_scenarios
from hausbuch.services.plan_inputs import PlanInputs
from hausbuch.services.scenarios import create_scenario, default_scenario

pytestmark = pytest.mark.db


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _read(world: World) -> GoalProperties:
    return world.query(goal_properties.goal_properties, TODAY)


def _sale_in(world: World, year: int) -> None:
    """The flat's default scenario again, sold on 31 December of `year`."""
    current = world.query(default_scenario, world.property_id)
    assert current is not None
    world.cmd(
        create_scenario,
        world.property_id,
        ScenarioCreate(
            property_id=world.property_id,
            name="Basis",
            is_default=True,
            supersedes_id=current.id,
            levers=Levers(value_growth=Decimal("0.02"), sale_year=year),
        ),
        now=datetime(2027, 4, 1, 10, tzinfo=UTC),
    )


def _goal_reads(world: World):
    with workspace_transaction(world.engine, world.ws) as conn:
        inputs = PlanInputs(conn, TODAY)
        row = _scenario(conn, None, inputs)
        levers = row.levers
        reads = inputs.ended_reads(levers_one_off_codes(levers))
        built = goal_inputs(conn, TODAY, row, levers, reads, inputs)
        assert not isinstance(built, HouseholdGoal)
        return built.goal


def test_nothing_is_shown_without_a_net_worth_goal_or_in_generic_mode(world):
    assert _read(world) == GoalProperties(shown=False, reason=GoalPropertiesReason.NO_GOAL)
    _followed_world(world)
    _follow(world, None, goal=_goal("20000.00", kind=GoalKind.SAVED_SUM))
    assert _read(world).reason is GoalPropertiesReason.NOT_NET_WORTH
    _follow(world, None, goal=_goal("330000.00"))
    assert _read(world).shown
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(workspace_table).values(advice_mode="generic"))
    assert _read(world) == GoalProperties(shown=False, reason=GoalPropertiesReason.GENERIC_MODE)


def test_the_card_beside_eurostats_windows(world):
    """The flat sold in 2036, the goal due December 2028 (20 months from April 2027: windows of
    one year, every year of the committed index); the growth is the smallest 0.1 % that reaches
    the target on the goal's walk, 0.1 % less does not."""
    _followed_world(world)
    got = _read(world)
    assert got.shown and got.reason is None
    [card] = got.properties
    assert card.property_id == world.property_id
    assert (card.typed_growth, card.sale_year, card.window_years) == (Decimal("0.02"), 2036, 1)
    house = house_price_series()
    assert card.windows == len(house.returns) and not card.thin
    assert card.source is not None and card.source.key == "house_prices"
    assert card.source.attribution.startswith("Eurostat (prc_hpi_q)")
    assert card.available and card.needed_growth is not None
    assert card.reached_windows is not None and card.verdict is not None

    gi = _goal_reads(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        flat = default_scenario(conn, world.property_id)
        _levers, base, _at = growth_projector(
            conn, world.property_id, today=TODAY, scenario_id=flat.id
        )
    prop = R.PropertyGrowth(base=base, typed_growth=Decimal("0.02"))
    target = target_of(gi.goal, gi.need)
    due = gi.goal.due_month
    for g, reaches in ((card.needed_growth, True), (card.needed_growth - Decimal("0.001"), False)):
        p = R._at_growth(gi.projection, world.property_id, prop, g, TODAY.year, False)
        assert (value_of(gi.goal, walk(p, until=due).at(due)) >= target) is reaches, g


def test_phase_4_at_another_growth_equals_the_plan_at_the_typed_one(world):
    """growth_projector at the scenario's own growth is the projection the plan walks: each
    projected year's value is base × 1.02^n to the cent and the net proceeds are the plan's;
    at −10 % the projection runs (no lever check) and its values follow the same rule."""
    _followed_world(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        flat = default_scenario(conn, world.property_id)
        levers, base, at = growth_projector(
            conn, world.property_id, today=TODAY, scenario_id=flat.id
        )
        plan_flat = flat_scenarios(conn, TODAY)[0][world.property_id]
    assert plan_flat is not None and levers.value_growth == Decimal("0.02")
    typed = at(Decimal("0.02"))
    assert typed.sale.net_proceeds == plan_flat.net_proceeds
    for year, fy in plan_flat.years.items():
        assert fy.value == R.value_at(base, Decimal("0.02"), year - TODAY.year), year
    fallen = at(Decimal("-0.100"))
    for y in fallen.years:
        if y.year > TODAY.year:
            assert y.value == R.value_at(base, Decimal("-0.100"), y.year - TODAY.year)
    assert fallen.sale.net_proceeds < typed.sale.net_proceeds


def test_a_flat_sold_in_the_due_month_counts_its_proceeds_one_sold_before_has_no_card(world):
    _followed_world(world)
    _sale_in(world, 2028)  # 31 December 2028, the goal's due month
    plan_inputs.clear()
    got = _read(world)
    assert got.shown
    [card] = got.properties
    assert card.sale_year == 2028

    _follow(world, None, goal=_goal("330000.00", due="2029-12"))
    plan_inputs.clear()
    after = _read(world)
    assert after.shown and after.properties == []
