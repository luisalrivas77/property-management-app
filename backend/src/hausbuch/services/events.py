"""Appending to and reading the event log (principle 4).

append_event validates the whole envelope with the Event schema before any SQL runs, so a bad
event never reaches the database; the stored row is then read back through Event again.

A correction (corrects_event_id) is a new event that repeats the corrected event's type,
entity and History scope, with the full corrected payload. Each event is corrected at most
once (UNIQUE in the database), so only the latest event of a chain A -> B -> C can be corrected.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Connection, insert, select
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import event as event_table
from hausbuch.rules.validation import check_correction
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EVENT_CATALOG, Event, EventRef, EventType, FieldChange

from .context import CommandContext
from .errors import violated_constraint


class InvalidEventError(ValueError):
    """The event would break the catalog's rules; nothing was written.

    code: the stable rule code when a validation rule failed (event.correction_mismatch).
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class EventNotFoundError(LookupError):
    """No such event in the current workspace (or RLS hides it)."""


# History scope (schemas/events.py): property_id on every event of a property, its units,
# tenancies and loans; unit_id additionally on unit and tenancy events. Owner, tenant and
# document events may carry either, but need neither.
REQUIRED_SCOPE: dict[EntityType, tuple[str, ...]] = {
    EntityType.PROPERTY: ("property_id",),
    EntityType.LOAN: ("property_id",),
    EntityType.UNIT: ("property_id", "unit_id"),
    EntityType.TENANCY: ("property_id", "unit_id"),
}

_CORRECTED_ONCE = "event_corrects_event_id_key"


def append_event(
    ctx: CommandContext,
    type: EventType,
    entity_id: UUID,
    payload: Schema,
    *,
    property_id: UUID | None = None,
    unit_id: UUID | None = None,
    effective_date: date | None = None,
    corrects_event_id: UUID | None = None,
) -> Event:
    try:
        event_type = EventType(type)
    except ValueError:
        raise InvalidEventError(f"unknown event type {type!r}") from None
    spec = EVENT_CATALOG[event_type]
    if not isinstance(payload, spec.payload):
        got = payload.__class__.__name__
        raise InvalidEventError(f"{event_type} needs a {spec.payload.__name__} payload, got {got}")
    if unit_id is not None and property_id is None:
        raise InvalidEventError("an event scoped to a unit needs its property_id")
    entity_type = EntityType(event_type.split(".", 1)[0])
    scope = {"property_id": property_id, "unit_id": unit_id}
    missing = [name for name in REQUIRED_SCOPE.get(entity_type, ()) if scope[name] is None]
    if missing:
        raise InvalidEventError(f"{event_type} needs {' and '.join(missing)} (History scope)")
    if entity_type is EntityType.UNIT and unit_id != entity_id:
        raise InvalidEventError(f"{event_type} must be scoped to its own unit (unit_id)")

    row = {
        "id": uuid4(),
        "workspace_id": ctx.workspace_id,
        "type": event_type.value,
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "property_id": property_id,
        "unit_id": unit_id,
        "effective_date": effective_date,
        "recorded_at": ctx.now,
        "actor_type": ctx.actor.actor_type.value,
        "actor_id": ctx.actor.actor_id,
        "correlation_id": ctx.correlation_id,
        "corrects_event_id": corrects_event_id,
        "payload": payload.model_dump(mode="json"),
    }
    try:
        # seq is assigned by the database; 1 is a placeholder so the envelope can be checked.
        candidate = Event(**row, seq=1)
    except ValidationError as exc:
        raise InvalidEventError(f"invalid {event_type} event: {exc}") from exc

    if corrects_event_id is not None:
        # Reads only; nothing is written unless the correction is valid.
        _check_correction(ctx.conn, candidate)

    try:
        stored = ctx.conn.execute(insert(event_table).values(row).returning(*event_table.c)).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _CORRECTED_ONCE:
            # A concurrent command corrected the same event first.
            raise InvalidEventError(
                f"event {corrects_event_id} was corrected concurrently; correct the latest "
                "event in its chain"
            ) from exc
        raise
    return Event.model_validate(dict(stored._mapping))


def _check_correction(conn: Connection, correction: Event) -> None:
    original = get_event(conn, correction.corrects_event_id)
    if original is None:
        raise InvalidEventError(f"corrected event {correction.corrects_event_id} not found")
    codes = check_correction(_ref(original), _ref(correction))
    if codes:
        raise InvalidEventError(
            f"a correction of {original.type} {original.entity_id} must repeat its type and "
            f"entity, got {correction.type} {correction.entity_id}",
            code=codes[0],
        )
    if (correction.property_id, correction.unit_id) != (original.property_id, original.unit_id):
        raise InvalidEventError(
            "a correction must keep the corrected event's History scope (property_id, unit_id)"
        )
    successor = _successor(conn, original.id)
    if successor is not None:
        latest = latest_in_chain(conn, successor.id)
        raise InvalidEventError(
            f"event {original.id} is already corrected; correct the latest event in its "
            f"chain ({latest.id})"
        )


def _ref(e: Event) -> EventRef:
    return EventRef(type=e.type, entity_type=e.entity_type, entity_id=e.entity_id)


def get_event(conn: Connection, event_id: UUID) -> Event | None:
    row = conn.execute(select(event_table).where(event_table.c.id == event_id)).one_or_none()
    return None if row is None else Event.model_validate(dict(row._mapping))


def latest_in_chain(conn: Connection, event_id: UUID) -> Event:
    """The event that is true now for the chain `event_id` belongs to, following corrections
    forward (A -> B -> C gives C for A, B or C). Raises EventNotFoundError if it is unknown."""
    current = get_event(conn, event_id)
    if current is None:
        raise EventNotFoundError(f"event {event_id} not found")
    while (successor := _successor(conn, current.id)) is not None:
        current = successor
    return current


def milestone_root(conn: Connection, type: EventType, entity_id: UUID) -> Event:
    """The first event of the single chain of `type` for an entity: a milestone recorded
    once per entity, such as property.purchase_notarized or tenancy.started."""
    rows = conn.execute(
        select(event_table)
        .where(
            event_table.c.type == EventType(type).value,
            event_table.c.entity_id == entity_id,
            event_table.c.corrects_event_id.is_(None),
        )
        .order_by(event_table.c.seq)
    ).all()
    if not rows:
        raise EventNotFoundError(f"no {type} event for {entity_id}")
    if len(rows) > 1:
        raise InvalidEventError(f"{entity_id} has {len(rows)} {type} chains; expected one")
    return Event.model_validate(dict(rows[0]._mapping))


def find_event(
    conn: Connection, type: EventType, entity_id: UUID, *, payload_contains: Mapping[str, Any]
) -> Event | None:
    """The one event of `type` for an entity whose payload contains these JSON values, e.g.
    the tenancy.rent_scheduled event of one rent row ({"rent_schedule_id": "..."})."""
    rows = conn.execute(
        select(event_table).where(
            event_table.c.type == EventType(type).value,
            event_table.c.entity_id == entity_id,
            event_table.c.payload.contains(_JSON.dump_python(payload_contains, mode="json")),
        )
    ).all()
    if len(rows) > 1:
        raise InvalidEventError(f"{len(rows)} {type} events of {entity_id} match; expected one")
    return Event.model_validate(dict(rows[0]._mapping)) if rows else None


def _successor(conn: Connection, event_id: UUID) -> Event | None:
    row = conn.execute(
        select(event_table).where(event_table.c.corrects_event_id == event_id)
    ).one_or_none()
    return None if row is None else Event.model_validate(dict(row._mapping))


def list_events(
    conn: Connection,
    *,
    entity_type: EntityType | None = None,
    entity_id: UUID | None = None,
    property_id: UUID | None = None,
) -> list[Event]:
    """Events visible in the current workspace, in recording order (the audit trail)."""
    query = select(event_table).order_by(event_table.c.seq)
    if entity_type is not None:
        query = query.where(event_table.c.entity_type == EntityType(entity_type).value)
    if entity_id is not None:
        query = query.where(event_table.c.entity_id == entity_id)
    if property_id is not None:
        query = query.where(event_table.c.property_id == property_id)
    return [Event.model_validate(dict(r._mapping)) for r in conn.execute(query)]


_JSON = TypeAdapter(Any)


def diff_changes(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, FieldChange]:
    """The fields of `new` whose value differs from `old` (missing in `old` counts as None),
    as JSON-safe old/new pairs for an `*.updated` payload. Decimal and date become strings."""
    return {
        key: FieldChange(
            old=_JSON.dump_python(old.get(key), mode="json"),
            new=_JSON.dump_python(value, mode="json"),
        )
        for key, value in new.items()
        if old.get(key) != value
    }
