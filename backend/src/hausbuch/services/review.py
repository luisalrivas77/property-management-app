"""The review queue and a person's answer (docs/phases/phase-2.md §8.3, §8.1, D25, D38).

- `inbox`: GET /inbox, one list newest first: pending proposals (ready, failed,
  blocked_tenant_data), unreconciled or pending-balance imports, open possible duplicates
  (statements.import_inbox_items), and lines of reconciled imports with a proposed
  allocation set or none (excluded lines and the later line of an open possible duplicate
  are not listed: the duplicate is decided first).
  An unmatched line whose latest categorize call failed says so (D50), so a failing model is
  visible in the queue rather than looking like "nothing fits".
- `allocate_transaction`: PUT /transactions/{id}/allocations. The person's answer is written
  through services.allocations (sum check, events, paid_on), optionally followed by a learned
  rule (services.rules_learned).
- `request_ai_suggestions`: POST /inbox/ai-retry (D50). Queues match_transaction (the normal
  order, so the model may be called) for every listed line that has no rows. Nothing else
  re-asks the model: rematch_waiting and rematch_on_evidence never call it.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import Connection, Select, exists, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import AI_DISABLED, RULE_CONFLICTS, RuleError
from hausbuch.rules.review import THRESHOLDS
from hausbuch.schemas.ai import AiJob
from hausbuch.schemas.allocation import (
    Allocation,
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
    MatchingRuleCreate,
    NameHistory,
)
from hausbuch.schemas.api import (
    AiRetryResult,
    ConfirmAllocationsBody,
    InboxItem,
    InboxItemKind,
    TransactionAllocations,
)
from hausbuch.schemas.bank import Transaction
from hausbuch.schemas.enums import (
    AccountPurpose,
    AllocationStatus,
    DuplicateStatus,
    EntityType,
    ImportStatus,
    ProposalStatus,
)

from . import allocations, matching, rules_learned, workspace_settings
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
    allocations.check_categories(ctx.conn, line.account_id, drafts)
    current = line.current
    corrects = None
    if current and allocations.same_answer(drafts, current):
        if current[0].status is AllocationStatus.PROPOSED:
            written = allocations.confirm_proposed(ctx, transaction_id)
        else:
            written = current
    else:
        corrects = _booked_by_rule(current)
        answer = AllocationSet(
            transaction_id=transaction_id,
            allocations=drafts,
            method=AllocationMethod.USER,
            confidence=USER_CONFIDENCE,
        )
        written = allocations.record_set(ctx, answer, confirm=True, auto=False)
    confirmed = _as_set(transaction_id, written)
    context = _context(ctx.conn, ctx.now.date(), line, iban_key)
    names = _names(ctx.conn, line)
    proposed, refusal = rules_learned.proposed_rule_or_refusal(
        line.tx,
        confirmed,
        context,
        name_pattern=body.counterparty_name_pattern,
        purpose_pattern=body.purpose_pattern,
        names=names,
    )
    learn = body.learn_rule
    if learn is None:
        learn = rules_learned.learn_default(proposed, confirmed)
    rule = None
    if learn:
        if proposed is None:
            # D160: a name rule refused for the name's other lines says why, so the card can
            # ask for a purpose pattern (rule.too_broad) or name the conflict (rule.conflicts).
            raise RuleViolationError(
                [refusal or rules_learned.NOT_LEARNABLE],
                "no rule can be learned from this answer",
            )
        data = proposed.model_dump() | {
            "created_from_allocation_id": written[0].id,
            "same_amount_only": body.same_amount_only and not rules_learned.checks_amount(proposed),
        }
        rule = rules_learned.create_rule(
            ctx, MatchingRuleCreate.model_validate(data), corrects=corrects
        )
    return _response(ctx.conn, line, written, context, names=names, rule=rule)


def _booked_by_rule(current: list[Allocation]) -> UUID | None:
    """The rule whose booking an answer with another split corrects (D49, review F2): the
    line's current rows are all confirmed and all carry that rule's id. None for an open
    line or a proposal (the model's, a matcher's, a rule's near miss): an answer there is
    another kind of payment, not a correction."""
    rule_ids = {a.rule_id for a in current}
    if not current or len(rule_ids) != 1:
        return None
    if any(a.status is not AllocationStatus.CONFIRMED for a in current):
        return None
    return next(iter(rule_ids))


def transaction_allocations(
    conn: Connection, transaction_id: UUID, *, iban_key: bytes, today: date
) -> TransactionAllocations | None:
    line = matching.load_line(conn, transaction_id)
    if line is None:
        return None
    context = _context(conn, today, line, iban_key)
    return _response(conn, line, line.current, context, names=_names(conn, line))


def _names(conn: Connection, line: matching.Line) -> list[NameHistory]:
    """The private lines a name rule from this line is checked against (D160); none for a
    rental line, which learns on its IBAN only."""
    return matching.name_history(conn, line.tx) if line.private else []


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


def _context(conn: Connection, today: date, line: matching.Line, iban_key: bytes) -> MatchContext:
    return matching.build_context(
        conn,
        iban_key=iban_key,
        today=today,
        for_transaction=line.tx.id,
        account_purpose=line.account_purpose,
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


def _probe_category(line: matching.Line) -> AllocationCategory:
    """A category the line's account takes and a rule may be learned for, to ask whether the
    line can become a rule at all: a learnable household code on a private line (D160)."""
    if not line.private:
        return AllocationCategory.OTHER_EXPENSE
    return AllocationCategory.HH_SALARY if line.tx.amount > 0 else AllocationCategory.HH_GROCERIES


def _response(
    conn: Connection,
    line: matching.Line,
    rows: list[Allocation],
    context: MatchContext,
    *,
    names: list[NameHistory],
    rule=None,  # noqa: ANN001 - MatchingRule | None
) -> TransactionAllocations:
    tx = line.tx
    transaction_id = tx.id
    probe = AllocationSet(
        transaction_id=transaction_id,
        allocations=[AllocationDraft(amount=tx.amount, category=_probe_category(line))],
        method=AllocationMethod.USER,
        confidence=USER_CONFIDENCE,
    )
    probed, refusal = rules_learned.proposed_rule_or_refusal(tx, probe, context, names=names)
    # rule.conflicts depends on the answer (another category may be learned); too_broad not.
    can_learn = probed is not None or refusal == RULE_CONFLICTS
    default = False
    if rows:
        current = _as_set(transaction_id, rows)
        default = rules_learned.learn_default(
            rules_learned.proposed_rule(tx, current, context, names=names), current
        )
    return TransactionAllocations(
        transaction=_transaction(conn, transaction_id),
        allocations=rows,
        can_learn_rule=can_learn,
        learn_rule_default=default,
        no_default_rule_categories=[c for c in AllocationCategory if THRESHOLDS[c] is None],
        waiting_other_amounts=matching.waiting_other_amounts(conn, tx) if can_learn else [],
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
_AI_FAILED_REASON = "Die KI konnte diese Zahlung nicht einordnen (Anfrage fehlgeschlagen)."
# Phase 6 D168: a private line nothing explains, while the model may not see private lines.
_PRIVATE_NO_AI_REASON = (
    "Privates Konto: keine Regel und kein Beleg passen. Die KI ordnet private Umsätze erst zu, "
    "wenn beide unter Einstellungen „Private Konten mit KI zuordnen“ zugestimmt haben."
)
_UNMATCHED_QUESTION = "Wofür war diese Buchung?"
_PROPOSED_QUESTION = "Stimmt diese Zuordnung?"


def inbox(
    conn: Connection, today: date | None = None, now: datetime | None = None
) -> list[InboxItem]:
    """Every item of §8.3, newest first. Texts quote no bank line, name or IBAN. With `today`,
    also last year's open Jahresfragen (phase 4b D110, `questionnaire_open`) and the reminders
    visible today (phase 5 §6, `reminder`); with `now`, the email-in cards (phase 5 §5.4:
    email_received, email_failed) and the bank sync's (phase 5 §4.6, D117: bank_sync_failed,
    bank_balance_drift)."""
    items = _proposal_items(conn) + import_inbox_items(conn) + _allocation_items(conn)
    if today is not None:
        items += _questionnaire_items(conn, today)
        from .reminders import reminder_inbox_items  # it imports the checklist services

        items += reminder_inbox_items(conn, today)
    if now is not None:
        from .inbound_mail import inbound_mail_items  # it imports the statement services

        items += inbound_mail_items(conn, now)
        from .bank_sync import bank_sync_items  # it imports the statement services

        items += bank_sync_items(conn)
    return sorted(items, key=lambda item: item.created_at, reverse=True)


def _questionnaire_items(conn: Connection, today: date) -> list[InboxItem]:
    """One card for the previous year's questionnaire from 1 January until every slot is
    answered. Never raises: a year without a questionnaire (before the first file) or a
    failure to compute the progress gives no card, so the queue always loads."""
    from .questionnaire import questionnaire_progress  # it imports the tax services

    year = today.year - 1
    try:
        progress = questionnaire_progress(conn, year)
    except (RuleError, ValueError):  # no file for the year; a file that does not validate
        return []
    if not progress.open:
        return []
    return [
        InboxItem(
            kind=InboxItemKind.QUESTIONNAIRE_OPEN,
            created_at=datetime(today.year, 1, 1, tzinfo=UTC),
            reasoning=(
                f"Die Jahresfragen {year} sind noch offen:"
                f" {progress.answered} von {progress.total} beantwortet."
            ),
            question=f"Möchtest du die Jahresfragen {year} jetzt beantworten?",
            tax_year=year,
        )
    ]


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


def _listed_lines() -> Select:
    """The lines the queue may list: a reconciled import, not excluded as a duplicate, not
    the later line of an open possible duplicate (Line.open_for's conditions)."""
    t = transaction_table.c
    awaiting = exists().where(
        duplicate_table.c.other_transaction_id == t.id,
        duplicate_table.c.status == DuplicateStatus.OPEN.value,
    )
    return (
        select(t.id, t.created_at, t.account_id, account_table.c.purpose)
        .join(import_table, import_table.c.id == t.import_id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            import_table.c.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            ~awaiting,
        )
    )


def _failed_categorize(conn: Connection) -> set[UUID]:
    """The lines whose latest categorize call (ai_call, job match) failed: an API error or an
    unusable answer, both stored with an error_code (D50). One query (DISTINCT ON)."""
    c = ai_call_table.c
    latest = (
        select(c.transaction_id, c.error_code)
        .where(c.job == AiJob.MATCH.value, c.transaction_id.is_not(None))
        .distinct(c.transaction_id)
        .order_by(c.transaction_id, c.created_at.desc(), c.id.desc())
    )
    return {row.transaction_id for row in conn.execute(latest) if row.error_code is not None}


def _allocation_items(conn: Connection) -> list[InboxItem]:
    a = allocation_table.c
    lines = conn.execute(_listed_lines()).all()
    failed = _failed_categorize(conn)
    private_ai = workspace_settings.private_ai_allowed(conn)
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
        private = line.purpose == AccountPurpose.PRIVATE.value
        if not rows:
            if line.id in failed:
                reasoning = _AI_FAILED_REASON
            elif private and not private_ai:
                reasoning = _PRIVATE_NO_AI_REASON  # routing.ai_disabled (D168)
            else:
                reasoning = _UNMATCHED_REASON
            items.append(
                InboxItem(
                    kind=InboxItemKind.UNMATCHED,
                    created_at=line.created_at,
                    reasoning=reasoning,
                    question=_UNMATCHED_QUESTION,
                    transaction_id=line.id,
                    account_id=line.account_id if private else None,
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
                    account_id=line.account_id if private else None,
                )
            )
    return items


# --- POST /inbox/ai-retry ----------------------------------------------------------------


def request_ai_suggestions(ctx: CommandContext, *, ai_enabled: bool) -> AiRetryResult:
    """Queue match_transaction for every line the queue lists as unmatched (no current rows),
    in this transaction; returns how many (D50). The job runs the whole order again, the model
    last, so a line a rule or matcher now books costs no call. A line already queued gets a
    second job, which is harmless: the first one's rows close it (Line.open_for). Refused with
    ai.disabled when HAUSBUCH_AI_ENABLED is off, since the job would then never call the model.
    Writes no event: nothing is recorded but the jobs, and each model call gets its ai_call
    row (as _on_reconciled queues match_import without an event)."""
    if not ai_enabled:
        raise RuleViolationError([AI_DISABLED], "the AI layer is switched off on the server")
    a = allocation_table.c
    t = transaction_table.c
    has_rows = exists().where(
        a.transaction_id == t.id, a.status != AllocationStatus.SUPERSEDED.value
    )
    listed = _listed_lines().where(~has_rows)
    if not workspace_settings.private_ai_allowed(ctx.conn):  # D168: the model may not see them
        listed = listed.where(account_table.c.purpose != AccountPurpose.PRIVATE.value)
    ids = list(ctx.conn.execute(listed.order_by(t.booking_date, t.id)).scalars())
    for transaction_id in ids:
        enqueue.enqueue_match_transaction(
            ctx.conn, workspace_id=ctx.workspace_id, transaction_id=transaction_id
        )
    return AiRetryResult(queued=len(ids))
