"""append_event and list_events against Postgres, as hausbuch_app under RLS."""

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import text

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.enums import ActorType, EntityType
from hausbuch.schemas.events import (
    DepositReceived,
    Empty,
    EventType,
    LoanDisbursed,
    OwnerCreated,
    UnitCreated,
    Updated,
)
from hausbuch.services.context import Actor, new_command
from hausbuch.services.events import InvalidEventError, append_event, list_events

NOW = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")


@contextmanager
def recorded_sql(conn):
    """Collects every statement sent to the database on `conn` while the block runs."""
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    sa_event.listen(conn, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sa_event.remove(conn, "before_cursor_execute", record)


def _property_scope():
    return {"property_id": uuid.uuid4(), "unit_id": uuid.uuid4()}


REJECTED = {
    "effective-date-forbidden": (
        "must not carry effective_date",
        dict(type=EventType.OWNER_CREATED, payload=OwnerCreated(), effective_date=date(2026, 1, 1)),
    ),
    "effective-date-required": (
        "needs effective_date",
        dict(type=EventType.TENANCY_STARTED, payload=Empty(), **_property_scope()),
    ),
    "wrong-payload-model": (
        "needs a Updated payload, got Empty",
        dict(type=EventType.OWNER_UPDATED, payload=Empty()),
    ),
    "payload-fails-model": (
        "at least 1 item",
        dict(type=EventType.OWNER_UPDATED, payload=Updated.model_construct(changes={})),
    ),
    "negative-money": (
        "greater than 0",
        dict(
            type=EventType.TENANCY_DEPOSIT_RECEIVED,
            payload=DepositReceived.model_construct(amount=Decimal("-1.00")),
            effective_date=date(2026, 1, 1),
            **_property_scope(),
        ),
    ),
    "unknown-type": ("unknown event type", dict(type="owner.deleted", payload=Empty())),
    "unit-without-property": (
        "needs its property_id",
        dict(
            type=EventType.TENANCY_STARTED,
            payload=Empty(),
            unit_id=uuid.uuid4(),
            effective_date=date(2026, 1, 1),
        ),
    ),
    "tenancy-without-property": (
        "needs property_id and unit_id",
        dict(type=EventType.TENANCY_STARTED, payload=Empty(), effective_date=date(2026, 1, 1)),
    ),
    "unit-event-without-unit": (
        "needs unit_id",
        dict(
            type=EventType.UNIT_CREATED,
            payload=UnitCreated(label="WE 3", floor_area_m2=Decimal("68.40")),
            property_id=uuid.uuid4(),
        ),
    ),
    "loan-without-property": (
        "needs property_id",
        dict(
            type=EventType.LOAN_DISBURSED,
            payload=LoanDisbursed(amount=Decimal("240000.00")),
            effective_date=date(2024, 4, 30),
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(REJECTED))
def test_rejects_a_bad_event_before_any_sql(app_engine, workspaces, case):
    a, _ = workspaces
    reason, kwargs = REJECTED[case]
    kwargs = dict(kwargs)
    entity_id = uuid.uuid4()
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        with recorded_sql(conn) as statements:
            with pytest.raises(InvalidEventError, match=reason):
                append_event(ctx, kwargs.pop("type"), entity_id, kwargs.pop("payload"), **kwargs)
            assert statements == []

            # The transaction is still usable, and the rejected event left no row.
            assert list_events(conn, entity_id=entity_id) == []
            append_event(ctx, EventType.OWNER_CREATED, entity_id, OwnerCreated())
            # Positive control: the recorder does see statements on this connection.
            assert any("INSERT INTO event" in s for s in statements), statements
        assert [e.type for e in list_events(conn, entity_id=entity_id)] == ["owner.created"]


def test_appends_a_scoped_milestone_and_reads_it_back(app_engine, workspaces):
    a, b = workspaces
    property_id, unit_id, tenancy_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        started = append_event(
            ctx,
            EventType.TENANCY_STARTED,
            tenancy_id,
            Empty(),
            property_id=property_id,
            unit_id=unit_id,
            effective_date=date(2024, 7, 1),
        )
        deposit = append_event(
            ctx,
            EventType.TENANCY_DEPOSIT_RECEIVED,
            tenancy_id,
            DepositReceived(amount=Decimal("1900.00")),
            property_id=property_id,
            unit_id=unit_id,
            effective_date=date(2024, 7, 3),
        )

    assert started.entity_type is EntityType.TENANCY
    assert started.workspace_id == a
    assert started.recorded_at == NOW
    assert (started.actor_type, started.actor_id) == (ActorType.USER, "user:test")
    assert started.correlation_id == deposit.correlation_id == ctx.correlation_id
    assert started.corrects_event_id is None
    assert started.payload == {} and started.payload_version == 1
    assert deposit.seq > started.seq
    assert deposit.payload == {"amount": "1900.00"}

    with workspace_transaction(app_engine, a) as conn:
        assert list_events(conn, property_id=property_id) == [started, deposit]
        assert list_events(conn, entity_type=EntityType.TENANCY, entity_id=tenancy_id) == [
            started,
            deposit,
        ]
        assert list_events(conn, entity_type=EntityType.OWNER, entity_id=tenancy_id) == []
        # Money is stored as a JSON string, never a float.
        kind = conn.execute(
            text("SELECT jsonb_typeof(payload->'amount') FROM event WHERE id = :id"),
            {"id": deposit.id},
        ).scalar_one()
        assert kind == "string"

    with workspace_transaction(app_engine, b) as conn:
        assert list_events(conn, property_id=property_id) == []
