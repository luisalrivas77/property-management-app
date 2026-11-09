"""Tax inputs: the user's tax decisions and verified certificate figures (phase 3 D58).

Spec: docs/phases/phase-3.md §6, §7, §10. One append-only table `tax_input` (migration 0013:
SELECT and INSERT only for the app role, a forbid_mutation trigger). A row is never changed:
a correction is a new row whose `supersedes_id` names the row it replaces, and a withdrawal
is a new row with `withdrawn = true` that repeats the withdrawn row's value. Every row is
written together with its `property.tax_input_recorded` event, in the caller's transaction
(principle 4).

"Current" = not withdrawn and not superseded by any other row. What a write must satisfy,
beyond TaxInputCreate's own scope validator:

- the path's property is the body's (`tax_input.scope_invalid`);
- a unit, loan or allocation belongs to that property; a `market_rent` is per Wohnung (D71):
  the property for an ETW or EFH (no unit), one unit for an MFH; an allocation-scoped kind
  fits the allocation's category (`renovation_treatment` a renovation; `spread_82b` and
  `exclude_from_15pct` a repair or renovation) (`tax_input.scope_invalid`);
- the allocation is confirmed (`tax_input.allocation_not_confirmed`): a proposed or a
  superseded row is not a booking the draft sums (D59); a withdrawal is allowed either way;
- `supersedes_id` names a current row of the same property, kind and scope
  (`tax_input.not_current`; another scope is `tax_input.scope_invalid`); a withdrawal repeats
  its value;
- no other current row has the same scope, so the rules never see two answers to one
  question (`tax_input.not_current`: supersede it instead). The scope is the allocation for
  the per-allocation kinds, (loan, year) for `interest_certified`, the year for
  `ruecklage_spent`, the property for `pre_handover_income`, and (unit, valid_from) for
  `market_rent`, whose values from different dates stand side by side (a new Mietspiegel);
- an Erweiterung is Herstellungskosten by nature (D78): an `exclude_from_15pct` with reason
  `extension` needs a `renovation` allocation with a current `renovation_treatment` of
  `herstellung`, and while such an exclusion is current, a `renovation_treatment` row that
  would leave the allocation without a current `herstellung` (an `erhaltung` answer or a
  withdrawal) is refused; the user withdraws the exclusion first
  (`tax_input.extension_needs_herstellung`). Otherwise the cost would leave the 15 % count
  and still be deducted at once on Z55.

Writes for one property are serialised by locking the property row (FOR NO KEY UPDATE), so
the "no other current row" and the Erweiterung checks cannot race; `supersedes_id` is UNIQUE
in the table as well. The event carries amounts and codes only, never the note (free text).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, insert, or_, select
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.rules.errors import TAX_INPUT_EXTENSION_NEEDS_HERSTELLUNG
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import AllocationStatus, EntityType, PropertyKind
from hausbuch.schemas.events import EventType, TaxInputRecorded
from hausbuch.schemas.tax import (
    TAX_INPUT_SCOPE_INVALID,
    TaxInput,
    TaxInputCreate,
    TaxInputKind,
)

from .allocations import user_id
from .context import CommandContext
from .errors import ConflictError, NotFoundError, RuleViolationError, violated_constraint
from .events import append_event
from .lookup import require_document, require_row

TAX_INPUT_NOT_CURRENT = "tax_input.not_current"
TAX_INPUT_ALLOCATION_NOT_CONFIRMED = "tax_input.allocation_not_confirmed"
TAX_INPUT = "tax_input"  # NotFoundError's entity name: the row carries no events of its own

_SUPERSEDED_ONCE = "tax_input_supersedes_id_key"
_REPAIRS_OR_RENOVATION = frozenset({AllocationCategory.REPAIRS, AllocationCategory.RENOVATION})
# The categories an allocation-scoped input can decide (§5.3, §5.4).
_KIND_CATEGORIES: dict[TaxInputKind, frozenset[AllocationCategory]] = {
    TaxInputKind.SPREAD_82B: _REPAIRS_OR_RENOVATION,
    TaxInputKind.RENOVATION_TREATMENT: frozenset({AllocationCategory.RENOVATION}),
    TaxInputKind.EXCLUDE_FROM_15PCT: _REPAIRS_OR_RENOVATION,
}
_SCOPE_COLUMNS = ("tax_year", "unit_id", "loan_id", "allocation_id")


# --- Reads -------------------------------------------------------------------------------


def _is_current() -> Any:
    """SQL: the row is neither withdrawn nor superseded by another row."""
    later = tax_input_table.alias("later")
    return and_(
        tax_input_table.c.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == tax_input_table.c.id),
    )


def list_tax_inputs(
    conn: Connection,
    property_id: UUID,
    *,
    tax_year: int | None = None,
    kind: TaxInputKind | None = None,
    include_history: bool = False,
) -> list[TaxInput]:
    """GET /properties/{id}/tax-inputs: the property's current rows, oldest first; with
    include_history every row (superseded and withdrawn ones too). tax_year keeps the rows of
    that year plus the rows that have no year (the per-allocation kinds, market_rent,
    pre_handover_income, which apply to whichever year they concern). NotFoundError for an
    unknown property."""
    require_row(conn, property_table, EntityType.PROPERTY, property_id)
    query = select(tax_input_table).where(tax_input_table.c.property_id == property_id)
    if not include_history:
        query = query.where(_is_current())
    if tax_year is not None:
        query = query.where(
            or_(tax_input_table.c.tax_year == tax_year, tax_input_table.c.tax_year.is_(None))
        )
    if kind is not None:
        query = query.where(tax_input_table.c.kind == kind.value)
    rows = conn.execute(query.order_by(tax_input_table.c.recorded_at, tax_input_table.c.id))
    return [_to_tax_input(r) for r in rows]


def get_tax_input(conn: Connection, tax_input_id: UUID) -> TaxInput | None:
    row = conn.execute(
        select(tax_input_table).where(tax_input_table.c.id == tax_input_id)
    ).one_or_none()
    return None if row is None else _to_tax_input(row)


def find_current(
    conn: Connection,
    property_id: UUID,
    kind: TaxInputKind,
    *,
    tax_year: int | None = None,
    unit_id: UUID | None = None,
    loan_id: UUID | None = None,
    allocation_id: UUID | None = None,
) -> list[TaxInput]:
    """The current rows of one property, kind and scope (oldest first). For market_rent the
    scope is the unit only, so several rows with different valid_from can come back."""
    scope = {
        "tax_year": tax_year,
        "unit_id": unit_id,
        "loan_id": loan_id,
        "allocation_id": allocation_id,
    }
    query = select(tax_input_table).where(
        tax_input_table.c.property_id == property_id,
        tax_input_table.c.kind == kind.value,
        _is_current(),
        *(
            tax_input_table.c[name].is_(None) if value is None else tax_input_table.c[name] == value
            for name, value in scope.items()
        ),
    )
    rows = conn.execute(query.order_by(tax_input_table.c.recorded_at, tax_input_table.c.id))
    return [_to_tax_input(r) for r in rows]


# --- Writes ------------------------------------------------------------------------------


def create_tax_input(ctx: CommandContext, property_id: UUID, data: TaxInputCreate) -> TaxInput:
    """POST /properties/{id}/tax-inputs: one new row and its event (see the module doc for
    the checks). Raises NotFoundError for an unknown property, unit, loan, allocation,
    document or superseded row; RuleViolationError tax_input.scope_invalid,
    tax_input.not_current, tax_input.allocation_not_confirmed or
    tax_input.extension_needs_herstellung. Nothing is written then."""
    if data.property_id != property_id:
        raise RuleViolationError(
            [TAX_INPUT_SCOPE_INVALID], "the body names another property than the path"
        )
    prop = _lock_property(ctx.conn, property_id)
    allocation = _check_scope(ctx.conn, prop, data)
    require_document(ctx.conn, data.document_id)
    if data.supersedes_id is not None:
        _check_supersedes(ctx.conn, data)
    _check_no_other_current(ctx.conn, data)
    _check_extension(ctx.conn, data, allocation)

    try:
        row = ctx.conn.execute(
            insert(tax_input_table)
            .values(
                workspace_id=ctx.workspace_id,
                **data.model_dump(exclude={"value"}),
                value=data.value.model_dump(mode="json"),
                recorded_at=ctx.now,
                recorded_by=user_id(ctx),
            )
            .returning(*tax_input_table.c)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _SUPERSEDED_ONCE:
            # Unreachable while the property lock holds; kept so a race is a 409, not a 500.
            raise ConflictError(
                f"tax input {data.supersedes_id} was superseded concurrently",
                constraint=_SUPERSEDED_ONCE,
            ) from None
        raise
    created = _to_tax_input(row)
    append_event(
        ctx,
        EventType.PROPERTY_TAX_INPUT_RECORDED,
        property_id,
        TaxInputRecorded(
            tax_input_id=created.id,
            kind=created.kind,
            tax_year=created.tax_year,
            unit_id=created.unit_id,
            loan_id=created.loan_id,
            allocation_id=created.allocation_id,
            document_id=created.document_id,
            value=created.value,
            supersedes_id=created.supersedes_id,
            withdrawn=created.withdrawn,
        ),
        property_id=property_id,
        unit_id=created.unit_id,
    )
    return created


# --- Checks ------------------------------------------------------------------------------


def _lock_property(conn: Connection, property_id: UUID) -> Row:
    """The property row, locked FOR NO KEY UPDATE: tax input writes of one property queue
    behind each other, while inserts that only reference the property (FOR KEY SHARE) don't."""
    row = conn.execute(
        select(property_table)
        .where(property_table.c.id == property_id)
        .with_for_update(key_share=True)
    ).one_or_none()
    if row is None:
        raise NotFoundError(EntityType.PROPERTY, property_id)
    return row


def _scope_invalid(message: str) -> RuleViolationError:
    return RuleViolationError([TAX_INPUT_SCOPE_INVALID], message)


def _check_scope(conn: Connection, prop: Row, data: TaxInputCreate) -> Row | None:
    """The scope checks; returns the allocation row of a per-allocation kind, else None."""
    kind = data.kind
    if data.unit_id is not None:
        unit = require_row(conn, unit_table, EntityType.UNIT, data.unit_id)
        if unit.property_id != prop.id:
            raise _scope_invalid("the unit belongs to another property")
    if kind is TaxInputKind.MARKET_RENT:
        mfh = PropertyKind(prop.kind) is PropertyKind.MFH
        if mfh and data.unit_id is None:
            raise _scope_invalid("an MFH's ortsübliche Miete is recorded per unit")
        if not mfh and data.unit_id is not None:
            raise _scope_invalid("the ortsübliche Miete of a flat or house is the property's")
    if data.loan_id is not None:
        loan = require_row(conn, loan_table, EntityType.LOAN, data.loan_id)
        if loan.property_id != prop.id:
            raise _scope_invalid("the loan belongs to another property")
    if data.allocation_id is not None:
        allocation = require_row(conn, allocation_table, "allocation", data.allocation_id)
        if allocation.property_id != prop.id:
            raise _scope_invalid("the allocation belongs to another property")
        if data.withdrawn:
            return allocation  # ends a decision on a row that may since have been reallocated
        if AllocationStatus(allocation.status) is not AllocationStatus.CONFIRMED:
            raise RuleViolationError(
                [TAX_INPUT_ALLOCATION_NOT_CONFIRMED],
                f"allocation {allocation.id} is {allocation.status}, not confirmed",
            )
        category = AllocationCategory(allocation.category)
        if category not in _KIND_CATEGORIES[kind]:
            raise _scope_invalid(f"a {kind} input does not apply to a {category} allocation")
        return allocation
    return None


def _check_supersedes(conn: Connection, data: TaxInputCreate) -> None:
    assert data.supersedes_id is not None
    old = get_tax_input(conn, data.supersedes_id)
    if old is None:
        raise NotFoundError(TAX_INPUT, data.supersedes_id)
    if old.property_id != data.property_id or old.kind is not data.kind:
        raise _scope_invalid("a correction supersedes a row of the same property and kind")
    if any(getattr(old, name) != getattr(data, name) for name in _SCOPE_COLUMNS):
        raise _scope_invalid("a correction keeps the superseded row's scope")
    current = {r.id for r in find_current(conn, data.property_id, data.kind, **_scope(data))}
    if old.id not in current:
        raise RuleViolationError(
            [TAX_INPUT_NOT_CURRENT],
            f"tax input {old.id} is not current (withdrawn or already superseded)",
        )
    if data.withdrawn and old.value != data.value:
        raise _scope_invalid("a withdrawal repeats the withdrawn row's value")


def _check_no_other_current(conn: Connection, data: TaxInputCreate) -> None:
    if data.withdrawn:
        return  # ends a decision; adds no answer
    others = [
        r
        for r in find_current(conn, data.property_id, data.kind, **_scope(data))
        if r.id != data.supersedes_id and _same_question(r, data)
    ]
    if others:
        raise RuleViolationError(
            [TAX_INPUT_NOT_CURRENT],
            f"tax input {others[0].id} already answers this; supersede it (supersedes_id)",
        )


def _check_extension(conn: Connection, data: TaxInputCreate, allocation: Row | None) -> None:
    """D78: an Erweiterung leaves the 15 % count only as Herstellungskosten. Runs under the
    property lock, after the other checks, so the current rows it reads cannot change."""
    if allocation is None:
        return
    if data.kind is TaxInputKind.EXCLUDE_FROM_15PCT:
        if data.withdrawn or data.value.reason != "extension":  # type: ignore[union-attr]
            return  # withdrawing the exclusion is how the treatment is unlocked
        renovation = AllocationCategory(allocation.category) is AllocationCategory.RENOVATION
        if not renovation or not _has_herstellung(conn, data.property_id, allocation.id):
            raise RuleViolationError(
                [TAX_INPUT_EXTENSION_NEEDS_HERSTELLUNG],
                f"an Erweiterung on allocation {allocation.id} is excluded from the 15 % test"
                " only on a renovation elected herstellung",
            )
    elif data.kind is TaxInputKind.RENOVATION_TREATMENT:
        if not data.withdrawn and data.value.treatment == "herstellung":  # type: ignore[union-attr]
            return
        extension = [
            r
            for r in find_current(
                conn,
                data.property_id,
                TaxInputKind.EXCLUDE_FROM_15PCT,
                allocation_id=allocation.id,
            )
            if r.value.reason == "extension"  # type: ignore[union-attr]
        ]
        if extension:
            raise RuleViolationError(
                [TAX_INPUT_EXTENSION_NEEDS_HERSTELLUNG],
                f"tax input {extension[0].id} excludes allocation {allocation.id} as an"
                " Erweiterung; withdraw it before giving up the herstellung election",
            )


def _has_herstellung(conn: Connection, property_id: UUID, allocation_id: UUID) -> bool:
    current = find_current(
        conn, property_id, TaxInputKind.RENOVATION_TREATMENT, allocation_id=allocation_id
    )
    return any(r.value.treatment == "herstellung" for r in current)  # type: ignore[union-attr]


def _scope(data: TaxInputCreate | TaxInput) -> dict[str, Any]:
    return {name: getattr(data, name) for name in _SCOPE_COLUMNS}


def _same_question(row: TaxInput, data: TaxInputCreate) -> bool:
    """Two current rows of one scope answer the same question, except market_rent values
    valid from different dates (D61: the value in force is chosen by date)."""
    if data.kind is TaxInputKind.MARKET_RENT:
        return row.value.valid_from == data.value.valid_from  # type: ignore[union-attr]
    return True


def _to_tax_input(row: Row) -> TaxInput:
    return TaxInput.model_validate(row._asdict())
