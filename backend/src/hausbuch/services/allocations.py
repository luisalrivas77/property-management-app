"""Writing allocations (docs/phases/phase-2.md §3.3, §3.4, §8.3, D23, D31).

Every write goes through `record_set`, so every path (rule, matcher, document match, model,
user) runs `rules.allocations.check_sum` against the transaction before any row is written:
the validator enforces Σ = amount, never the proposer.

Rows are append-only (D23). A new set supersedes all of the transaction's current rows (the
ones not yet superseded): their status moves to `superseded` and each new row names one of
them in `supersedes_id` (row i the old row i, extra new rows the last old one), so the
non-superseded rows always sum to the transaction amount. The only other UPDATE is
`proposed -> confirmed` when a person confirms a proposed set unchanged.

Events (§3.5), only when rows become confirmed (proposed rows are review items; their AI
provenance is the ai_call row, D18):
- transaction.allocated: the first confirmed set of a transaction; `auto` when no person
  confirmed it (a rule or a matcher at 1.0, or a routed document / model set);
- transaction.reallocated: a confirmed set that supersedes confirmed rows, `supersedes`
  listing those rows.
Both are dated by the booking date and carry no tax year (D31) and no text: no purpose,
counterparty, reasoning or question.

Confirming a document match on a Nebenkosten invoice (category acquisition_cost with the
invoice as Beleg) sets the cost row's paid_on to the booking date through
services.properties.update_acquisition_cost, which records it as an event. A financing
invoice (loan_fees, D35) has no cost row.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import property_acquisition_cost as cost_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.rules.allocations import check_sum
from hausbuch.rules.errors import ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT
from hausbuch.rules.household.taxonomy import allowed_categories
from hausbuch.schemas.allocation import (
    Allocation,
    AllocationCategory,
    AllocationDraft,
    AllocationSet,
)
from hausbuch.schemas.enums import AccountPurpose, ActorType, AllocationStatus, EntityType
from hausbuch.schemas.events import (
    AllocatedPart,
    EventType,
    TransactionAllocated,
    TransactionReallocated,
)

from .context import CommandContext
from .errors import RuleViolationError
from .events import append_event
from .lookup import require_document, require_row
from .properties import update_acquisition_cost

SCOPE_MISMATCH = "allocation.scope_mismatch"
TRANSACTION_MISMATCH = "allocation.transaction_mismatch"
EXCLUDED = "allocation.excluded_transaction"

_PROPOSED = AllocationStatus.PROPOSED.value
_CONFIRMED = AllocationStatus.CONFIRMED.value
_SUPERSEDED = AllocationStatus.SUPERSEDED.value

# The fields that make two allocation sets the same answer (provenance aside).
_DRAFT_FIELDS = tuple(AllocationDraft.model_fields)


# --- Reads ---------------------------------------------------------------------------


def current_allocations(conn: Connection, transaction_id: UUID) -> list[Allocation]:
    """The transaction's rows that are not superseded (all proposed or all confirmed)."""
    rows = conn.execute(
        select(allocation_table)
        .where(
            allocation_table.c.transaction_id == transaction_id,
            allocation_table.c.status != _SUPERSEDED,
        )
        .order_by(allocation_table.c.recorded_at, allocation_table.c.id)
    )
    return [to_allocation(r) for r in rows]


def to_allocation(row: Row) -> Allocation:
    data = row._asdict()
    data["created_at"] = data["recorded_at"]
    return Allocation.model_validate(data)


def same_answer(drafts: Sequence[AllocationDraft], current: Sequence[Allocation]) -> bool:
    """Whether the drafts are the current rows' split (amounts, categories, scope, period,
    recurring, Beleg), in any order."""

    def key(item: AllocationDraft | Allocation) -> tuple:
        return tuple(str(getattr(item, name)) for name in _DRAFT_FIELDS)

    return len(drafts) == len(current) and sorted(map(key, drafts)) == sorted(map(key, current))


# --- Writes --------------------------------------------------------------------------


def record_set(
    ctx: CommandContext,
    allocation_set: AllocationSet,
    *,
    confirm: bool,
    auto: bool,
) -> list[Allocation]:
    """Write `allocation_set` as the transaction's current rows, superseding the rows it
    had. `confirm`: the rows are confirmed now (by the actor when it is a person, else
    auto-confirmed with confirmed_by null); otherwise they are proposed for review.

    Raises RuleError allocation.empty / allocation.sum_mismatch (nothing written),
    RuleViolationError allocation.excluded_transaction for a line merged away as a
    duplicate, allocation.scope_mismatch when a unit, tenancy or loan is not of the
    allocation's property, allocation.category_not_for_account for a category the line's
    account does not take (phase 6 D159), whoever answers: a person, a rule or the model."""
    tx = _lock_transaction(ctx, allocation_set.transaction_id)
    check_sum(tx.amount, allocation_set.allocations)
    check_categories(ctx.conn, tx.account_id, allocation_set.allocations)
    _check_scope(ctx.conn, allocation_set.allocations)
    previous = current_allocations(ctx.conn, tx.id)
    if previous:
        ctx.conn.execute(
            update(allocation_table)
            .where(allocation_table.c.id.in_([a.id for a in previous]))
            .values(status=_SUPERSEDED)
        )
    confirmed_by = user_id(ctx) if confirm and not auto else None
    rows = []
    for i, draft in enumerate(allocation_set.allocations):
        supersedes = previous[min(i, len(previous) - 1)].id if previous else None
        rows.append(
            {
                "workspace_id": ctx.workspace_id,
                "transaction_id": tx.id,
                **draft.model_dump(),
                "method": allocation_set.method.value,
                "confidence": allocation_set.confidence,
                "status": _CONFIRMED if confirm else _PROPOSED,
                "supersedes_id": supersedes,
                "rule_id": allocation_set.rule_id,
                "ai_call_id": allocation_set.ai_call_id,
                "reasoning": allocation_set.reasoning,
                "question": allocation_set.question,
                "recorded_at": ctx.now,
                "confirmed_at": ctx.now if confirm else None,
                "confirmed_by": confirmed_by,
            }
        )
    written = [
        to_allocation(r)
        for r in ctx.conn.execute(
            insert(allocation_table).values(rows).returning(*allocation_table.c)
        )
    ]
    if confirm:
        superseded = [a.id for a in previous if a.status is AllocationStatus.CONFIRMED]
        _confirmed(ctx, tx, written, superseded, auto=auto)
    from . import transfers  # phase 6 D162: it reads the lines this module books

    transfers.after_booking(ctx, tx.id, {a.category for a in written})
    return written


def confirm_proposed(ctx: CommandContext, transaction_id: UUID) -> list[Allocation]:
    """A person confirms the transaction's proposed set as it is: its rows move to
    confirmed (the proposer's method, confidence and provenance stay)."""
    tx = _lock_transaction(ctx, transaction_id)
    current = current_allocations(ctx.conn, tx.id)
    if not current or any(a.status is not AllocationStatus.PROPOSED for a in current):
        raise ValueError("no proposed allocation set to confirm")
    check_sum(tx.amount, current)  # type: ignore[arg-type]  # Allocation has the draft fields
    rows = ctx.conn.execute(
        update(allocation_table)
        .where(allocation_table.c.id.in_([a.id for a in current]))
        .values(status=_CONFIRMED, confirmed_at=ctx.now, confirmed_by=user_id(ctx))
        .returning(*allocation_table.c)
    )
    written = sorted((to_allocation(r) for r in rows), key=lambda a: (a.recorded_at, str(a.id)))
    _confirmed(ctx, tx, written, [], auto=False)
    return written


def user_id(ctx: CommandContext) -> UUID | None:
    """app_user.id of a person's command ("user:<uuid>"), else None."""
    if ctx.actor.actor_type is not ActorType.USER:
        return None
    try:
        return UUID(ctx.actor.actor_id.removeprefix("user:"))
    except ValueError:
        return None


# --- Helpers -------------------------------------------------------------------------


def account_purpose(conn: Connection, account_id: UUID) -> AccountPurpose:
    purpose = conn.execute(
        select(account_table.c.purpose).where(account_table.c.id == account_id)
    ).scalar_one()
    return AccountPurpose(purpose)


def categories_allowed(purpose: AccountPurpose, drafts: Sequence[AllocationDraft]) -> bool:
    allowed = allowed_categories(purpose)
    return all(d.category in allowed for d in drafts)


def check_categories(conn: Connection, account_id: UUID, drafts: Sequence[AllocationDraft]) -> None:
    """An allocation carries only the categories of its account's purpose (phase 6 D159): a
    household, transfer or depot code on a rental line would be on no Anlage V line and in no
    to-do, so the cost would vanish from the draft without a word; a rental code on a private
    line would reach the Anlage V from an account that is not the property's (D161)."""
    purpose = account_purpose(conn, account_id)
    allowed = allowed_categories(purpose)
    refused = sorted({d.category.value for d in drafts if d.category not in allowed})
    if refused:
        raise RuleViolationError(
            [ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT],
            f"categories not for a {purpose.value} account: {', '.join(refused)}",
        )


def _lock_transaction(ctx: CommandContext, transaction_id: UUID) -> Row:
    """The transaction row, locked, so two writes for one line never interleave."""
    tx = require_row(
        ctx.conn, transaction_table, EntityType.TRANSACTION, transaction_id, for_update=True
    )
    if tx.excluded_as_duplicate_of is not None:
        raise RuleViolationError([EXCLUDED], "the line was merged away as a duplicate")
    return tx


def _check_scope(conn: Connection, drafts: Sequence[AllocationDraft]) -> None:
    """Every referenced row exists here (404 otherwise), and a unit, tenancy or loan
    belongs to the allocation's property."""
    for d in drafts:
        require_document(conn, d.document_id)
        if d.property_id is not None:  # else the composite FK would raise a 500
            require_row(conn, property_table, EntityType.PROPERTY, d.property_id)
        owners: list[UUID] = []
        if d.unit_id is not None:
            owners.append(require_row(conn, unit_table, EntityType.UNIT, d.unit_id).property_id)
        if d.tenancy_id is not None:
            tenancy = require_row(conn, tenancy_table, EntityType.TENANCY, d.tenancy_id)
            unit = require_row(conn, unit_table, EntityType.UNIT, tenancy.unit_id)
            owners.append(unit.property_id)
            if d.unit_id is not None and d.unit_id != tenancy.unit_id:
                raise RuleViolationError([SCOPE_MISMATCH], "the tenancy is of another unit")
        if d.loan_id is not None:
            owners.append(require_row(conn, loan_table, EntityType.LOAN, d.loan_id).property_id)
        if d.property_id is None and owners:
            raise RuleViolationError([SCOPE_MISMATCH], "a unit, tenancy or loan needs its property")
        if any(owner != d.property_id for owner in owners):
            raise RuleViolationError([SCOPE_MISMATCH], "the scope is of another property")


def _confirmed(
    ctx: CommandContext,
    tx: Row,
    rows: Sequence[Allocation],
    superseded: Sequence[UUID],
    *,
    auto: bool,
) -> None:
    first = rows[0]
    fields: dict[str, Any] = {
        "allocation_ids": [a.id for a in rows],
        "allocations": [_part(a) for a in rows],
        "method": first.method,
        "confidence": first.confidence,
        "rule_id": first.rule_id,
        "ai_call_id": first.ai_call_id,
        "auto": auto,
    }
    properties = {a.property_id for a in rows}
    property_id = next(iter(properties)) if len(properties) == 1 else None
    if superseded:
        event_type = EventType.TRANSACTION_REALLOCATED
        payload: TransactionAllocated = TransactionReallocated(**fields, supersedes=superseded)
    else:
        event_type = EventType.TRANSACTION_ALLOCATED
        payload = TransactionAllocated(**fields)
    append_event(
        ctx, event_type, tx.id, payload, property_id=property_id, effective_date=tx.booking_date
    )
    for a in rows:
        if a.category is AllocationCategory.ACQUISITION_COST and a.document_id is not None:
            _set_paid_on(ctx, a.document_id, a.property_id, tx.booking_date)


def _part(a: Allocation) -> AllocatedPart:
    return AllocatedPart(
        allocation_id=a.id,
        category=a.category,
        amount=a.amount,
        property_id=a.property_id,
        unit_id=a.unit_id,
        tenancy_id=a.tenancy_id,
        loan_id=a.loan_id,
        service_period_start=a.service_period_start,
        service_period_end=a.service_period_end,
        recurring=a.recurring,
    )


def _set_paid_on(
    ctx: CommandContext, document_id: UUID, property_id: UUID | None, paid_on: date
) -> None:
    """The Nebenkosten row whose Beleg is this invoice gets the payment's booking date. Only
    when exactly one cost row has it as Beleg (of the allocation's property, when set)."""
    query = select(cost_table.c.id, cost_table.c.paid_on).where(
        cost_table.c.document_id == document_id
    )
    if property_id is not None:
        query = query.where(cost_table.c.property_id == property_id)
    costs = ctx.conn.execute(query).all()
    if len(costs) != 1 or costs[0].paid_on == paid_on:
        return
    update_acquisition_cost(ctx, costs[0].id, paid_on=paid_on)
