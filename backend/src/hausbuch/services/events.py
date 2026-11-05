"""Appending to and reading the event log (principle 4).

append_event validates the whole envelope with the Event schema before any SQL runs, so a bad
event never reaches the database; the stored row is then read back through Event again.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Connection, insert, select

from hausbuch.db.tables import event as event_table
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EVENT_CATALOG, Event, EventType, FieldChange

from .context import CommandContext


class InvalidEventError(ValueError):
    """The event would break the catalog's rules; nothing was written."""


def append_event(
    ctx: CommandContext,
    type: EventType,
    entity_id: UUID,
    payload: Schema,
    *,
    property_id: UUID | None = None,
    unit_id: UUID | None = None,
    effective_date: date | None = None,
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

    row = {
        "id": uuid4(),
        "workspace_id": ctx.workspace_id,
        "type": event_type.value,
        "entity_type": EntityType(event_type.split(".", 1)[0]).value,
        "entity_id": entity_id,
        "property_id": property_id,
        "unit_id": unit_id,
        "effective_date": effective_date,
        "recorded_at": ctx.now,
        "actor_type": ctx.actor.actor_type.value,
        "actor_id": ctx.actor.actor_id,
        "correlation_id": ctx.correlation_id,
        "payload": payload.model_dump(mode="json"),
    }
    try:
        # seq is assigned by the database; 1 is a placeholder so the envelope can be checked.
        Event(**row, seq=1)
    except ValidationError as exc:
        raise InvalidEventError(f"invalid {event_type} event: {exc}") from exc

    stored = ctx.conn.execute(insert(event_table).values(row).returning(*event_table.c)).one()
    return Event.model_validate(dict(stored._mapping))


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
