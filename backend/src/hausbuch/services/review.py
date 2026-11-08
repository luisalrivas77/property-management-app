"""The review queue and a person's answer (docs/phases/phase-2.md §8.3, §8.1, D25, D38).

- `inbox`: GET /inbox, one list newest first: pending proposals (ready, failed,
  blocked_tenant_data), unreconciled or pending-balance imports, open possible duplicates
  (statements.import_inbox_items), and lines of reconciled imports with a proposed
  allocation set or none (excluded lines and the later line of an open possible duplicate
  are not listed: the duplicate is decided first).
- `allocate_transaction`: PUT /transactions/{id}/allocations. The person's answer is written
  through services.allocations (sum check, events, paid_on), optionally followed by a learned
  rule (services.rules_learned).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection, exists, select

from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.review import THRESHOLDS
from hausbuch.schemas.allocation import (
    Allocation,
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
    MatchingRuleCreate,
    MatchTransaction,
)
from hausbuch.schemas.api import (
    ConfirmAllocationsBody,
    InboxItem,
    InboxItemKind,
    TransactionAllocations,
)
from hausbuch.schemas.bank import Transaction
from hausbuch.schemas.enums import (
    AllocationStatus,
    DuplicateStatus,
    EntityType,
    ImportStatus,
    ProposalStatus,
)

from . import allocations, matching, rules_learned
from .context import CommandContext
from .errors import NotFoundError, RuleViolationError
from .statements import _to_transaction, import_inbox_items, transaction_select

TRANSACTION_MISMATCH = "allocation.transaction_mismatch"
IMPORT_NOT_RECONCILED = "allocation.import_not_reconciled"
NOT_OPEN = "allocation.line_not_open"
USER_CONFIDENCE = "1.000"


# --- PUT / GET /transactions/{id}/allocations --------------------------------------------


def allocate_transaction(
    ctx: CommandContext, transaction_id: UUID, body: ConfirmAllocationsBody, *, iban_key: bytes
) -> TransactionAllocations:
    """The person's answer. Refused (nothing written): a set for another transaction
    (allocation.transaction_mismatch), a line of an import that is not reconciled
    (allocation.import_not_reconciled, §3.4), an excluded line or the later line of an open
    possible duplicate (allocation.line_not_open), a set that does not sum
    (allocation.empty / allocation.sum_mismatch), a rule asked for that cannot be learned
    (rule.not_learnable) or a pattern that does not compile (rule.invalid_pattern)."""
    if body.allocation_set.transaction_id != transaction_id:
        raise RuleViolationError([TRANSACTION_MISMATCH], "the set is for another transaction")
    line = _open_line(ctx.conn, transaction_id)
    drafts = body.allocation_set.allocations
    current = line.current
    if current and allocations.same_answer(drafts, current):
        if current[0].status is AllocationStatus.PROPOSED:
            written = allocations.confirm_proposed(ctx, transaction_id)
        else:
            written = current
    else:
        answer = AllocationSet(
            transaction_id=transaction_id,
            allocations=drafts,
            method=AllocationMethod.USER,
            confidence=USER_CONFIDENCE,
        )
        written = allocations.record_set(ctx, answer, confirm=True, auto=False)
    confirmed = _as_set(transaction_id, written)
    context = _context(ctx.conn, ctx.now.date(), transaction_id, iban_key)
    proposed = rules_learned.proposed_rule(
        line.tx,
        confirmed,
        context,
        name_pattern=body.counterparty_name_pattern,
        purpose_pattern=body.purpose_pattern,
    )
    learn = body.learn_rule
    if learn is None:
        learn = rules_learned.learn_default(proposed, confirmed)
    rule = None
    if learn:
        if proposed is None:
            raise RuleViolationError(
                [rules_learned.NOT_LEARNABLE], "no rule can be learned from this answer"
            )
        data = proposed.model_dump() | {"created_from_allocation_id": written[0].id}
        rule = rules_learned.create_rule(ctx, MatchingRuleCreate.model_validate(data))
    return _response(ctx.conn, transaction_id, written, line.tx, context, rule=rule)


def transaction_allocations(
    conn: Connection, transaction_id: UUID, *, iban_key: bytes, today: date
) -> TransactionAllocations | None:
    line = matching.load_line(conn, transaction_id)
    if line is None:
        return None
    context = _context(conn, today, transaction_id, iban_key)
    return _response(conn, transaction_id, line.current, line.tx, context)


def _open_line(conn: Connection, transaction_id: UUID) -> matching.Line:
    line = matching.load_line(conn, transaction_id, lock=True)
    if line is None:
        raise NotFoundError(EntityType.TRANSACTION, transaction_id)
    if line.import_status is not ImportStatus.RECONCILED:
        raise RuleViolationError([IMPORT_NOT_RECONCILED], "the line's import is not reconciled")
    if line.excluded or line.awaiting_duplicate:
        raise RuleViolationError(
            [NOT_OPEN], "the line is excluded or waits for a duplicate decision"
        )
    return line


def _context(conn: Connection, today: date, transaction_id: UUID, iban_key: bytes) -> MatchContext:
    return matching.build_context(
        conn, iban_key=iban_key, today=today, for_transaction=transaction_id
    )


def _as_set(transaction_id: UUID, rows: list[Allocation]) -> AllocationSet:
    first = rows[0]
    return AllocationSet(
        transaction_id=transaction_id,
        allocations=[
            AllocationDraft.model_validate(r.model_dump(include=set(AllocationDraft.model_fields)))
            for r in rows
        ],
        method=first.method,
        confidence=first.confidence,
        rule_id=first.rule_id,
        ai_call_id=first.ai_call_id,
    )


def _response(
    conn: Connection,
    transaction_id: UUID,
    rows: list[Allocation],
    tx: MatchTransaction,
    context: MatchContext,
    *,
    rule=None,  # noqa: ANN001 - MatchingRule | None
) -> TransactionAllocations:
    probe = AllocationSet(
        transaction_id=transaction_id,
        allocations=[AllocationDraft(amount=tx.amount, category=AllocationCategory.OTHER_EXPENSE)],
        method=AllocationMethod.USER,
        confidence=USER_CONFIDENCE,
    )
    can_learn = rules_learned.proposed_rule(tx, probe, context) is not None
    default = False
    if rows:
        current = _as_set(transaction_id, rows)
        default = rules_learned.learn_default(
            rules_learned.proposed_rule(tx, current, context), current
        )
    return TransactionAllocations(
        transaction=_transaction(conn, transaction_id),
        allocations=rows,
        can_learn_rule=can_learn,
        learn_rule_default=default,
        no_default_rule_categories=[c for c in AllocationCategory if THRESHOLDS[c] is None],
        rule=rule,
    )


def _transaction(conn: Connection, transaction_id: UUID) -> Transaction:
    row = conn.execute(transaction_select().where(transaction_table.c.id == transaction_id)).one()
    return _to_transaction(row)


# --- GET /inbox ----------------------------------------------------------------------------

_PROPOSAL_TEXTS = {
    ProposalStatus.READY: (
        InboxItemKind.PROPOSAL,
        "Die KI hat das Dokument gelesen und schlägt Werte vor.",
        "Stimmen die vorgeschlagenen Werte?",
    ),
    ProposalStatus.FAILED: (
        InboxItemKind.READ_FAILED,
        "Konnte nicht gelesen werden.",
        "Möchtest du es erneut lesen lassen oder die Werte selbst eintragen?",
    ),
    ProposalStatus.BLOCKED_TENANT_DATA: (
        InboxItemKind.BLOCKED_TENANT_DATA,
        "Das Dokument enthält Mieterdaten; die KI darf es nicht lesen.",
        "Möchtest du die Werte selbst eintragen?",
    ),
}
_UNMATCHED_REASON = "Nicht zugeordnet: keine Regel, kein Abgleich und kein Beleg passen."
_UNMATCHED_QUESTION = "Wofür war diese Buchung?"
_PROPOSED_QUESTION = "Stimmt diese Zuordnung?"


def inbox(conn: Connection) -> list[InboxItem]:
    """Every item of §8.3, newest first. Texts quote no bank line, name or IBAN."""
    items = _proposal_items(conn) + import_inbox_items(conn) + _allocation_items(conn)
    return sorted(items, key=lambda item: item.created_at, reverse=True)


def _proposal_items(conn: Connection) -> list[InboxItem]:
    rows = conn.execute(
        select(proposal_table).where(
            proposal_table.c.status.in_([s.value for s in _PROPOSAL_TEXTS])
        )
    )
    items = []
    for row in rows:
        kind, reasoning, question = _PROPOSAL_TEXTS[ProposalStatus(row.status)]
        items.append(
            InboxItem(
                kind=kind,
                created_at=row.updated_at,
                reasoning=reasoning,
                question=question,
                document_id=row.document_id,
                proposal_id=row.id,
            )
        )
    return items


def _allocation_items(conn: Connection) -> list[InboxItem]:
    t = transaction_table.c
    a = allocation_table.c
    awaiting = exists().where(
        duplicate_table.c.other_transaction_id == t.id,
        duplicate_table.c.status == DuplicateStatus.OPEN.value,
    )
    lines = conn.execute(
        select(t.id, t.created_at)
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            import_table.c.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            ~awaiting,
        )
    ).all()
    current: dict[UUID, list] = {}
    for row in conn.execute(
        select(a.transaction_id, a.status, a.reasoning, a.question, a.recorded_at)
        .where(a.status != AllocationStatus.SUPERSEDED.value)
        .order_by(a.recorded_at, a.id)
    ):
        current.setdefault(row.transaction_id, []).append(row)
    items = []
    for line in lines:
        rows = current.get(line.id)
        if not rows:
            items.append(
                InboxItem(
                    kind=InboxItemKind.UNMATCHED,
                    created_at=line.created_at,
                    reasoning=_UNMATCHED_REASON,
                    question=_UNMATCHED_QUESTION,
                    transaction_id=line.id,
                )
            )
        elif rows[0].status == AllocationStatus.PROPOSED.value:
            items.append(
                InboxItem(
                    kind=InboxItemKind.ALLOCATION_PROPOSED,
                    created_at=rows[0].recorded_at,
                    reasoning=rows[0].reasoning,
                    question=rows[0].question or _PROPOSED_QUESTION,
                    transaction_id=line.id,
                )
            )
    return items
