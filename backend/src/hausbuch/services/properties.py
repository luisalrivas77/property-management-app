"""Property commands: current-state rows plus property.* events in the same transaction (D4).

The two purchase milestones (D7) are dates in History: notarized_on (property.purchase_notarized,
starts the §23 Spekulationsfrist) and purchase_date (property.possession_transferred, AfA starts).
They change only through a correcting event, never through update_property.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, delete, insert, null, select, update

from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import property_acquisition_cost as cost_table
from hausbuch.db.tables import property_ownership as ownership_table
from hausbuch.db.tables import property_price_split as split_table
from hausbuch.rules.validation import check_ownership_shares
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import EntityType, PropertyKind
from hausbuch.schemas.events import (
    AcquisitionCostRecorded,
    Empty,
    EventType,
    OwnershipSet,
    PriceSplitRecorded,
    PriceSplitSelected,
    PropertyCreated,
    PurchaseNotarized,
    Updated,
)
from hausbuch.schemas.property import (
    AcquisitionCost,
    AcquisitionCostCreate,
    Address,
    OwnerShare,
    PriceSplit,
    PriceSplitCreate,
    Property,
    PropertyCreate,
    PropertyOwnership,
)

from .context import CommandContext
from .errors import NotFoundError, require_valid
from .events import append_event, diff_changes, latest_in_chain, milestone_root
from .lookup import require_document, require_row
from .owners import UNSET, Unset

_ADDRESS_COLUMNS = tuple(Address.model_fields)
_CREATE_FIELDS = set(PropertyCreate.model_fields)


def create_property(ctx: CommandContext, data: PropertyCreate) -> Property:
    """The property row plus property.created and both purchase milestones."""
    row = ctx.conn.execute(
        insert(property_table)
        .values(
            workspace_id=ctx.workspace_id,
            **_columns(data.model_dump()),
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*property_table.c)
    ).one()
    created = _to_property(row)
    scope = {"property_id": created.id}
    append_event(
        ctx,
        EventType.PROPERTY_CREATED,
        created.id,
        PropertyCreated(
            label=created.label,
            kind=created.kind,
            build_year=created.build_year,
            purchase_price=created.purchase_price,
        ),
        **scope,
    )
    append_event(
        ctx,
        EventType.PROPERTY_PURCHASE_NOTARIZED,
        created.id,
        PurchaseNotarized(purchase_price=created.purchase_price),
        effective_date=created.notarized_on,
        **scope,
    )
    append_event(
        ctx,
        EventType.PROPERTY_POSSESSION_TRANSFERRED,
        created.id,
        Empty(),
        effective_date=created.purchase_date,
        **scope,
    )
    return created


def update_property(
    ctx: CommandContext,
    property_id: UUID,
    *,
    label: str | Unset = UNSET,
    kind: PropertyKind | Unset = UNSET,
    address: Address | Unset = UNSET,
    build_year: int | Unset = UNSET,
) -> Property:
    """Change non-milestone fields. The purchase dates and price change only through
    correct_notarized_on / correct_purchase_date. No-op (no write, no event) if nothing changes.
    """
    requested = {
        name: value
        for name, value in (
            ("label", label),
            ("kind", kind),
            ("address", address),
            ("build_year", build_year),
        )
        if value is not UNSET
    }
    current = _to_property(_lock(ctx.conn, property_id))
    fields = set(requested)
    old = current.model_dump(include=fields)
    new = PropertyCreate.model_validate(current.model_dump(include=_CREATE_FIELDS) | requested)
    new_values = new.model_dump(include=fields)
    if not diff_changes(old, new_values):
        return current

    updated = _update_row(ctx, property_id, new_values)
    changes = diff_changes(old, updated.model_dump(include=fields))
    append_event(
        ctx,
        EventType.PROPERTY_UPDATED,
        property_id,
        Updated(changes=changes),
        property_id=property_id,
    )
    return updated


def correct_notarized_on(
    ctx: CommandContext,
    property_id: UUID,
    new_date: date,
    *,
    purchase_price: Decimal | None = None,
) -> Property:
    """Correct the notarization date (and optionally the price it records): updates the row
    and appends a correcting property.purchase_notarized against the latest in its chain."""
    current = _to_property(_lock(ctx.conn, property_id))
    price = current.purchase_price if purchase_price is None else purchase_price
    PropertyCreate.model_validate(
        current.model_dump(include=_CREATE_FIELDS)
        | {"notarized_on": new_date, "purchase_price": price}
    )
    if (new_date, price) == (current.notarized_on, current.purchase_price):
        return current

    root = milestone_root(ctx.conn, EventType.PROPERTY_PURCHASE_NOTARIZED, property_id)
    latest = latest_in_chain(ctx.conn, root.id)
    updated = _update_row(ctx, property_id, {"notarized_on": new_date, "purchase_price": price})
    append_event(
        ctx,
        EventType.PROPERTY_PURCHASE_NOTARIZED,
        property_id,
        PurchaseNotarized(purchase_price=updated.purchase_price),
        property_id=property_id,
        effective_date=updated.notarized_on,
        corrects_event_id=latest.id,
    )
    return updated


def correct_purchase_date(ctx: CommandContext, property_id: UUID, new_date: date) -> Property:
    """Correct the transfer of Besitz, Nutzen und Lasten: updates the row and appends a
    correcting property.possession_transferred against the latest in its chain."""
    current = _to_property(_lock(ctx.conn, property_id))
    PropertyCreate.model_validate(
        current.model_dump(include=_CREATE_FIELDS) | {"purchase_date": new_date}
    )
    if new_date == current.purchase_date:
        return current

    root = milestone_root(ctx.conn, EventType.PROPERTY_POSSESSION_TRANSFERRED, property_id)
    latest = latest_in_chain(ctx.conn, root.id)
    updated = _update_row(ctx, property_id, {"purchase_date": new_date})
    append_event(
        ctx,
        EventType.PROPERTY_POSSESSION_TRANSFERRED,
        property_id,
        Empty(),
        property_id=property_id,
        effective_date=updated.purchase_date,
        corrects_event_id=latest.id,
    )
    return updated


def get_property(conn: Connection, property_id: UUID) -> Property | None:
    row = conn.execute(
        select(property_table).where(property_table.c.id == property_id)
    ).one_or_none()
    return None if row is None else _to_property(row)


# --- Ownership ---------------------------------------------------------------


def set_ownership(
    ctx: CommandContext,
    property_id: UUID,
    shares: Sequence[OwnerShare],
    *,
    effective_date: date | None = None,
) -> PropertyOwnership:
    """Replace the owners' shares. They must sum to exactly 1 and name each owner once.
    No-op (no write, no event) if the shares are unchanged."""
    ownership = PropertyOwnership(property_id=property_id, shares=list(shares))
    require_valid(check_ownership_shares([s.share for s in ownership.shares]), "ownership shares")
    owner_ids = [s.owner_id for s in ownership.shares]
    if len(set(owner_ids)) != len(owner_ids):
        raise ValueError("each owner may hold only one share of a property")

    _lock(ctx.conn, property_id)
    found = set(
        ctx.conn.execute(select(owner_table.c.id).where(owner_table.c.id.in_(owner_ids))).scalars()
    )
    for owner_id in owner_ids:
        if owner_id not in found:
            raise NotFoundError(EntityType.OWNER, owner_id)
    current = get_ownership(ctx.conn, property_id)
    if current is not None and set(current.shares) == set(ownership.shares):
        return current

    ctx.conn.execute(delete(ownership_table).where(ownership_table.c.property_id == property_id))
    ctx.conn.execute(
        insert(ownership_table),
        [
            {
                "workspace_id": ctx.workspace_id,
                "property_id": property_id,
                "owner_id": s.owner_id,
                "share_numerator": s.share.numerator,
                "share_denominator": s.share.denominator,
            }
            for s in ownership.shares
        ],
    )
    append_event(
        ctx,
        EventType.PROPERTY_OWNERSHIP_SET,
        property_id,
        OwnershipSet(shares=ownership.shares),
        property_id=property_id,
        effective_date=effective_date,
    )
    return ownership


def get_ownership(conn: Connection, property_id: UUID) -> PropertyOwnership | None:
    rows = conn.execute(
        select(ownership_table)
        .where(ownership_table.c.property_id == property_id)
        .order_by(ownership_table.c.owner_id)
    ).all()
    if not rows:
        return None
    return PropertyOwnership(
        property_id=property_id,
        shares=[
            OwnerShare(
                owner_id=r.owner_id,
                share=Fraction(numerator=r.share_numerator, denominator=r.share_denominator),
            )
            for r in rows
        ],
    )


# --- Acquisition costs and price splits (the AfA basis, D8) ------------------


def add_acquisition_cost(ctx: CommandContext, data: AcquisitionCostCreate) -> AcquisitionCost:
    """One itemized Anschaffungsnebenkosten line. History date = paid_on, when known."""
    require_row(ctx.conn, property_table, EntityType.PROPERTY, data.property_id)
    require_document(ctx.conn, data.document_id)
    row = ctx.conn.execute(
        insert(cost_table)
        .values(
            workspace_id=ctx.workspace_id,
            **data.model_dump(),
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*cost_table.c)
    ).one()
    cost = AcquisitionCost.model_validate(dict(row._mapping))
    append_event(
        ctx,
        EventType.PROPERTY_ACQUISITION_COST_RECORDED,
        cost.property_id,
        AcquisitionCostRecorded(
            cost_id=cost.id, kind=cost.kind, amount=cost.amount, document_id=cost.document_id
        ),
        property_id=cost.property_id,
        effective_date=cost.paid_on,
    )
    return cost


def list_acquisition_costs(conn: Connection, property_id: UUID) -> list[AcquisitionCost]:
    rows = conn.execute(
        select(cost_table)
        .where(cost_table.c.property_id == property_id)
        .order_by(cost_table.c.created_at, cost_table.c.id)
    )
    return [AcquisitionCost.model_validate(dict(r._mapping)) for r in rows]


def record_price_split(ctx: CommandContext, data: PriceSplitCreate) -> PriceSplit:
    """Append a building/land split candidate. Candidates are never edited (append-only)."""
    require_row(ctx.conn, property_table, EntityType.PROPERTY, data.property_id)
    require_document(ctx.conn, data.evidence_document_id)
    values = data.model_dump()
    if values["inputs"] is None:
        values["inputs"] = null()  # SQL NULL; a JSONB column would otherwise store JSON null
    row = ctx.conn.execute(
        insert(split_table)
        .values(workspace_id=ctx.workspace_id, **values, recorded_at=ctx.now)
        .returning(*split_table.c)
    ).one()
    split = PriceSplit.model_validate(dict(row._mapping))
    append_event(
        ctx,
        EventType.PROPERTY_PRICE_SPLIT_RECORDED,
        split.property_id,
        PriceSplitRecorded(
            split_id=split.id,
            source=split.source,
            building_share=split.building_share,
            evidence_document_id=split.evidence_document_id,
        ),
        property_id=split.property_id,
    )
    return split


def list_price_splits(conn: Connection, property_id: UUID) -> list[PriceSplit]:
    rows = conn.execute(
        select(split_table)
        .where(split_table.c.property_id == property_id)
        .order_by(split_table.c.recorded_at, split_table.c.id)
    )
    return [PriceSplit.model_validate(dict(r._mapping)) for r in rows]


def select_price_split(ctx: CommandContext, property_id: UUID, split_id: UUID) -> Property:
    """Choose one of the property's split candidates as the AfA basis."""
    current = _to_property(_lock(ctx.conn, property_id))
    belongs = ctx.conn.execute(
        select(split_table.c.id).where(
            split_table.c.id == split_id, split_table.c.property_id == property_id
        )
    ).one_or_none()
    if belongs is None:
        raise NotFoundError("price_split", split_id)
    if current.selected_price_split_id == split_id:
        return current

    updated = _update_row(ctx, property_id, {"selected_price_split_id": split_id})
    append_event(
        ctx,
        EventType.PROPERTY_PRICE_SPLIT_SELECTED,
        property_id,
        PriceSplitSelected(split_id=split_id),
        property_id=property_id,
    )
    return updated


# --- Rows ----------------------------------------------------------------------


def _lock(conn: Connection, property_id: UUID) -> Row:
    return require_row(conn, property_table, EntityType.PROPERTY, property_id, for_update=True)


def _update_row(ctx: CommandContext, property_id: UUID, values: dict[str, Any]) -> Property:
    row = ctx.conn.execute(
        update(property_table)
        .where(property_table.c.id == property_id)
        .values(**_columns(values), updated_at=ctx.now)
        .returning(*property_table.c)
    ).one()
    return _to_property(row)


def _columns(values: dict[str, Any]) -> dict[str, Any]:
    """Schema fields to columns: the nested address becomes its five columns."""
    values = dict(values)
    address = values.pop("address", None)
    if address is not None:
        values |= address
    return values


def _to_property(row: Row) -> Property:
    values = dict(row._mapping)
    address = {name: values.pop(name) for name in _ADDRESS_COLUMNS}
    return Property.model_validate(values | {"address": address})
