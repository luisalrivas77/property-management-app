"""services.scenarios under hausbuch_app and RLS (phase-4.md D87, §6, §8): append-only rows,
supersede and withdraw, one default in force per property, the sale year, the event in the
same transaction (without the name), and workspace isolation."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from sqlalchemy import select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import projection_scenario as scenario_table
from hausbuch.rules.errors import (
    SCENARIO_NOT_CURRENT,
    SCENARIO_SALE_YEAR_BEFORE_TODAY,
    SCENARIO_SCOPE_INVALID,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.performance import ScenarioCreate
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.properties import create_property
from hausbuch.services.scenarios import create_scenario, default_scenario, list_scenarios

# World's clock is 7 Nov 2026: a sale in 2026 or before has no projected year.


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _body(world: World, **overrides) -> ScenarioCreate:
    levers = {"sale_year": 2036} | overrides.pop("levers", {})
    return ScenarioCreate.model_validate(
        {"property_id": world.property_id, "name": "Basis", "levers": levers} | overrides
    )


def _create(world: World, body: ScenarioCreate):
    return world.cmd(create_scenario, world.property_id, body)


def _codes(exc_info) -> list[str]:
    return list(exc_info.value.codes)


def test_a_scenario_is_recorded_corrected_and_listed_with_its_event(world):
    first = _create(world, _body(world, is_default=True, name="Mein Plan"))
    assert first.levers.sale_year == 2036 and first.is_default
    assert world.query(default_scenario, world.property_id) == first
    second = _create(
        world,
        _body(world, is_default=True, supersedes_id=first.id, levers={"value_growth": "0.03"}),
    )
    assert world.query(list_scenarios, world.property_id) == [second]
    history = world.query(list_scenarios, world.property_id, include_history=True)
    assert [s.id for s in history] == [first.id, second.id]
    assert world.query(default_scenario, world.property_id) == second

    with workspace_transaction(world.engine, world.ws) as conn:
        events = conn.execute(
            select(event_table).where(
                event_table.c.type == EventType.PROPERTY_SCENARIO_RECORDED.value
            )
        ).all()
    assert [e.payload["scenario_id"] for e in events] == [str(first.id), str(second.id)]
    assert all(e.entity_id == world.property_id for e in events)
    assert "Mein Plan" not in str([e.payload for e in events])  # the name stays in the row


def test_a_withdrawal_ends_the_scenario_and_repeats_its_levers(world):
    row = _create(world, _body(world))
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _body(world, supersedes_id=row.id, withdrawn=True, levers={"sale_year": 2040}),
        )
    assert _codes(exc) == [SCENARIO_SCOPE_INVALID]
    _create(world, _body(world, supersedes_id=row.id, withdrawn=True))
    assert world.query(list_scenarios, world.property_id) == []
    # A withdrawn row cannot be superseded again.
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _body(world, supersedes_id=row.id))
    assert _codes(exc) == [SCENARIO_NOT_CURRENT]


def test_supersedes_must_be_in_force(world):
    first = _create(world, _body(world))
    _create(world, _body(world, supersedes_id=first.id))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _body(world, supersedes_id=first.id))
    assert _codes(exc) == [SCENARIO_NOT_CURRENT]
    with pytest.raises(NotFoundError):
        _create(world, _body(world, supersedes_id=uuid.uuid4()))


def test_at_most_one_default_in_force(world):
    default = _create(world, _body(world, is_default=True))
    other = _create(world, _body(world, name="Pessimistisch"))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _body(world, is_default=True, name="Zweiter"))
    assert _codes(exc) == [SCENARIO_NOT_CURRENT]
    with pytest.raises(RuleViolationError) as exc:  # promoting another one: same rule
        _create(world, _body(world, is_default=True, supersedes_id=other.id))
    assert _codes(exc) == [SCENARIO_NOT_CURRENT]
    # Demote the default first, then the other one may become it.
    _create(world, _body(world, supersedes_id=default.id))
    promoted = _create(world, _body(world, is_default=True, supersedes_id=other.id))
    assert world.query(default_scenario, world.property_id) == promoted


def test_the_sale_year_is_after_today(world):
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _body(world, levers={"sale_year": 2026}))
    assert _codes(exc) == [SCENARIO_SALE_YEAR_BEFORE_TODAY]
    assert world.query(list_scenarios, world.property_id, include_history=True) == []


def _second_property(world: World) -> uuid.UUID:
    return world.cmd(
        create_property,
        PropertyCreate(
            label="Wohnung Ring",
            kind="etw",
            address=Address(street="Ring", house_number="7", postal_code="10115", city="Berlin"),
            build_year=2001,
            notarized_on=date(2025, 2, 1),
            purchase_date=date(2025, 4, 1),
            purchase_price=Decimal("250000.00"),
        ),
    ).id


def test_scope_checks(world):
    second = _second_property(world)
    with pytest.raises(RuleViolationError) as exc:
        world.cmd(create_scenario, second, _body(world))
    assert _codes(exc) == [SCENARIO_SCOPE_INVALID]
    their_loan = world.loan(property_id=second, iban=None)
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _body(world, levers={"loans": [{"loan_id": str(their_loan)}]}))
    assert _codes(exc) == [SCENARIO_SCOPE_INVALID]
    their_row = world.cmd(
        create_scenario,
        second,
        ScenarioCreate.model_validate(
            {"property_id": second, "name": "X", "levers": {"sale_year": 2036}}
        ),
    )
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _body(world, supersedes_id=their_row.id))
    assert _codes(exc) == [SCENARIO_SCOPE_INVALID]
    mine = world.loan(iban=None)
    row = _create(
        world, _body(world, levers={"loans": [{"loan_id": str(mine), "follow_up_rate": "0.045"}]})
    )
    assert row.levers.loans[0].loan_id == mine


def test_another_workspace_sees_nothing_and_cannot_reference_rows(
    world, app_engine, fresh_workspaces
):
    row = _create(world, _body(world, is_default=True))
    other = World(app_engine, fresh_workspaces[1])
    with pytest.raises(NotFoundError):
        other.query(list_scenarios, world.property_id)
    with workspace_transaction(app_engine, other.ws) as conn:
        assert conn.execute(select(scenario_table)).all() == []
    assert other.query(default_scenario, world.property_id) is None
    with pytest.raises(NotFoundError):
        other.cmd(create_scenario, world.property_id, _body(world))
    with pytest.raises(NotFoundError):
        other.cmd(
            create_scenario,
            other.property_id,
            ScenarioCreate.model_validate(
                {
                    "property_id": other.property_id,
                    "name": "X",
                    "levers": {"sale_year": 2036},
                    "supersedes_id": str(row.id),
                }
            ),
        )
    assert other.query(list_scenarios, other.property_id, include_history=True) == []
