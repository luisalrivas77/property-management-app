"""The one-off re-sort of household lines into the subcategories (docs/phases/phase-6.md D159
amended 14 Nov 2026, migration 0032; Luis: "Let's recategorize everything").

Migration 0032 split each spending group into subcategories and kept the group's old code as
its fallback („Sonstige …“, rules.household.taxonomy.FALLBACK). Every private-account line
booked in one of those fallbacks, proposed or confirmed, is asked once more, and a finer
answer goes back to Zu prüfen as a proposal:

- resort_household (`hausbuch household-resort`) picks the lines and queues one
  match_transaction job each, with `resort`;
- resort_line (in the job) runs the deterministic order as for a new line, without the rules
  learned on a fallback (a learned rule on another code and the transfer matcher still answer
  first), and hands the line to the model when nothing answers;
- finish_resort (after the model) writes the answer under the line's lock.

A waiting line (all rows proposed) is answered as a re-ask is (services.matching.
reask_household): the model's answer supersedes the proposal and is never auto-confirmed. A
confirmed line keeps its rows unless the answer is one part in another household spending
code (taxonomy.HOUSEHOLD_SPENDING); then the proposal supersedes the confirmed rows
(append-only, allocations.record_set) and transaction.resort_proposed records it, since a
proposal writes no other event. Never a booking without a person: on a confirmed line even a
standing rule hit is only proposed.

Left alone (never queued): a line with more than one row (a split), a row with a property,
unit, tenancy, loan or Beleg, a leg of a transfer link or a line with a depot movement, a
possible cost of buying or financing the flat (matching.may_be_purchase_cost), a rental line,
and a line already answered with the current household prompt, so a second run asks nothing
again. Nothing is queued while `ai_private_accounts` is off (D168). `uncategorized` is not a
fallback: `hausbuch household-reask` re-asks those lines."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, exists, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as transfer_link_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT
from hausbuch.rules.household.taxonomy import FALLBACK_CODES, HOUSEHOLD_SPENDING
from hausbuch.rules.review import no_match_reason
from hausbuch.schemas.allocation import Allocation, AllocationSet
from hausbuch.schemas.enums import (
    AccountPurpose,
    AllocationStatus,
    DuplicateStatus,
    ImportStatus,
)
from hausbuch.schemas.events import AllocatedPart, EventType, TransactionResortProposed

from . import allocations, matching, workspace_settings
from .context import CommandContext
from .events import append_event

RESORT_CATEGORIES = FALLBACK_CODES

NOT_RESORTABLE = "matching.not_resortable"
KEEPS_CONFIRMED = "matching.keeps_confirmed"
ANSWERED_MEANWHILE = "matching.answered_meanwhile"


@dataclass(frozen=True)
class ResortResult:
    """What a run took and what it left alone, by reason (the dry run prints it)."""

    proposed: int  # taken: lines whose fallback row is proposed
    confirmed: int  # taken: lines whose fallback row is confirmed
    queued: int  # a match_transaction job each, with resort (proposed + confirmed)
    held_back: int  # `ai_private_accounts` is off: taken, but nothing queued
    split: int  # left alone: more than one row
    linked: int  # left alone: scoped, a Beleg, a transfer link or a depot movement
    purchase_costs: int  # left alone: may be a privately paid cost of buying the flat
    already_asked: int  # left alone: asked with the current household prompt already


# --- Which lines --------------------------------------------------------------------------


def _scoped(row: Any) -> bool:
    """A row with a property, unit, tenancy, loan or Beleg: its booking belongs to that link,
    as review._block_of reads it for „Diese auch … umbuchen“."""
    return any(
        v is not None
        for v in (row.property_id, row.unit_id, row.tenancy_id, row.loan_id, row.document_id)
    )


def _linked(conn: Connection, transaction_ids: list[UUID]) -> set[UUID]:
    """The lines that are a leg of a current transfer link (D162) or carry a depot movement
    (D165), as review._linked reads them."""
    if not transaction_ids:
        return set()
    link = transfer_link_table.c
    movement = movement_table.c
    legs = conn.execute(
        select(link.out_transaction_id, link.in_transaction_id).where(
            link.superseded_by.is_(None),
            or_(
                link.out_transaction_id.in_(transaction_ids),
                link.in_transaction_id.in_(transaction_ids),
            ),
        )
    ).all()
    moved = conn.execute(
        select(movement.transaction_id).where(
            movement.superseded_by.is_(None), movement.transaction_id.in_(transaction_ids)
        )
    ).scalars()
    found = {leg for pair in legs for leg in pair} | set(moved)
    return found & set(transaction_ids)


def resort_household(ctx: CommandContext) -> ResortResult:
    """Queue the re-sort of every private-account line booked in a group's fallback (module
    docstring). Run once by the operator after the deploy of migration 0032; no endpoint.
    Writes no event: the jobs write the proposals (and transaction.resort_proposed), and each
    model call its ai_call row."""
    codes = sorted(c.value for c in RESORT_CATEGORIES)
    t = transaction_table.c
    a = allocation_table.c
    fallback = allocation_table.alias("fallback_row")
    current = AllocationStatus.SUPERSEDED.value
    awaiting = exists().where(
        duplicate_table.c.other_transaction_id == t.id,
        duplicate_table.c.status == DuplicateStatus.OPEN.value,
    )
    in_fallback = exists().where(
        fallback.c.transaction_id == t.id,
        fallback.c.status != current,
        fallback.c.category.in_(codes),
    )
    rows = ctx.conn.execute(
        select(
            t.id,
            t.amount,
            t.counterparty_name,
            t.purpose,
            t.booking_text,
            a.status,
            a.property_id,
            a.unit_id,
            a.tenancy_id,
            a.loan_id,
            a.document_id,
        )
        .join(allocation_table, a.transaction_id == t.id)
        .join(import_table, import_table.c.id == t.import_id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
            import_table.c.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            ~awaiting,
            in_fallback,
            a.status != current,
        )
        .order_by(t.booking_date, t.id, a.id)
    ).all()
    by_line: dict[UUID, list[Any]] = {}
    for row in rows:
        by_line.setdefault(row.id, []).append(row)
    linked = _linked(ctx.conn, list(by_line))
    costs = matching.acquisition_cost_amounts(ctx.conn)
    asked = matching.lines_asked_with_current_prompt(ctx.conn)
    split = scoped = purchase = already = 0
    taken: list[tuple[UUID, bool]] = []  # (line, its row is confirmed)
    for transaction_id, parts in by_line.items():
        (first, *rest) = parts
        if rest:
            split += 1
        elif _scoped(first) or transaction_id in linked:
            scoped += 1
        elif matching.may_be_purchase_cost(first, costs):
            purchase += 1
        elif transaction_id in asked:
            already += 1
        else:
            taken.append((transaction_id, first.status == AllocationStatus.CONFIRMED.value))
    n_confirmed = sum(1 for _, confirmed in taken if confirmed)
    counts = {
        "proposed": len(taken) - n_confirmed,
        "confirmed": n_confirmed,
        "split": split,
        "linked": scoped,
        "purchase_costs": purchase,
        "already_asked": already,
    }
    if not workspace_settings.private_ai_allowed(ctx.conn):
        return ResortResult(queued=0, held_back=len(taken), **counts)
    for transaction_id, _ in taken:
        enqueue.enqueue_match_transaction(
            ctx.conn, workspace_id=ctx.workspace_id, transaction_id=transaction_id, resort=True
        )
    return ResortResult(queued=len(taken), held_back=0, **counts)


# --- One line (jobs.matching, with `resort`) ------------------------------------------------


def open_for_resort(conn: Connection, line: matching.Line) -> bool:
    """The line may be re-sorted: an open private line (reconciled, not excluded, not the
    later line of an open possible duplicate) with exactly one current row, proposed or
    confirmed, in a group's fallback, without a scope or a Beleg, and not a transfer or depot
    leg. Read under the line's lock by resort_line and finish_resort."""
    if not line.private or line.excluded or line.awaiting_duplicate:
        return False
    if line.import_status is not ImportStatus.RECONCILED or len(line.current) != 1:
        return False
    (row,) = line.current
    if row.category not in RESORT_CATEGORIES or _scoped(row):
        return False
    return not _linked(conn, [line.tx.id])


def seen(line: matching.Line) -> tuple[tuple[UUID, AllocationStatus], ...]:
    """The line's current rows as the job saw them (ids and statuses): finish_resort writes
    only while they are unchanged, so a person's answer in between is never replaced."""
    return tuple((a.id, a.status) for a in line.current)


def resort_line(
    ctx: CommandContext,
    transaction_id: UUID,
    *,
    iban_key: bytes,
    ai_available: bool,
    skip_if_asked: bool = True,
) -> matching.MatchPlan:
    """The job's first step for a re-sorted line, under its lock: the deterministic order
    without the fallback rules; a waiting line takes its answer as a re-ask does
    (matching.write), a confirmed one only as a proposal of a finer spending code
    (_propose_over_confirmed). Nothing answers: `categorize` when the model may see the line.
    `skip_if_asked` as in matching.match_line (a retry passes False)."""
    line = matching.load_line(ctx.conn, transaction_id, lock=True)
    if line is None:
        return matching.MatchPlan("skipped", "matching.not_found")
    if not open_for_resort(ctx.conn, line):
        return matching.MatchPlan("skipped", NOT_RESORTABLE)
    if skip_if_asked and matching.asked_with_current_prompt(ctx.conn, transaction_id):
        return matching.MatchPlan("skipped", "matching.asked_already")
    context = matching.build_context(
        ctx.conn,
        iban_key=iban_key,
        today=ctx.now.date(),
        for_transaction=transaction_id,
        account_purpose=line.account_purpose,
        account_id=line.account_id,
    )
    found = matching.deterministic_set(
        ctx.conn, line.tx, context, skip_rule_categories=RESORT_CATEGORIES
    )
    if found is not None:
        if line.waiting:
            return matching.write(ctx, line, found)
        return _propose_over_confirmed(ctx, line, found)
    if not ai_available or not line.private_ai:
        # D168: without the switch the line stays as it is, for a person.
        return matching.MatchPlan("skipped", no_match_reason(line.account_purpose, line.private_ai))
    return matching.MatchPlan("categorize", "routing.no_match")


def finish_resort(
    ctx: CommandContext,
    transaction_id: UUID,
    found: AllocationSet,
    expected: tuple[tuple[UUID, AllocationStatus], ...],
) -> matching.MatchPlan:
    """After the model call, under the line's lock: write the answer only while the line is
    still open for the re-sort and its rows are the ones the job saw (`expected`, seen()). A
    waiting line: the answer supersedes the proposal and is never auto-confirmed. A confirmed
    line: _propose_over_confirmed."""
    line = matching.load_line(ctx.conn, transaction_id, lock=True)
    if line is None or not open_for_resort(ctx.conn, line) or seen(line) != expected:
        return matching.MatchPlan("skipped", ANSWERED_MEANWHILE)
    if not allocations.categories_allowed(line.account_purpose, found.allocations):
        return matching.MatchPlan("skipped", ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT)
    if line.waiting:
        return matching.write(ctx, line, found, propose_only=True)
    return _propose_over_confirmed(ctx, line, found)


def _propose_over_confirmed(
    ctx: CommandContext, line: matching.Line, found: AllocationSet
) -> matching.MatchPlan:
    """A confirmed fallback line: only an answer of one part in another household spending
    code is written, as a proposal superseding the confirmed row, with
    transaction.resort_proposed. Anything else (the same code, a split, a transfer, income, a
    tax-relevant code, „noch offen“) leaves the confirmed row as it is."""
    (current,) = line.current
    if len(found.allocations) != 1:
        return matching.MatchPlan("skipped", KEEPS_CONFIRMED)
    (part,) = found.allocations
    if part.category is current.category or part.category not in HOUSEHOLD_SPENDING:
        return matching.MatchPlan("skipped", KEEPS_CONFIRMED)
    written = allocations.record_set(ctx, found, confirm=False, auto=False)
    _record_event(ctx, line, written, current, found)
    return matching.MatchPlan("written", "routing.review", found, written)


def _record_event(
    ctx: CommandContext,
    line: matching.Line,
    written: list[Allocation],
    superseded: Allocation,
    found: AllocationSet,
) -> None:
    payload = TransactionResortProposed(
        allocation_ids=[a.id for a in written],
        allocations=[_part(a) for a in written],
        method=found.method,
        confidence=found.confidence,
        rule_id=found.rule_id,
        ai_call_id=found.ai_call_id,
        auto=False,
        supersedes=[superseded.id],
        prompt_version=(
            matching.household_prompt_version() if found.ai_call_id is not None else None
        ),
    )
    append_event(
        ctx,
        EventType.TRANSACTION_RESORT_PROPOSED,
        line.tx.id,
        payload,
        effective_date=line.tx.booking_date,
    )


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
