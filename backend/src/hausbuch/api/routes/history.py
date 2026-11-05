"""History and audit trail: two views of the same event log (principle 4).

Endpoints: docs/phases/phase-1-step-3.md §4.2 and §4.8. Both are plain reads of
services.history; the frontend renders TimelineEntry.payload per EventType (§9.4).
"""

from uuid import UUID

from sqlalchemy import Connection

from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import Event, TimelineEntry
from hausbuch.services.history import audit_trail, property_history
from hausbuch.services.properties import get_property

from ..deps import Cmd, domain_router, require

router = domain_router()


@router.get("/properties/{property_id}/history")
def history_of_property(
    property_id: UUID, cmd: Cmd, unit_id: UUID | None = None
) -> list[TimelineEntry]:
    """Milestones, corrections collapsed, by (effective date, root event seq). With unit_id,
    that unit's History, which keeps the property-level milestones (purchase, loans)."""
    return cmd.read(property_history, property_id, unit_id)


def _property_events(conn: Connection, property_id: UUID) -> list[Event]:
    require(conn, get_property, EntityType.PROPERTY, property_id)
    return audit_trail(conn, property_id=property_id)


@router.get("/properties/{property_id}/events")
def events_of_property(property_id: UUID, cmd: Cmd) -> list[Event]:
    """The property's audit trail: every event in seq order, corrections included."""
    return cmd.read(_property_events, property_id)


@router.get("/events")
def events_list(
    cmd: Cmd,
    property_id: UUID | None = None,
    entity_type: EntityType | None = None,
    entity_id: UUID | None = None,
) -> list[Event]:
    """Every event of the workspace in seq order, optionally filtered."""
    return cmd.read(
        audit_trail, property_id=property_id, entity_type=entity_type, entity_id=entity_id
    )
