"""Which tenancies a tenant was ever a party of, for retention.

tenancy_tenant holds the parties now; set_parties deletes the row of a tenant who is removed.
Retention must still follow that tenancy (F1, decided 6 Nov 2026): removal is bookkeeping,
not the end of the claims period. So the current rows are joined with the tenancies the event
log names for the tenant: tenancy.created (tenant_ids) and tenancy.parties_set
(parties[].tenant_id). Both carry ids only (D5). The event table is under RLS like every
other, so only the current workspace's events are read.
"""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Connection, and_, or_, select

from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import tenancy_tenant as party_table
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EventType


def tenancies_ever_of(conn: Connection, tenant_ids: Collection[UUID]) -> set[UUID]:
    """Every tenancy any of `tenant_ids` is or was a party of."""
    ids = sorted({str(t) for t in tenant_ids})
    if not ids:
        return set()
    current = set(
        conn.execute(
            select(party_table.c.tenancy_id).where(party_table.c.tenant_id.in_(ids))
        ).scalars()
    )
    named = []
    for tenant_id in ids:
        named.append(
            and_(
                event_table.c.type == EventType.TENANCY_CREATED.value,
                event_table.c.payload.contains({"tenant_ids": [tenant_id]}),
            )
        )
        named.append(
            and_(
                event_table.c.type == EventType.TENANCY_PARTIES_SET.value,
                event_table.c.payload.contains({"parties": [{"tenant_id": tenant_id}]}),
            )
        )
    logged = set(
        conn.execute(
            select(event_table.c.entity_id)
            .where(event_table.c.entity_type == EntityType.TENANCY.value, or_(*named))
            .distinct()
        ).scalars()
    )
    return current | logged
