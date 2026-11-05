"""Unit commands: current-state row plus unit.* events in the same transaction (D4).

Unit events carry the History scope of their property and of the unit itself.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EventType, UnitCreated, Updated
from hausbuch.schemas.property import Unit, UnitCreate

from .context import CommandContext
from .errors import ConflictError, violated_constraint
from .events import append_event, diff_changes
from .lookup import require_row
from .owners import UNSET, Unset

_LABEL_TAKEN = "unit_property_id_label_key"
_CREATE_FIELDS = set(UnitCreate.model_fields)


def create_unit(ctx: CommandContext, data: UnitCreate) -> Unit:
    require_row(ctx.conn, property_table, EntityType.PROPERTY, data.property_id)
    row = _write(
        ctx,
        insert(unit_table).values(
            workspace_id=ctx.workspace_id,
            **_columns(data.model_dump()),
            created_at=ctx.now,
            updated_at=ctx.now,
        ),
        data.label,
    )
    created = _to_unit(row)
    append_event(
        ctx,
        EventType.UNIT_CREATED,
        created.id,
        UnitCreated(
            label=created.label,
            floor_area_m2=created.floor_area_m2,
            rooms=created.rooms,
            mea=created.mea,
        ),
        property_id=created.property_id,
        unit_id=created.id,
    )
    return created


def update_unit(
    ctx: CommandContext,
    unit_id: UUID,
    *,
    label: str | Unset = UNSET,
    floor_area_m2: Decimal | Unset = UNSET,
    rooms: Decimal | None | Unset = UNSET,
    mea: Fraction | None | Unset = UNSET,
) -> Unit:
    """Change only the fields passed (None clears rooms or mea). No-op if nothing changes."""
    requested = {
        name: value
        for name, value in (
            ("label", label),
            ("floor_area_m2", floor_area_m2),
            ("rooms", rooms),
            ("mea", mea),
        )
        if value is not UNSET
    }
    current = _to_unit(require_row(ctx.conn, unit_table, EntityType.UNIT, unit_id, for_update=True))
    fields = set(requested)
    old = current.model_dump(include=fields)
    new = UnitCreate.model_validate(current.model_dump(include=_CREATE_FIELDS) | requested)
    new_values = new.model_dump(include=fields)
    if not diff_changes(old, new_values):
        return current

    row = _write(
        ctx,
        update(unit_table)
        .where(unit_table.c.id == unit_id)
        .values(**_columns(new_values), updated_at=ctx.now),
        new.label,
    )
    updated = _to_unit(row)
    changes = diff_changes(old, updated.model_dump(include=fields))
    append_event(
        ctx,
        EventType.UNIT_UPDATED,
        unit_id,
        Updated(changes=changes),
        property_id=updated.property_id,
        unit_id=unit_id,
    )
    return updated


def get_unit(conn: Connection, unit_id: UUID) -> Unit | None:
    row = conn.execute(select(unit_table).where(unit_table.c.id == unit_id)).one_or_none()
    return None if row is None else _to_unit(row)


def list_units(conn: Connection, property_id: UUID) -> list[Unit]:
    rows = conn.execute(
        select(unit_table)
        .where(unit_table.c.property_id == property_id)
        .order_by(unit_table.c.label)
    )
    return [_to_unit(r) for r in rows]


def _write(ctx: CommandContext, statement: Any, label: str) -> Row:
    try:
        return ctx.conn.execute(statement.returning(*unit_table.c)).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _LABEL_TAKEN:
            raise ConflictError(
                f"the property already has a unit labelled {label!r}", constraint=_LABEL_TAKEN
            ) from exc
        raise


def _columns(values: dict[str, Any]) -> dict[str, Any]:
    """Schema fields to columns: mea becomes its numerator and denominator."""
    values = dict(values)
    if "mea" in values:
        mea = values.pop("mea")
        values["mea_numerator"] = None if mea is None else mea["numerator"]
        values["mea_denominator"] = None if mea is None else mea["denominator"]
    return values


def _to_unit(row: Row) -> Unit:
    values = dict(row._mapping)
    numerator, denominator = values.pop("mea_numerator"), values.pop("mea_denominator")
    mea = None if numerator is None else Fraction(numerator=numerator, denominator=denominator)
    return Unit.model_validate(values | {"mea": mea})
