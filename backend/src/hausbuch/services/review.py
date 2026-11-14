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

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Select, exists, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as transfer_link_table
from hausbuch.jobs import enqueue
from hausbuch.rules import matching as matchers
from hausbuch.rules.errors import (
    AI_DISABLED,
    RULE_CONFLICTS,
    RULE_REBOOK_BLOCKED,
    RULE_TOO_BROAD,
    RuleError,
)
from hausbuch.rules.household.taxonomy import BULK_CONFIRMABLE, allowed_categories
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
    MatchTransaction,
    NameHistory,
)
from hausbuch.schemas.api import (
    AiRetryResult,
    ConfirmAllocationsBody,
    ConfirmCategoryBody,
    ConfirmCategoryResult,
    ConfirmCategorySkip,
    InboxItem,
    InboxItemKind,
    NameCategoryCount,
    RebookBlocker,
    RebookBlockReason,
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
from .proposals import PROPOSAL_BANK_FILE_NOT_READ, PROPOSAL_EXCHANGE_EXPORT_NOT_READ
from .statements import _to_transaction, import_inbox_items, transaction_select

TRANSACTION_MISMATCH = "allocation.transaction_mismatch"
IMPORT_NOT_RECONCILED = "allocation.import_not_reconciled"
NOT_OPEN = "allocation.line_not_open"
BULK_CATEGORY_REFUSED = "inbox.category_not_bulk"  # „Alle bestätigen“ (D159 amended)
BULK_NOT_PRIVATE = "inbox.line_not_private"
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
    rebooked: list[UUID] = []
    if learn:
        corrected = {corrects} if corrects is not None else set()
        if proposed is None and body.rebook_earlier and refusal in _REBOOKABLE:
            # D160, 14 Nov 2026: the name's other confirmed lines are re-booked to this
            # answer first (all or none), then the rule is checked again against them.
            rebooked, rules_replaced = _rebook_earlier(ctx, line, confirmed, refusal)
            corrected |= rules_replaced
            names = _names(ctx.conn, line)
            proposed, refusal = rules_learned.proposed_rule_or_refusal(
                line.tx, confirmed, context, names=names
            )
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
            ctx, MatchingRuleCreate.model_validate(data), corrects=corrected
        )
    return _response(
        ctx.conn, line, written, context, names=names, rule=rule, rebooked=len(rebooked)
    )


# The name rule's refusals that come from the name's other confirmed lines only (D160).
_REBOOKABLE = frozenset({RULE_CONFLICTS, RULE_TOO_BROAD})


def _rebook_earlier(
    ctx: CommandContext, line: matching.Line, confirmed: AllocationSet, refusal: str
) -> tuple[list[UUID], set[UUID]]:
    """„Diese auch auf <Kategorie> umbuchen“ (phase 6 D160, 14 Nov 2026): every confirmed
    private line of the same sign whose name the line's name rule hits, and whose booking is
    not already the answer's category, is re-booked to that category as a person's
    correction (services.allocations.record_set: new confirmed rows, the old ones superseded,
    transaction.reallocated), each keeping its own period and recurring flag. All or none:
    a hit line that is not one confirmed row of a learnable household code without a
    property, tenancy, loan or Beleg, or that is linked as a transfer or a depot movement,
    or is no longer open, refuses the whole answer with rule.rebook_blocked, naming the kind
    of block and that line's id, booking date and amount, before anything is written (the
    GET names such lines beforehand in rebook_blockers, and the panel does not offer the box).

    Lines whose rows are not all confirmed, or that are already one row of the answer's
    category, are dropped from the unlocked read before any lock is taken; the rest are
    locked in id order and checked again under the lock. Two re-books of the same payee at
    once may still lock each other's lines in another order than a concurrent answer on one
    of them: Postgres then aborts one request with a deadlock error, and that request writes
    nothing. Returns the re-booked line ids and the rules whose bookings were corrected
    (create_rule replaces one with the same matcher, D49)."""
    if len(confirmed.allocations) != 1:
        raise RuleViolationError([refusal], "no rule can be learned from this answer")
    (only,) = confirmed.allocations
    tx = line.tx
    hits = sorted(
        (
            hit.transaction_id
            for hit in matching.name_lines(ctx.conn, tx)
            if hit.confirmed
            and not _already(hit.rows, only.category)
            and matchers.same_payee(tx.counterparty_name, hit.counterparty_name)
        ),
        key=str,
    )
    targets: list[tuple[matching.Line, Allocation]] = []
    for transaction_id in hits:
        hit = matching.load_line(ctx.conn, transaction_id, lock=True)
        if hit is None or not hit.current:
            continue
        if any(a.status is not AllocationStatus.CONFIRMED for a in hit.current):
            continue  # a waiting proposal is no earlier answer; the new rule re-checks it
        if _already(hit.current, only.category):
            continue
        reason = _block_of_line(hit)
        if reason is not None:
            raise _blocked(hit.tx, reason)
        targets.append((hit, hit.current[0]))
    target_ids = [hit.tx.id for hit, _ in targets]
    if _linked(ctx.conn, target_ids):
        first = next(hit for hit, _ in targets if _linked(ctx.conn, [hit.tx.id]))
        raise _blocked(first.tx, RebookBlockReason.LINKED)
    replaced: set[UUID] = set()
    for hit, row in targets:
        by_rule = _booked_by_rule(hit.current)
        if by_rule is not None:
            replaced.add(by_rule)
        draft = AllocationDraft(
            amount=hit.tx.amount,
            category=only.category,
            service_period_start=row.service_period_start,
            service_period_end=row.service_period_end,
            recurring=row.recurring,
        )
        answer = AllocationSet(
            transaction_id=hit.tx.id,
            allocations=[draft],
            method=AllocationMethod.USER,
            confidence=USER_CONFIDENCE,
        )
        allocations.record_set(ctx, answer, confirm=True, auto=False)
    return target_ids, replaced


def _already(rows: Sequence[Any], category: AllocationCategory) -> bool:
    """One row, already of the answer's category: nothing to re-book."""
    return len(rows) == 1 and AllocationCategory(rows[0].category) is category


def _blocked(tx: MatchTransaction, reason: RebookBlockReason) -> RuleViolationError:
    """rule.rebook_blocked, naming the kind of block and the blocking line by id, booking
    date and amount (never its counterparty)."""
    return RuleViolationError(
        [RULE_REBOOK_BLOCKED],
        f"an earlier line of the name cannot be re-booked ({reason.value}): transaction "
        f"{tx.id} booked {tx.booking_date.isoformat()} amount {tx.amount}",
    )


def _rebookable(hit: matching.Line) -> bool:
    """One confirmed row of a learnable household code with no scope or Beleg, on an open
    line of a private account."""
    return _block_of_line(hit) is None


def _block_of_line(hit: matching.Line) -> RebookBlockReason | None:
    return _block_of(
        private=hit.private,
        excluded=hit.excluded,
        awaiting_duplicate=hit.awaiting_duplicate,
        import_status=hit.import_status,
        rows=hit.current,
    )


def _block_of(
    *,
    private: bool,
    excluded: bool,
    awaiting_duplicate: bool,
    import_status: ImportStatus,
    rows: Sequence[Any],
) -> RebookBlockReason | None:
    """Why a confirmed line cannot be re-booked (_rebookable's checks, which the GET's
    rebook_blockers reads too): not an open line of a private account, more than one row, a
    row with a property, unit, tenancy or loan, a Beleg, or a code no rule is learned for.
    The transfer and depot links are _linked's. `rows` are Allocations or name_lines rows."""
    if not private or excluded or awaiting_duplicate:
        return RebookBlockReason.NOT_OPEN
    if import_status is not ImportStatus.RECONCILED:
        return RebookBlockReason.NOT_OPEN
    if len(rows) != 1:
        return RebookBlockReason.SPLIT
    (row,) = rows
    if any(v is not None for v in (row.property_id, row.unit_id, row.tenancy_id, row.loan_id)):
        return RebookBlockReason.SCOPED
    if row.document_id is not None:
        return RebookBlockReason.DOCUMENT
    if not matchers.learnable_household(AllocationCategory(row.category)):
        return RebookBlockReason.CATEGORY
    return None


def _linked(conn: Connection, transaction_ids: list[UUID]) -> bool:
    """Whether a line is a leg of a current transfer link (D162) or carries a depot
    movement (D165): its booking belongs to that link, not to a name rule."""
    if not transaction_ids:
        return False
    link = transfer_link_table.c
    movement = movement_table.c
    linked = conn.execute(
        select(
            exists().where(
                link.superseded_by.is_(None),
                or_(
                    link.out_transaction_id.in_(transaction_ids),
                    link.in_transaction_id.in_(transaction_ids),
                ),
            )
            | exists().where(
                movement.superseded_by.is_(None), movement.transaction_id.in_(transaction_ids)
            )
        )
    ).scalar_one()
    return bool(linked)


def _rebook_blockers(conn: Connection, tx: MatchTransaction) -> list[RebookBlocker]:
    """D160: the payee's earlier confirmed lines that _rebook_earlier would refuse, by the
    same checks without a lock, oldest first: what the panel names instead of offering
    „Diese auch auf … umbuchen“. The re-book skips a line already of the chosen category,
    so the panel drops a blocker whose category is the one chosen."""
    lines = [
        hit
        for hit in matching.name_lines(conn, tx)
        if hit.confirmed and matchers.same_payee(tx.counterparty_name, hit.counterparty_name)
    ]
    if not lines:
        return []
    t = transaction_table.c
    awaiting = (
        exists()
        .where(
            duplicate_table.c.other_transaction_id == t.id,
            duplicate_table.c.status == DuplicateStatus.OPEN.value,
        )
        .label("awaiting")
    )
    state = {
        row.id: row
        for row in conn.execute(
            select(t.id, t.booking_date, import_table.c.status, awaiting)
            .join(import_table, import_table.c.id == t.import_id)
            .where(t.id.in_([hit.transaction_id for hit in lines]))
        )
    }
    reasons: dict[UUID, RebookBlockReason] = {}
    for hit in lines:
        reason = _block_of(
            private=True,  # name_lines reads private accounts only, never a merged line
            excluded=False,
            awaiting_duplicate=bool(state[hit.transaction_id].awaiting),
            import_status=ImportStatus(state[hit.transaction_id].status),
            rows=hit.rows,
        )
        if reason is not None:
            reasons[hit.transaction_id] = reason
    rest = [hit.transaction_id for hit in lines if hit.transaction_id not in reasons]
    if _linked(conn, rest):
        for transaction_id in rest:
            if _linked(conn, [transaction_id]):
                reasons[transaction_id] = RebookBlockReason.LINKED
    return [
        RebookBlocker(
            transaction_id=hit.transaction_id,
            booking_date=state[hit.transaction_id].booking_date,
            amount=hit.amount,
            reason=reasons[hit.transaction_id],
            category=AllocationCategory(hit.rows[0].category) if len(hit.rows) == 1 else None,
        )
        for hit in lines
        if hit.transaction_id in reasons
    ]


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
        account_id=line.account_id,
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
    rebooked: int = 0,
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
    history = _name_history(line, context, names)
    return TransactionAllocations(
        transaction=_transaction(conn, transaction_id),
        allocations=rows,
        can_learn_rule=can_learn,
        learn_rule_default=default,
        no_default_rule_categories=[c for c in AllocationCategory if THRESHOLDS[c] is None],
        unlearnable_categories=_unlearnable(line),
        waiting_other_amounts=(
            matching.waiting_other_amounts(
                conn, tx, shared=tx.counterparty_iban_hmac in context.shared_iban_hmacs
            )
            if can_learn
            else []
        ),
        rule=rule,
        name_history=history,
        rebook_blockers=_rebook_blockers(conn, tx) if history else [],
        rebooked=rebooked,
    )


def _name_history(
    line: matching.Line, context: MatchContext, names: list[NameHistory]
) -> list[NameCategoryCount]:
    """D160: the payee's other confirmed lines by category, when the line's rule would be on
    its name (a private line without an IBAN, or on a shared one): the lines the name rule's
    checks read (rules.matching.name_hits), most frequent first."""
    tx = line.tx
    if not line.private:
        return []
    hmac = tx.counterparty_iban_hmac
    if hmac is not None and hmac not in context.shared_iban_hmacs:
        return []
    counts = Counter(h.category for h in matchers.name_hits(tx, names))
    order = list(AllocationCategory)
    return [
        NameCategoryCount(category=category, count=count)
        for category, count in sorted(counts.items(), key=lambda i: (-i[1], order.index(i[0])))
    ]


def _unlearnable(line: matching.Line) -> list[AllocationCategory]:
    """The categories the line's account takes for which a one-row answer can never become
    a rule, whatever is typed (D160 on a private account: a transfer, depot or tax-relevant
    code, or one without a threshold); none on the rental account, whose limits depend on the
    counterparty (D38) and stay can_learn_rule's."""
    if not line.private:
        return []
    allowed = allowed_categories(line.account_purpose)
    return [c for c in AllocationCategory if c in allowed and not matchers.learnable_household(c)]


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
# Phase 6 D176 (3), step 4: a card or depot statement uploaded through Dokumente is read by
# code on its account; the card offers „Auf Konto importieren“ instead of a new read.
_BANK_FILE_TEXTS = (
    "Ein Kontoauszug oder Umsatz-Export einer Bank, eine Kreditkartenabrechnung oder ein "
    "Kontoauszug eines Depots: Hausbuch liest sie selbst, ohne KI, auf ihrem Konto ein.",
    "Auf welches Konto soll sie importiert werden?",
)
# Phase 6 D166, step 5a: an exchange export uploaded through Dokumente.
_EXCHANGE_EXPORT_TEXTS = (
    "Ein Export einer Börse (BISON oder Binance): Hausbuch liest ihn selbst, ohne KI, auf dem "
    "Konto der Börse ein, nicht unter Dokumente.",
    "Unter Konten das Konto der Börse öffnen und den Export dort mit „Export der Börse hochladen“ "
    "hochladen; gibt es noch keins, unter Konten ein Konto der Art „Börse (Krypto)“ anlegen.",
)
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
    """Every item of §8.3, newest first. Texts quote no bank line, name or IBAN. Also the
    phase 6 D161 cards of private lines paying property costs, and a depot whose latest
    report disagrees with its booked movements (phase 6 step 4). With `today`, also last year's
    open Jahresfragen (phase 4b D110, `questionnaire_open`), the reminders visible today
    (phase 5 §6, `reminder`) and the transfer cards (phase 6 D162); with `now`, the email-in
    cards (phase 5 §5.4: email_received, email_failed) and the bank sync's (phase 5 §4.6,
    D117: bank_sync_failed, bank_balance_drift)."""
    items = _proposal_items(conn) + import_inbox_items(conn) + _allocation_items(conn)
    from .property_costs import property_cost_items  # it imports the matching services

    items += property_cost_items(conn)
    from .depot import holdings_inbox_items  # it imports the asset services

    items += holdings_inbox_items(conn)
    from .exchange.imports import exchange_inbox_items  # it imports the transfer services

    items += exchange_inbox_items(conn)
    if today is not None:
        items += _questionnaire_items(conn, today)
        from .transfers import transfer_inbox_items

        items += transfer_inbox_items(conn, today)
        from .reminders import reminder_inbox_items  # it imports the checklist services

        items += reminder_inbox_items(conn, today)
    if now is not None:
        from .inbound_mail import inbound_mail_items  # it imports the statement services

        items += inbound_mail_items(conn, now)
        from .bank_sync import bank_sync_items  # it imports the statement services

        items += bank_sync_items(conn)
        from .prices.overview import price_inbox_items  # phase 6 step 5b

        items += price_inbox_items(conn, now)
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
        if kind is InboxItemKind.READ_FAILED and row.error_code == PROPOSAL_BANK_FILE_NOT_READ:
            reasoning, question = _BANK_FILE_TEXTS
        if (
            kind is InboxItemKind.READ_FAILED
            and row.error_code == PROPOSAL_EXCHANGE_EXPORT_NOT_READ
        ):
            reasoning, question = _EXCHANGE_EXPORT_TEXTS
        items.append(
            InboxItem(
                kind=kind,
                created_at=row.updated_at,
                reasoning=reasoning,
                question=question,
                document_id=row.document_id,
                proposal_id=row.id,
                error_code=row.error_code if kind is InboxItemKind.READ_FAILED else None,
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
        select(a.transaction_id, a.status, a.category, a.reasoning, a.question, a.recorded_at)
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
            category = AllocationCategory(rows[0].category) if len(rows) == 1 else None
            items.append(
                InboxItem(
                    kind=InboxItemKind.ALLOCATION_PROPOSED,
                    created_at=rows[0].recorded_at,
                    reasoning=rows[0].reasoning,
                    question=rows[0].question or _PROPOSED_QUESTION,
                    transaction_id=line.id,
                    account_id=line.account_id if private else None,
                    category=category,
                    bulk_confirmable=private
                    and category in BULK_CONFIRMABLE
                    and rows[0].question is None,
                )
            )
    return items


# --- POST /inbox/confirm-category -----------------------------------------------------------


def confirm_category(ctx: CommandContext, body: ConfirmCategoryBody) -> ConfirmCategoryResult:
    """„Alle bestätigen“ (phase 6 D159 amended 13 Nov 2026): the person confirms the listed
    private lines' proposals of one plain household category, each exactly as a single
    confirmation does (allocations.confirm_proposed: the rows move to confirmed by the person,
    one transaction.allocated event per line), in this one transaction. No rule is learned.

    Only the lines named in the body, never the category's other lines. A listed line whose
    proposal changed since the list was shown (confirmed or answered meanwhile, re-proposed
    in another category or split, given a question) or that no longer counts is skipped and
    named. Refused, nothing confirmed: a category „Alle bestätigen“ does not offer
    (inbox.category_not_bulk: a tax-relevant, transfer, depot or uncategorized code, money to
    family, work costs, capital income), a line of the rental account (inbox.line_not_private),
    a line of no account of this workspace (404). Lines are locked in id order, so two
    requests never wait on each other crosswise."""
    if body.category not in BULK_CONFIRMABLE:
        raise RuleViolationError([BULK_CATEGORY_REFUSED], "this category is confirmed line by line")
    confirmed = 0
    skipped = []
    for transaction_id in sorted(set(body.transaction_ids)):
        line = matching.load_line(ctx.conn, transaction_id, lock=True)
        if line is None:
            raise NotFoundError(EntityType.TRANSACTION, transaction_id)
        if not line.private:
            raise RuleViolationError([BULK_NOT_PRIVATE], "only private lines are confirmed so")
        reason = _bulk_skip(line, body.category)
        if reason is not None:
            skipped.append(ConfirmCategorySkip(transaction_id=transaction_id, reason=reason))
            continue
        allocations.confirm_proposed(ctx, transaction_id)
        confirmed += 1
    return ConfirmCategoryResult(confirmed=confirmed, skipped=skipped)


def _bulk_skip(line: matching.Line, category: AllocationCategory) -> str | None:
    """Why a listed line is not confirmed with the others, or None (the inbox's
    bulk_confirmable, read again under the line's lock)."""
    if line.excluded or line.awaiting_duplicate:
        return "not_open"
    if line.import_status is not ImportStatus.RECONCILED:
        return "not_open"
    if not line.current or not line.waiting:
        return "not_waiting"
    if len(line.current) != 1:
        return "split"
    (row,) = line.current
    if row.category is not category:
        return "category_changed"
    if row.question is not None:
        return "question"
    return None


# --- POST /inbox/ai-retry ----------------------------------------------------------------


def request_ai_suggestions(ctx: CommandContext, *, ai_enabled: bool) -> AiRetryResult:
    """Queue match_transaction for every line the queue lists as unmatched (no current rows),
    in this transaction; returns how many (D50). The job runs the whole order again, the model
    last, so a line a rule or matcher now books costs no call. A line already queued gets a
    second job, which is harmless: the first one's rows close it (Line.open_for). A private
    line is held back and counted while `ai_private_accounts` is off (D168), so the screen can
    say why nothing was sent. Refused with ai.disabled when HAUSBUCH_AI_ENABLED is off, since
    the job would then never call the model.
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
    held_back = 0
    if not workspace_settings.private_ai_allowed(ctx.conn):  # D168: the model may not see them
        is_private = account_table.c.purpose == AccountPurpose.PRIVATE.value
        held_back = len(ctx.conn.execute(listed.where(is_private)).all())
        listed = listed.where(~is_private)
    ids = list(ctx.conn.execute(listed.order_by(t.booking_date, t.id)).scalars())
    for transaction_id in ids:
        enqueue.enqueue_match_transaction(
            ctx.conn, workspace_id=ctx.workspace_id, transaction_id=transaction_id
        )
    return AiRetryResult(queued=len(ids), private_held_back=held_back)
