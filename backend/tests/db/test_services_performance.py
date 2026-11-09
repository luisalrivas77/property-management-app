"""services.performance under hausbuch_app and RLS (phase-4.md D85–D87, §8): actuals and the
projection computed on read from the stored property, loans and scenarios, nothing written,
the error codes, and workspace isolation."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from sqlalchemy import func, select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.rules.errors import (
    PROJECTION_NO_RULE_SET,
    SCENARIO_SALE_YEAR_BEFORE_TODAY,
    RuleError,
)
from hausbuch.schemas.performance import Levers, ScenarioCreate, YearFlag, YearKind
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.performance import performance_actuals, performance_projection
from hausbuch.services.properties import create_property
from hausbuch.services.scenarios import create_scenario, list_scenarios

TODAY = date(2026, 11, 7)  # World's clock


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _flat_2026(world: World) -> uuid.UUID:
    """A flat bought in 2026 (World's own was bought in 2024, before the first rule set)."""
    return world.cmd(
        create_property,
        PropertyCreate(
            label="Wohnung Ring",
            kind="etw",
            address=Address(street="Ring", house_number="7", postal_code="10115", city="Berlin"),
            build_year=2001,
            notarized_on=date(2026, 3, 10),
            purchase_date=date(2026, 7, 1),
            purchase_price=Decimal("300000.00"),
        ),
    ).id


def _events(world: World) -> int:
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(select(func.count()).select_from(event_table)).scalar_one()


def test_actuals_run_from_the_purchase_year_through_today(world):
    pid = _flat_2026(world)
    got = world.query(performance_actuals, pid, today=TODAY)
    assert got.property_id == pid
    assert [(y.year, y.kind) for y in got.years] == [(2026, YearKind.ACTUAL_PARTIAL)]
    (year,) = got.years
    assert year.rent_in == Decimal("0.00")
    assert year.tax_effect is None  # no income inputs recorded
    assert {YearFlag.SO_FAR, YearFlag.INCOME_MISSING} <= set(year.flags)


def test_the_projection_without_a_scenario_sells_in_the_first_tax_free_year(world):
    pid = _flat_2026(world)
    world.loan(
        property_id=pid,
        iban=None,
        principal=Decimal("240000.00"),
        disbursed_on=date(2026, 6, 15),
        first_payment_on=date(2026, 7, 30),
        fixed_until=date(2036, 6, 30),
    )
    events = _events(world)
    got = world.query(performance_projection, pid, today=TODAY)
    assert got.scenario == Levers(sale_year=2036)  # contract of 10 Mar 2026
    assert got.scenario_id is None
    assert [y.year for y in got.years] == list(range(2027, 2037))
    assert all(y.kind is YearKind.PROJECTED for y in got.years)
    assert got.years[0].interest > 0 and got.years[0].loan_balance < Decimal("240000.00")
    assert not got.sale.taxable
    assert got.sale.initial_equity == Decimal("60000.00")  # 300,000 − 240,000
    assert _events(world) == events  # computed on read: nothing written


def test_the_default_scenario_and_per_request_overrides(world):
    pid = _flat_2026(world)
    default = world.cmd(
        create_scenario,
        pid,
        ScenarioCreate.model_validate(
            {
                "property_id": pid,
                "name": "Basis",
                "is_default": True,
                "levers": {"sale_year": 2032, "rent_growth": "0.03"},
            }
        ),
    )
    got = world.query(performance_projection, pid, today=TODAY)
    assert (got.scenario_id, got.scenario.sale_year) == (default.id, 2032)
    assert got.sale.taxable  # inside the ten years

    got = world.query(
        performance_projection,
        pid,
        today=TODAY,
        overrides={"sale_year": 2037, "value_growth": Decimal("0.01")},
    )
    assert (got.scenario.sale_year, got.scenario.value_growth) == (2037, Decimal("0.01"))
    assert got.scenario.rent_growth == Decimal("0.03")  # the scenario's, not overridden
    assert world.query(list_scenarios, pid) == [default]  # the override wrote nothing

    with pytest.raises(RuleViolationError) as exc:
        world.query(performance_projection, pid, today=TODAY, overrides={"sale_year": 2026})
    assert exc.value.codes == (SCENARIO_SALE_YEAR_BEFORE_TODAY,)


def test_a_named_scenario_must_be_of_this_property(world):
    pid = _flat_2026(world)
    theirs = world.cmd(
        create_scenario,
        world.property_id,
        ScenarioCreate.model_validate(
            {"property_id": world.property_id, "name": "X", "levers": {"sale_year": 2036}}
        ),
    )
    with pytest.raises(NotFoundError):
        world.query(performance_projection, pid, today=TODAY, scenario_id=theirs.id)
    with pytest.raises(NotFoundError):
        world.query(performance_projection, pid, today=TODAY, scenario_id=uuid.uuid4())


def test_a_purchase_before_the_first_rule_set_is_refused(world):
    """World's flat was bought in 2024: no rule set reaches back that far, and the sale's
    AfA add-back and the profit need every owned year, so nothing is shown rather than a
    figure missing years."""
    with pytest.raises(RuleError) as exc:
        world.query(performance_actuals, world.property_id, today=TODAY)
    assert exc.value.code == PROJECTION_NO_RULE_SET
    with pytest.raises(RuleError) as exc:
        world.query(performance_projection, world.property_id, today=TODAY)
    assert exc.value.code == PROJECTION_NO_RULE_SET


def test_another_workspace_sees_nothing(world, app_engine, fresh_workspaces):
    pid = _flat_2026(world)
    row = world.cmd(
        create_scenario,
        pid,
        ScenarioCreate.model_validate(
            {"property_id": pid, "name": "Basis", "is_default": True, "levers": {"sale_year": 2036}}
        ),
    )
    other = World(app_engine, fresh_workspaces[1])
    with pytest.raises(NotFoundError):
        other.query(performance_actuals, pid, today=TODAY)
    with pytest.raises(NotFoundError):
        other.query(performance_projection, pid, today=TODAY)
    other_flat = _flat_2026(other)
    with pytest.raises(NotFoundError):  # their own flat, our scenario: RLS hides the row
        other.query(performance_projection, other_flat, today=TODAY, scenario_id=row.id)
    got = other.query(performance_projection, other_flat, today=TODAY)
    assert got.scenario_id is None  # our default does not leak into their projection
