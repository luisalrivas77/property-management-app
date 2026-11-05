"""Owner commands: current-state row plus owner.* events in the same transaction (D4)."""

from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from sqlalchemy import Connection, insert, select, update

from hausbuch.db.tables import owner as owner_table
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EventType, OwnerCreated, Updated
from hausbuch.schemas.property import Owner, OwnerCreate

from .context import CommandContext
from .errors import NotFoundError
from .events import append_event, diff_changes


class _Unset(Enum):
    UNSET = "UNSET"


# "Not passed". None is a real value for tax_id_ref and marginal_tax_rate (clears it).
UNSET = _Unset.UNSET
Unset = Literal[_Unset.UNSET]


def create_owner(ctx: CommandContext, data: OwnerCreate) -> Owner:
    row = ctx.conn.execute(
        insert(owner_table)
        .values(
            workspace_id=ctx.workspace_id,
            **data.model_dump(),
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*owner_table.c)
    ).one()
    created = Owner.model_validate(dict(row._mapping))
    append_event(
        ctx,
        EventType.OWNER_CREATED,
        created.id,
        OwnerCreated(marginal_tax_rate=created.marginal_tax_rate),
    )
    return created


def update_owner(
    ctx: CommandContext,
    owner_id: UUID,
    *,
    full_name: str | Unset = UNSET,
    tax_id_ref: str | None | Unset = UNSET,
    marginal_tax_rate: Decimal | None | Unset = UNSET,
) -> Owner:
    """Change only the fields passed. Writes nothing and appends no event if nothing changes."""
    requested = {
        name: value
        for name, value in (
            ("full_name", full_name),
            ("tax_id_ref", tax_id_ref),
            ("marginal_tax_rate", marginal_tax_rate),
        )
        if value is not UNSET
    }
    current = _select_owner(ctx.conn, owner_id, for_update=True)
    if current is None:
        raise NotFoundError(EntityType.OWNER, owner_id)

    fields = set(requested)
    old = current.model_dump(include=fields)
    # Validate the result as a whole owner, with the same rules as on create.
    new = OwnerCreate.model_validate(
        current.model_dump(include=set(OwnerCreate.model_fields)) | requested
    )
    new_values = new.model_dump(include=fields)
    if not diff_changes(old, new_values):
        return current

    row = ctx.conn.execute(
        update(owner_table)
        .where(owner_table.c.id == owner_id)
        .values(**new_values, updated_at=ctx.now)
        .returning(*owner_table.c)
    ).one()
    updated = Owner.model_validate(dict(row._mapping))
    # Diff against the stored row, so the event shows values exactly as Postgres keeps them.
    changes = diff_changes(old, updated.model_dump(include=fields))
    append_event(ctx, EventType.OWNER_UPDATED, owner_id, Updated(changes=changes))
    return updated


def get_owner(conn: Connection, owner_id: UUID) -> Owner | None:
    return _select_owner(conn, owner_id, for_update=False)


def list_owners(conn: Connection) -> list[Owner]:
    """Every owner of the workspace, by name."""
    rows = conn.execute(select(owner_table).order_by(owner_table.c.full_name, owner_table.c.id))
    return [Owner.model_validate(dict(r._mapping)) for r in rows]


def _select_owner(conn: Connection, owner_id: UUID, *, for_update: bool) -> Owner | None:
    query = select(owner_table).where(owner_table.c.id == owner_id)
    if for_update:
        query = query.with_for_update()
    row = conn.execute(query).one_or_none()
    return None if row is None else Owner.model_validate(dict(row._mapping))
