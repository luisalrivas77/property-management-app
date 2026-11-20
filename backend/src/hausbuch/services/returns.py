"""A returned payment paired with its original (docs/phases/phase-6.md, D162 amended (7),
15 Nov 2026; Luis: „What is the 3200€ payment still there?“).

A private account's credit whose purpose reads as a return and whose amount equals an earlier
debit of the same account (rules.household.returns) is shown on Zu prüfen as „Rücküberweisung
erkannt“, computed on read like the transfer cards (no stored state: a new import's lines are
recognised on the next read, and `hausbuch pair-returns` only lists what it sees). The card's
button books the pair (book_return): both lines through the person's answer
(review.allocate_transaction, method user, events) as internal_transfer_out and _in, then a
transfer_link joining the two lines of the one account (matched_by user, transfer.linked).
No migration: the link table has no rule that its legs sit on two accounts; only the link by
hand (transfers._pair) asks for that.

Once booked, the two lines count as nothing: a transfer code is neither income nor spending
on the Haushalt and Übersicht; transfers.list_links (the Haushalt's „Gespart, nicht
ausgegeben“, saved_in) never lists a link of one account; both legs are linked, so neither
gets a transfer card; Konten › Umsätze names each half's partner (returned_partners). The
pair counts like a matcher's link (transfers._current_links): while both lines are booked as
transfers. A person who re-books one of them withdraws it and the card offers the pair again.

The card ends without booking when a person or a rule confirms the credit as something other
than a transfer (their answer that it is no return). A debit a person already booked (as
spending, say) is still offered; booking the pair re-books it through the same answer. The
rental account is never read here: its lines keep phase 2's matchers."""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, time
from uuid import UUID

from sqlalchemy import Connection, exists, func, select

from hausbuch import i18n
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.errors import TRANSFER_RETURN_INVALID
from hausbuch.rules.household.returns import ReturnLine, return_candidates, window_start
from hausbuch.rules.matching import transfer_link_kind
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.bank import ReturnedPartner
from hausbuch.schemas.enums import AccountPurpose, DuplicateStatus, ImportStatus
from hausbuch.schemas.household import (
    OpenReturn,
    ReturnedPaymentCreate,
    TransferLegSource,
    TransferLink,
    TransferMatchedBy,
)

from . import transfers
from .context import CommandContext
from .errors import ConflictError, NotFoundError, RuleViolationError

# A cheap first filter in the database for rules.household.returns.reads_as_return, which
# decides. It must never be stricter than the rule: every return word, as the rule reads it
# (case ignored, ü written ü or ue), starts with one of these stems, and it is matched against
# the purpose with all whitespace removed, since the rule ignores spacing (a word wrapped across
# purpose lines still reads). The plain u it also lets through („ruck“) reads as no return word;
# the rule drops such a line.
_STEMS = r"r(ü|Ü|ue|u)ck|recall|retour"
_TRANSFER_IN = frozenset({AllocationCategory.INTERNAL_TRANSFER_IN})

# The card's texts, German beside English (phase 10 D284); chosen when Zu prüfen is read.
TEXTS = i18n.Texts(
    "returns",
    {
        "reasoning": (
            "Diese Gutschrift bringt eine frühere Abbuchung desselben Kontos zurück"
            " (Rücküberweisung): Das Geld ging hinaus und kam wieder. Zusammen zählen die beiden"
            " Buchungen als nichts, weder als Ausgabe noch als Einnahme noch als Überweisung.",
            "This credit returns an earlier debit of the same account (a returned payment):"
            " the money went out and came back. Together the two transactions count as"
            " nothing, neither as spending nor as income nor as a transfer.",
        ),
        "question.one": (
            "Als Rücküberweisung verbuchen?",
            "Book as a returned payment?",
        ),
        "question.several": ("Welche Abbuchung kam zurück?", "Which debit came back?"),
    },
)
REASONING = TEXTS.german("reasoning")
QUESTION_ONE = TEXTS.german("question.one")
QUESTION_SEVERAL = TEXTS.german("question.several")


def _lines(conn: Connection, *where) -> list[ReturnLine]:  # noqa: ANN002
    """The private accounts' lines that count (a reconciled import, not merged away, not the
    later line of an open possible duplicate, as review._listed_lines: no card until the
    duplicate question is answered)."""
    t, acc = transaction_table.c, account_table.c
    awaiting = exists().where(
        duplicate_table.c.other_transaction_id == t.id,
        duplicate_table.c.status == DuplicateStatus.OPEN.value,
    )
    rows = conn.execute(
        select(
            t.id,
            t.account_id,
            t.booking_date,
            t.amount,
            t.purpose,
            t.counterparty_iban_hmac,
            t.counterparty_name,
        )
        .join(account_table, acc.id == t.account_id)
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            acc.purpose == AccountPurpose.PRIVATE.value,
            t.excluded_as_duplicate_of.is_(None),
            import_table.c.status == ImportStatus.RECONCILED.value,
            ~awaiting,
            *where,
        )
        .order_by(t.booking_date, t.id)
    ).all()
    return [
        ReturnLine(
            id=r.id,
            account_id=r.account_id,
            booked_on=r.booking_date,
            amount=r.amount,
            purpose=r.purpose or "",
            counterparty_iban_hmac=None
            if r.counterparty_iban_hmac is None
            else bytes(r.counterparty_iban_hmac).hex(),
            counterparty_name=r.counterparty_name,
        )
        for r in rows
    ]


def _debits_before(conn: Connection, credit: ReturnLine) -> list[ReturnLine]:
    t = transaction_table.c
    return _lines(
        conn,
        t.account_id == credit.account_id,
        t.amount == -credit.amount,
        t.booking_date >= window_start(credit.booked_on),
        t.booking_date <= credit.booked_on,
    )


def _answered_otherwise(conn: Connection, credit_id: UUID) -> bool:
    """Whether a person or a rule confirmed the credit as something other than a transfer in
    (transfers._held_answer): their answer that it is no return, which ends its card."""
    held = transfers._held_answer(conn, credit_id)
    return held is not None and not held <= _TRANSFER_IN


def _candidates(conn: Connection, credit: ReturnLine, linked: set[UUID]) -> list[UUID]:
    debits = [d for d in _debits_before(conn, credit) if d.id not in linked]
    return return_candidates(credit, debits)


def _pairs(conn: Connection) -> list[tuple[UUID, list[UUID]]]:
    """Every waiting returned payment: the credit and the debits it may return."""
    t = transaction_table.c
    unspaced = func.regexp_replace(t.purpose, r"\s", "", "g")
    credits = _lines(conn, t.amount > 0, unspaced.op("~*")(_STEMS))
    if not credits:
        return []
    linked = transfers._linked_ids(conn)
    found = []
    for credit in credits:
        if credit.id in linked or _answered_otherwise(conn, credit.id):
            continue
        candidates = _candidates(conn, credit, linked)
        if candidates:
            found.append((credit.id, candidates))
    return found


def open_returns(conn: Connection) -> list[OpenReturn]:
    """The „Rücküberweisung erkannt“ cards, computed now, oldest credit first."""
    pairs = _pairs(conn)
    if not pairs:
        return []
    ids = {i for credit, debits in pairs for i in (credit, *debits)}
    legs = {
        leg.leg.id: leg
        for leg in transfers._line_legs(conn, transaction_table.c.id.in_(sorted(ids)))
    }
    cards = []
    for credit, debits in pairs:
        if credit not in legs or any(d not in legs for d in debits):
            continue  # a 0 € line, which a transfer leg never is
        cards.append(
            OpenReturn(leg=legs[credit].view(), candidates=[legs[d].view() for d in debits])
        )
    return cards


def return_inbox_items(conn: Connection) -> list[InboxItem]:
    """Zu prüfen's „Rücküberweisung erkannt“ cards (InboxItemKind.RETURNED_PAYMENT), dated by
    the returning credit."""
    return [
        InboxItem(
            kind=InboxItemKind.RETURNED_PAYMENT,
            created_at=datetime.combine(card.leg.booked_on, time(), tzinfo=UTC),
            reasoning=TEXTS("reasoning"),
            question=TEXTS("question.one" if len(card.candidates) == 1 else "question.several"),
            transaction_id=card.leg.id,
            account_id=card.leg.account_id,
            returned=card,
        )
        for card in open_returns(conn)
    ]


def lines_on_cards(items: Iterable[InboxItem]) -> tuple[set[UUID], set[UUID]]:
    """D162 amended (7), as D162 amended (5) for the transfer cards: one decision, one place.
    The lines of the „Rücküberweisung erkannt“ cards among `items`: (every line a card names,
    the credit and each debit it offers; the lines the card's button surely books, the credit
    and a card's one debit). Zu prüfen drops a transfer card whose leg is among the first (the
    credit's „Überweisung ohne Gegenstück“ is this card's question) and lists the model's or a
    matcher's proposal of a line among the second on the card only."""
    named: set[UUID] = set()
    booked: set[UUID] = set()
    for item in items:
        card = item.returned
        if item.kind is not InboxItemKind.RETURNED_PAYMENT or card is None:
            continue
        named.add(card.leg.id)
        named.update(c.id for c in card.candidates)
        booked.add(card.leg.id)
        if len(card.candidates) == 1:
            booked.add(card.candidates[0].id)
    return named, booked


def book_return(
    ctx: CommandContext, body: ReturnedPaymentCreate, *, iban_key: bytes
) -> TransferLink:
    """POST /returns: „Als Rücküberweisung verbuchen“. Both lines are booked by the person's
    answer as internal_transfer_out and _in (their events), then linked to each other
    (transfer.linked), all in one transaction. The pair linked already returns its link. 404 for
    an unknown line; 409 when either line is linked to another (also when booking one of them
    let the transfer matcher link it elsewhere: nothing is written); 422 transfer.return_invalid
    when the two are no returned payment of one private account (the credit's purpose and the
    equal debit within 30 days before, rules.household.returns), and when the card is not
    offered: the credit holds a person's or a rule's answer other than a transfer in, or either
    line is the later line of an open possible duplicate (the write accepts what the read
    offers)."""
    transfers._lock(ctx)
    conn = ctx.conn
    out = transfers._one_leg(conn, TransferLegSource.TRANSACTION, body.out_transaction_id)
    in_ = transfers._one_leg(conn, TransferLegSource.TRANSACTION, body.in_transaction_id)
    if out is None:
        raise NotFoundError("transaction", body.out_transaction_id)
    if in_ is None:
        raise NotFoundError("transaction", body.in_transaction_id)
    already = transfers._link_of(conn, out.leg.id, in_.leg.id)
    if already is not None:
        return already
    linked = transfers._linked_ids(conn)
    if out.leg.id in linked or in_.leg.id in linked:
        raise ConflictError("a line of this returned payment is linked already")
    found = _lines(conn, transaction_table.c.id == in_.leg.id)
    credit = found[0] if found else None
    valid = (
        credit is not None
        and out.purpose is AccountPurpose.PRIVATE
        and out.leg.account_id == in_.leg.account_id
        and not _answered_otherwise(conn, credit.id)
        and out.leg.id in _candidates(conn, credit, linked)
    )
    if not valid:
        raise RuleViolationError(
            [TRANSFER_RETURN_INVALID],
            "the credit returns no such debit of its account (a return word in its purpose, "
            "the same amount within 30 days before), or no card offers this pair (the credit "
            "answered otherwise, or a line awaiting its duplicate question)",
        )
    for leg in (out, in_):
        transfers._rebook(ctx, leg, iban_key)
    # The pair booked once and withdrawn by re-booking one half (transfers._current_links)
    # counts again now that both halves are transfers: that same row is the link, and no
    # second one is written (as for a matcher's link).
    revived = transfers._link_of(conn, out.leg.id, in_.leg.id)
    if revived is not None:
        return revived
    # Booking a line as a transfer runs the transfer matcher (allocations.record_set's hook),
    # which links it when another account holds its counterpart: then it was a transfer after
    # all, and nothing of this answer is kept.
    linked = transfers._linked_ids(conn)
    if out.leg.id in linked or in_.leg.id in linked:
        raise ConflictError("a line of this returned payment was linked as a transfer")
    kind = transfer_link_kind(out.leg.account_kind, in_.leg.account_kind)
    return transfers._write(ctx, out.leg, in_.leg, kind, TransferMatchedBy.USER)


def returned_partners(
    conn: Connection, transaction_ids: Sequence[UUID]
) -> dict[UUID, ReturnedPartner]:
    """Konten › Umsätze: for each of `transaction_ids` that is one half of a booked returned
    payment (a current link of two lines of one account), the other half."""
    wanted = set(transaction_ids)
    if not wanted:
        return {}
    pairs = [
        (r.out_transaction_id, r.in_transaction_id)
        for r in transfers._current_links(conn)
        if r.out_transaction_id is not None
        and r.in_transaction_id is not None
        and (r.out_transaction_id in wanted or r.in_transaction_id in wanted)
    ]
    if not pairs:
        return {}
    t = transaction_table.c
    ids = {i for pair in pairs for i in pair}
    rows = {
        r.id: r
        for r in conn.execute(
            select(t.id, t.account_id, t.booking_date, t.amount).where(t.id.in_(ids))
        )
    }
    found: dict[UUID, ReturnedPartner] = {}
    for a, b in pairs:
        if a not in rows or b not in rows or rows[a].account_id != rows[b].account_id:
            continue
        for this, other in ((a, b), (b, a)):
            if this in wanted:
                found[this] = ReturnedPartner(
                    transaction_id=other,
                    booking_date=rows[other].booking_date,
                    amount=rows[other].amount,
                )
    return found


def count_pairs(conn: Connection) -> tuple[int, int]:
    """`hausbuch pair-returns`: how many „Rücküberweisung erkannt“ cards Zu prüfen shows now,
    and how many of them offer more than one debit."""
    pairs = _pairs(conn)
    return len(pairs), sum(1 for _, debits in pairs if len(debits) > 1)


__all__ = [
    "book_return",
    "count_pairs",
    "lines_on_cards",
    "open_returns",
    "return_inbox_items",
    "returned_partners",
]
