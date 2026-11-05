"""Unit commands end to end, as hausbuch_app under RLS."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import ActorType, PropertyKind
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import ConflictError, NotFoundError
from hausbuch.services.events import list_events
from hausbuch.services.properties import create_property
from hausbuch.services.units import create_unit, get_unit, list_units, update_unit

CREATED_AT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
PROPERTY = PropertyCreate(
    label="Wohnung Lindenstraße",
    kind=PropertyKind.ETW,
    address=Address(street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"),
    build_year=1994,
    notarized_on=date(2024, 3, 15),
    purchase_date=date(2024, 5, 1),
    purchase_price=Decimal("289000.00"),
)
MEA = Fraction(numerator=85, denominator=1000)


def _flat(property_id):
    return UnitCreate(
        property_id=property_id,
        label="WE 3",
        floor_area_m2=Decimal("68.40"),
        rooms=Decimal("2.5"),
        mea=MEA,
    )


def _unit_events(read, ws, unit_id):
    return read(ws, list_events, entity_id=unit_id)


def test_create_writes_the_row_and_a_scoped_event(app_engine, fresh_workspaces, run, read):
    a, _ = fresh_workspaces
    prop = run(a, create_property, PROPERTY, now=CREATED_AT)
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        flat = create_unit(ctx, _flat(prop.id))

    assert flat.model_dump(include=set(UnitCreate.model_fields)) == _flat(prop.id).model_dump()
    assert read(a, get_unit, flat.id) == flat
    assert read(a, list_units, prop.id) == [flat]
    [created] = _unit_events(read, a, flat.id)
    assert created.type is EventType.UNIT_CREATED
    assert (created.property_id, created.unit_id) == (prop.id, flat.id)
    assert created.correlation_id == ctx.correlation_id
    assert created.payload == {
        "label": "WE 3",
        "floor_area_m2": "68.40",
        "rooms": "2.5",
        "mea": {"numerator": 85, "denominator": 1000},
    }


def test_a_unit_without_rooms_or_mea(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    prop = run(a, create_property, PROPERTY, now=CREATED_AT)
    parking = UnitCreate(
        property_id=prop.id, label="TG-Stellplatz 12", floor_area_m2=Decimal("12.50")
    )
    unit = run(a, create_unit, parking, now=CREATED_AT)
    assert (unit.rooms, unit.mea) == (None, None)
    assert _unit_events(read, a, unit.id)[0].payload == {
        "label": "TG-Stellplatz 12",
        "floor_area_m2": "12.50",
        "rooms": None,
        "mea": None,
    }


def test_update_records_changes_and_none_clears(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    prop = run(a, create_property, PROPERTY, now=CREATED_AT)
    flat = run(a, create_unit, _flat(prop.id), now=CREATED_AT)

    updated = run(
        a, update_unit, flat.id, floor_area_m2=Decimal("68.4"), rooms=None, mea=MEA, now=LATER
    )

    assert (updated.rooms, updated.floor_area_m2, updated.updated_at) == (
        None,
        Decimal("68.40"),
        LATER,
    )
    changed = _unit_events(read, a, flat.id)[-1]
    assert changed.type is EventType.UNIT_UPDATED
    assert (changed.property_id, changed.unit_id) == (prop.id, flat.id)
    assert changed.payload["changes"] == {"rooms": {"old": "2.5", "new": None}}
    assert run(a, update_unit, flat.id, label="WE 3", now=LATER) == updated
    assert len(_unit_events(read, a, flat.id)) == 2


def test_domain_errors_write_nothing(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    prop = run(a, create_property, PROPERTY, now=CREATED_AT)
    flat = run(a, create_unit, _flat(prop.id), now=CREATED_AT)
    other = run(a, create_unit, _flat(prop.id).model_copy(update={"label": "WE 4"}))

    with pytest.raises(ConflictError, match="WE 3"):
        run(a, create_unit, _flat(prop.id))
    with pytest.raises(ConflictError, match="WE 3"):
        run(a, update_unit, other.id, label="WE 3")
    with pytest.raises(NotFoundError, match="property"):
        run(a, create_unit, _flat(uuid.uuid4()))
    with pytest.raises(ValidationError):
        run(a, update_unit, flat.id, floor_area_m2=Decimal("0"))

    assert [u.label for u in read(a, list_units, prop.id)] == ["WE 3", "WE 4"]
    assert len(_unit_events(read, a, other.id)) == 1
    assert read(a, get_unit, flat.id) == flat


def test_unit_is_invisible_from_another_workspace(run, read, fresh_workspaces):
    a, b = fresh_workspaces
    prop = run(a, create_property, PROPERTY, now=CREATED_AT)
    flat = run(a, create_unit, _flat(prop.id), now=CREATED_AT)
    assert read(b, get_unit, flat.id) is None
    with pytest.raises(NotFoundError):
        run(b, update_unit, flat.id, label="Mallory")
    with pytest.raises(NotFoundError, match="property"):
        run(b, create_unit, _flat(prop.id).model_copy(update={"label": "WE 9"}))
    assert read(a, get_unit, flat.id) == flat
