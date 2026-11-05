"""History and audit trail: two views of the same event log (principle 4).

History = milestones of a property (or one of its units), corrections collapsed, built by the
pure rules.timeline.build_history. Audit trail = every event in recording (seq) order.
"""

from uuid import UUID

from sqlalchemy import Connection, or_, select

from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.rules.timeline import build_history
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import Event, TimelineEntry, TimelineScope

from .errors import NotFoundError
from .events import list_events
from .lookup import require_row


def property_history(
    conn: Connection, property_id: UUID, unit_id: UUID | None = None
) -> list[TimelineEntry]:
    """The property's History, or with unit_id that unit's (which keeps property-level
    milestones such as the purchase and loans). Ordered by (effective date, root event seq)."""
    require_row(conn, property_table, EntityType.PROPERTY, property_id)
    if unit_id is not None:
        unit = require_row(conn, unit_table, EntityType.UNIT, unit_id)
        if unit.property_id != property_id:
            raise NotFoundError(EntityType.UNIT, unit_id)
    scope = TimelineScope(property_id=property_id, unit_id=unit_id)
    return build_history(_events_with_chains(conn, property_id), scope)


def audit_trail(
    conn: Connection,
    *,
    property_id: UUID | None = None,
    entity_type: EntityType | None = None,
    entity_id: UUID | None = None,
) -> list[Event]:
    """Every event in seq order, corrections included (the audit view of the same log)."""
    return list_events(conn, property_id=property_id, entity_type=entity_type, entity_id=entity_id)


def _events_with_chains(conn: Connection, property_id: UUID) -> list[Event]:
    """The property's events plus every event of any correction chain they belong to.

    build_history needs whole chains. append_event keeps a chain inside one History scope, so
    today this adds nothing; the closure keeps History correct (rather than failing) for any
    chain that crosses scopes, which the database itself does not forbid.
    """
    e = event_table
    chain = (
        select(e.c.id, e.c.corrects_event_id)
        .where(e.c.property_id == property_id)
        .cte("chain", recursive=True)
    )
    step = e.alias("step")
    chain = chain.union(
        select(step.c.id, step.c.corrects_event_id).join(
            chain,
            or_(step.c.corrects_event_id == chain.c.id, step.c.id == chain.c.corrects_event_id),
        )
    )
    rows = conn.execute(select(e).where(e.c.id.in_(select(chain.c.id))).order_by(e.c.seq))
    return [Event.model_validate(dict(r._mapping)) for r in rows]
