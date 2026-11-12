"""Transfers between the household's own accounts (docs/phases/phase-6.md D162, §6).

A leg is a bank line (transaction) or an exchange or depot EUR movement (asset_movement with a
transfer kind). rules.matching.match_transfer decides; this module reads the legs, writes the
links and computes the cards on read.

- link_leg: after a line is booked (services.allocations.record_set) its transfer is linked
  when exactly one counterpart fits; a leg of another account booked as a transfer and still
  unlinked is tried too, so the counterpart that arrives late, booked otherwise, still links
  it. relink(account_id) runs the same over an account's unlinked transfer legs (confirmed
  and proposed), for a late import.
- transfer_link is append-only: a wrong link is replaced by a new row and the old one names
  it in superseded_by (unlink). A link with no replacement cannot be undone: the table has no
  status, and migration 0025 is on main.
- open_transfers: computed on read, never written. A leg inside its window has no card; after
  it transfer.unmatched_leg; two fitting counterparts transfer.ambiguous; a counterpart that
  fits but was not linked (a movement, or a leg booked before the other) transfer.proposed.

Every link and every replacement writes its event (transfer.linked, transfer.unlinked). Reads
and writes run under the workspace's row-level security."""

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.rules.errors import TRANSFER_LEGS_INVALID
from hausbuch.rules.matching import (
    TRANSFER_CATEGORIES,
    legs_fit,
    match_transfer,
    transfer_link_kind,
    transfer_window_end,
)
from hausbuch.schemas.allocation import AllocationCategory, MatchContext
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.enums import AccountKind, AccountPurpose, AllocationStatus, ImportStatus
from hausbuch.schemas.events import EventType, TransferLinked, TransferUnlinked
from hausbuch.schemas.household import (
    MovementKind,
    MovementStatus,
    OpenTransfer,
    OpenTransferCode,
    TransferCandidate,
    TransferLeg,
    TransferLegSource,
    TransferLegView,
    TransferLink,
    TransferLinkCreate,
    TransferLinkKind,
    TransferLinkView,
    TransferMatchedBy,
)

from .context import CommandContext
from .errors import ConflictError, NotFoundError, RuleViolationError
from .events import append_event

# Legs read around a leg: wider than three banking days either side (a Friday's window ends
# on Wednesday), so no fitting counterpart is outside the read.
_AROUND = timedelta(days=7)
# An unmatched leg's card offers the same amount up to a month either side, for a link by hand.
_BY_HAND = timedelta(days=31)
_LIVE = (AllocationStatus.CONFIRMED.value, AllocationStatus.PROPOSED.value)
_MOVEMENT_KINDS = {
    MovementKind.INTERNAL_TRANSFER_IN.value: AllocationCategory.INTERNAL_TRANSFER_IN,
    MovementKind.INTERNAL_TRANSFER_OUT.value: AllocationCategory.INTERNAL_TRANSFER_OUT,
}


# --- Reading legs ---------------------------------------------------------------------------


class _Leg:
    """A leg with what the screens show of it (kept apart from the rule's TransferLeg)."""

    __slots__ = ("leg", "label", "name", "purpose", "booked")

    def __init__(
        self,
        leg: TransferLeg,
        label: str,
        name: str | None,
        purpose: AccountPurpose,
        booked: AllocationCategory | None,
    ) -> None:
        self.leg = leg
        self.label = label
        self.name = name
        self.purpose = purpose
        self.booked = booked  # the line's current transfer category, None when booked otherwise

    def view(self) -> TransferLegView:
        return TransferLegView(
            source=self.leg.source,
            id=self.leg.id,
            account_id=self.leg.account_id,
            account_label=self.label,
            booked_on=self.leg.booked_on,
            amount=self.leg.amount,
            counterparty_name=self.name,
        )


def _hex(value: bytes | None) -> str | None:
    return None if value is None else bytes(value).hex()


def _linked_ids(conn: Connection) -> set[UUID]:
    """Every leg of a current link (one not superseded)."""
    c = link_table.c
    ids: set[UUID] = set()
    for row in conn.execute(
        select(
            c.out_transaction_id, c.out_movement_id, c.in_transaction_id, c.in_movement_id
        ).where(c.superseded_by.is_(None))
    ):
        ids.update(i for i in row if i is not None)
    return ids


def _transfer_booked(conn: Connection, ids: Sequence[UUID]) -> dict[UUID, AllocationCategory]:
    """The lines among `ids` whose live rows are one transfer category."""
    if not ids:
        return {}
    a = allocation_table.c
    cats: dict[UUID, set[str]] = {}
    for tx_id, category in conn.execute(
        select(a.transaction_id, a.category).where(
            a.transaction_id.in_(ids), a.status.in_(_LIVE), a.amount != 0
        )
    ):
        cats.setdefault(tx_id, set()).add(category)
    return {
        tx_id: AllocationCategory(next(iter(found)))
        for tx_id, found in cats.items()
        if len(found) == 1 and AllocationCategory(next(iter(found))) in TRANSFER_CATEGORIES
    }


def _line_legs(conn: Connection, *where) -> list[_Leg]:  # noqa: ANN002
    t, acc = transaction_table.c, account_table.c
    rows = conn.execute(
        select(
            t.id,
            t.account_id,
            t.booking_date,
            t.amount,
            t.counterparty_name,
            t.counterparty_iban_hmac,
            acc.kind,
            acc.purpose,
            acc.label,
            acc.iban_hmac,
        )
        .join(account_table, acc.id == t.account_id)
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            t.excluded_as_duplicate_of.is_(None),
            t.amount != 0,
            import_table.c.status == ImportStatus.RECONCILED.value,
            *where,
        )
        .order_by(t.booking_date, t.id)
    ).all()
    booked = _transfer_booked(conn, [r.id for r in rows])
    return [
        _Leg(
            TransferLeg(
                source=TransferLegSource.TRANSACTION,
                id=r.id,
                account_id=r.account_id,
                account_kind=AccountKind(r.kind),
                account_iban_hmac=_hex(r.iban_hmac),
                booked_on=r.booking_date,
                amount=r.amount,
                counterparty_iban_hmac=_hex(r.counterparty_iban_hmac),
                category=booked.get(r.id),
            ),
            r.label,
            r.counterparty_name,
            AccountPurpose(r.purpose),
            booked.get(r.id),
        )
        for r in rows
    ]


def _movement_legs(conn: Connection, *where) -> list[_Leg]:  # noqa: ANN002
    m, acc = movement_table.c, account_table.c
    rows = conn.execute(
        select(
            m.id,
            m.account_id,
            m.kind,
            m.eur_amount,
            m.fee_eur,
            m.occurred_at,
            acc.kind.label("account_kind"),
            acc.purpose,
            acc.label,
        )
        .join(account_table, acc.id == m.account_id)
        .where(
            m.kind.in_(list(_MOVEMENT_KINDS)),
            m.eur_amount.is_not(None),
            m.eur_amount != 0,
            m.superseded_by.is_(None),
            m.status.in_([MovementStatus.PROPOSED.value, MovementStatus.CONFIRMED.value]),
            *where,
        )
        .order_by(m.occurred_at, m.id)
    ).all()
    legs = []
    for r in rows:
        category = _MOVEMENT_KINDS[r.kind]
        amount = abs(r.eur_amount)
        legs.append(
            _Leg(
                TransferLeg(
                    source=TransferLegSource.MOVEMENT,
                    id=r.id,
                    account_id=r.account_id,
                    account_kind=AccountKind(r.account_kind),
                    booked_on=r.occurred_at.date(),
                    amount=amount
                    if category is AllocationCategory.INTERNAL_TRANSFER_IN
                    else -amount,
                    fee_eur=r.fee_eur,
                    category=category,
                ),
                r.label,
                None,
                AccountPurpose(r.purpose),
                category,
            )
        )
    return legs


def _legs_between(conn: Connection, start: date, end: date) -> list[_Leg]:
    t, m = transaction_table.c, movement_table.c
    lines = _line_legs(conn, t.booking_date >= start, t.booking_date <= end)
    moves = _movement_legs(
        conn,
        func.date(m.occurred_at) >= start,
        func.date(m.occurred_at) <= end,
    )
    return lines + moves


def _one_leg(conn: Connection, source: TransferLegSource, leg_id: UUID) -> _Leg | None:
    if source is TransferLegSource.TRANSACTION:
        found = _line_legs(conn, transaction_table.c.id == leg_id)
    else:
        found = _movement_legs(conn, movement_table.c.id == leg_id)
    return found[0] if found else None


def _iban_hmacs(conn: Connection, *, except_account: UUID) -> list[str]:
    acc = account_table.c
    return sorted(
        bytes(h).hex()
        for h in conn.execute(
            select(acc.iban_hmac).where(acc.iban_hmac.is_not(None), acc.id != except_account)
        ).scalars()
    )


def _context(conn: Connection, leg: _Leg, others: Iterable[_Leg]) -> MatchContext:
    return MatchContext(
        account_purpose=leg.purpose,
        own_account_hmacs=_iban_hmacs(conn, except_account=leg.leg.account_id),
        open_legs=[o.leg for o in others],
    )


# --- Writing links --------------------------------------------------------------------------


def _lock(ctx: CommandContext) -> None:
    """One linker at a time per workspace, so two lines booked at once never both link the
    same counterpart (released at commit or rollback)."""
    key = func.hashtextextended(f"transfer_link:{ctx.workspace_id}", 0)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(key)))


def _columns(out: TransferLeg, in_: TransferLeg) -> dict[str, UUID]:
    out_col = (
        "out_transaction_id" if out.source is TransferLegSource.TRANSACTION else "out_movement_id"
    )
    in_col = (
        "in_transaction_id" if in_.source is TransferLegSource.TRANSACTION else "in_movement_id"
    )
    return {out_col: out.id, in_col: in_.id}


def _write(
    ctx: CommandContext,
    out: TransferLeg,
    in_: TransferLeg,
    kind: TransferLinkKind,
    matched_by: TransferMatchedBy,
) -> TransferLink:
    legs = _columns(out, in_)
    row = ctx.conn.execute(
        insert(link_table)
        .values(
            id=uuid4(),
            workspace_id=ctx.workspace_id,
            kind=kind.value,
            matched_by=matched_by.value,
            created_at=ctx.now,
            **legs,
        )
        .returning(*link_table.c)
    ).one()
    link = TransferLink.model_validate(row._asdict())
    append_event(
        ctx,
        EventType.TRANSFER_LINKED,
        link.id,
        TransferLinked(kind=kind, matched_by=matched_by, **legs),
    )
    return link


def _both_ways(
    conn: Connection, leg: _Leg, pair: TransferCandidate, linked: set[UUID], today: date
) -> bool:
    """D162 from the counterpart's side too: it must see this leg as its only fit, so one of
    two equal legs is never linked while the other's card asks which (transfer.ambiguous)."""
    other_id = pair.in_leg.id if pair.out_leg.id == leg.leg.id else pair.out_leg.id
    near = _legs_between(conn, leg.leg.booked_on - 2 * _AROUND, leg.leg.booked_on + 2 * _AROUND)
    counter = next((o for o in near if o.leg.id == other_id), None)
    if counter is None:
        return False
    rest = [o for o in near if o.leg.id not in linked and o.leg.id != other_id]
    back = match_transfer(counter.leg, _context(conn, counter, rest), today=today)
    if back.category is not None:
        return back.link is not None and leg.leg.id in (back.link.out_leg.id, back.link.in_leg.id)
    fits = [
        o.leg.id
        for o in rest
        if o.leg.account_id != counter.leg.account_id
        and (o.leg.amount > 0) != (counter.leg.amount > 0)
        and legs_fit(*_out_in(counter.leg, o.leg))
    ]
    return fits == [leg.leg.id]


def _out_in(a: TransferLeg, b: TransferLeg) -> tuple[TransferLeg, TransferLeg]:
    return (a, b) if a.amount < 0 else (b, a)


def link_leg(
    ctx: CommandContext, leg_id: UUID, source: TransferLegSource = TransferLegSource.TRANSACTION
) -> TransferLink | None:
    """Link one leg when exactly one counterpart fits (D162), matched_by matcher; None when
    it is linked already, is no transfer, or has none or several counterparts."""
    _lock(ctx)
    leg = _one_leg(ctx.conn, source, leg_id)
    if leg is None:
        return None
    linked = _linked_ids(ctx.conn)
    if leg.leg.id in linked:
        return None
    around = _legs_between(ctx.conn, leg.leg.booked_on - _AROUND, leg.leg.booked_on + _AROUND)
    others = [o for o in around if o.leg.id not in linked and o.leg.id != leg.leg.id]
    found = match_transfer(leg.leg, _context(ctx.conn, leg, others), today=ctx.now.date())
    if found.link is None or not _both_ways(ctx.conn, leg, found.link, linked, ctx.now.date()):
        return None
    return _write(
        ctx, found.link.out_leg, found.link.in_leg, found.link.kind, TransferMatchedBy.MATCHER
    )


def after_booking(
    ctx: CommandContext, transaction_id: UUID, categories: set[AllocationCategory]
) -> None:
    """services.allocations.record_set's hook: a line booked as a transfer is linked; any
    line booked may also be the late counterpart of another account's unlinked transfer leg
    near its date, which is tried then."""
    if categories & TRANSFER_CATEGORIES and link_leg(ctx, transaction_id) is not None:
        return
    leg = _one_leg(ctx.conn, TransferLegSource.TRANSACTION, transaction_id)
    if leg is None:
        return
    linked = _linked_ids(ctx.conn)
    if transaction_id in linked:
        return
    around = _legs_between(ctx.conn, leg.leg.booked_on - _AROUND, leg.leg.booked_on + _AROUND)
    for other in around:
        if other.leg.account_id == leg.leg.account_id or other.booked is None:
            continue
        if other.leg.id in linked:
            continue
        if link_leg(ctx, other.leg.id, other.leg.source) is not None:
            return


def relink(ctx: CommandContext, account_id: UUID) -> int:
    """D162 after an import: every unlinked transfer leg of the account (confirmed or
    proposed) is linked when one counterpart fits now; the number linked."""
    legs = _line_legs(ctx.conn, transaction_table.c.account_id == account_id)
    legs += _movement_legs(ctx.conn, movement_table.c.account_id == account_id)
    linked = _linked_ids(ctx.conn)
    count = 0
    for leg in legs:
        if leg.booked is None or leg.leg.id in linked:
            continue
        if link_leg(ctx, leg.leg.id, leg.leg.source) is not None:
            count += 1
            linked = _linked_ids(ctx.conn)
    return count


def _pair(conn: Connection, body: TransferLinkCreate) -> tuple[_Leg, _Leg]:
    out_src, out_id = (
        (TransferLegSource.TRANSACTION, body.out_transaction_id)
        if body.out_transaction_id is not None
        else (TransferLegSource.MOVEMENT, body.out_movement_id)
    )
    in_src, in_id = (
        (TransferLegSource.TRANSACTION, body.in_transaction_id)
        if body.in_transaction_id is not None
        else (TransferLegSource.MOVEMENT, body.in_movement_id)
    )
    assert out_id is not None and in_id is not None  # TransferLinkCreate checks one of each
    out = _one_leg(conn, out_src, out_id)
    in_ = _one_leg(conn, in_src, in_id)
    if out is None:
        raise NotFoundError(out_src.value, out_id)
    if in_ is None:
        raise NotFoundError(in_src.value, in_id)
    valid = (
        out.leg.amount < 0 < in_.leg.amount
        and out.leg.account_id != in_.leg.account_id
        and transfer_link_kind(out.leg.account_kind, in_.leg.account_kind) is body.kind
    )
    if not valid:
        raise RuleViolationError(
            [TRANSFER_LEGS_INVALID], "a link joins an out leg and an in leg of two own accounts"
        )
    return out, in_


def link_by_hand(ctx: CommandContext, body: TransferLinkCreate) -> TransferLink:
    """POST /transfers: the user links two legs (matched_by user). 409 when either leg is
    linked already; 422 transfer.legs_invalid for two legs that are no transfer."""
    _lock(ctx)
    out, in_ = _pair(ctx.conn, body)
    linked = _linked_ids(ctx.conn)
    if out.leg.id in linked or in_.leg.id in linked:
        raise ConflictError("a leg of this transfer is linked already")
    return _write(ctx, out.leg, in_.leg, body.kind, TransferMatchedBy.USER)


def unlink(ctx: CommandContext, link_id: UUID, replacement: TransferLinkCreate) -> TransferLink:
    """POST /transfers/{id}/unlink: the wrong link is replaced by the one the user picks; the
    old row names the new one in superseded_by (append-only), events transfer.unlinked and
    transfer.linked. 404 for an unknown link; 409 when it is superseded already or a leg of
    the replacement belongs to another current link."""
    _lock(ctx)
    c = link_table.c
    old = ctx.conn.execute(select(link_table).where(c.id == link_id)).one_or_none()
    if old is None:
        raise NotFoundError("transfer", link_id)
    if old.superseded_by is not None:
        raise ConflictError("this link was replaced already")
    out, in_ = _pair(ctx.conn, replacement)
    freed = {
        i
        for i in (
            old.out_transaction_id,
            old.out_movement_id,
            old.in_transaction_id,
            old.in_movement_id,
        )
        if i is not None
    }
    linked = _linked_ids(ctx.conn) - freed
    if out.leg.id in linked or in_.leg.id in linked:
        raise ConflictError("a leg of the replacement is linked already")
    new = _write(ctx, out.leg, in_.leg, replacement.kind, TransferMatchedBy.USER)
    ctx.conn.execute(update(link_table).where(c.id == link_id).values(superseded_by=new.id))
    append_event(ctx, EventType.TRANSFER_UNLINKED, link_id, TransferUnlinked(superseded_by=new.id))
    return new


# --- Reading links and cards ----------------------------------------------------------------


def list_links(conn: Connection, *, start: date, end: date) -> list[TransferLinkView]:
    """The current links with a leg booked between `start` and `end`, by the out leg's date."""
    c = link_table.c
    rows = conn.execute(select(link_table).where(c.superseded_by.is_(None))).all()
    legs = {leg.leg.id: leg for leg in _legs_between(conn, start - _AROUND, end + _AROUND)}
    out = []
    for r in rows:
        a = legs.get(r.out_transaction_id or r.out_movement_id)
        b = legs.get(r.in_transaction_id or r.in_movement_id)
        if a is None or b is None:
            continue
        if not (start <= a.leg.booked_on <= end or start <= b.leg.booked_on <= end):
            continue
        out.append(
            TransferLinkView(
                id=r.id,
                kind=TransferLinkKind(r.kind),
                matched_by=TransferMatchedBy(r.matched_by),
                created_at=r.created_at,
                out_leg=a.view(),
                in_leg=b.view(),
            )
        )
    return sorted(out, key=lambda v: (v.out_leg.booked_on, str(v.id)))


def open_transfers(conn: Connection, today: date) -> list[OpenTransfer]:
    """Every transfer leg that waits for a person, computed now (D162): never a leg inside
    its window without a counterpart."""
    linked = _linked_ids(conn)
    waiting = [
        leg
        for leg in _line_legs(conn) + _movement_legs(conn)
        if leg.booked is not None and leg.leg.id not in linked
    ]
    items = []
    for leg in waiting:
        start, end = leg.leg.booked_on - _BY_HAND, leg.leg.booked_on + _BY_HAND
        others = [
            o
            for o in _legs_between(conn, start, end)
            if o.leg.id not in linked and o.leg.account_id != leg.leg.account_id
        ]
        near = [o for o in others if abs(o.leg.booked_on - leg.leg.booked_on) <= _AROUND]
        found = match_transfer(leg.leg, _context(conn, leg, near), today=today)
        by_id = {o.leg.id: o for o in others}
        if found.link is not None and not _both_ways(conn, leg, found.link, linked, today):
            continue  # one of several equal legs: the counterpart's card asks which
        if found.link is not None or found.proposed_link is not None:
            pair = found.link or found.proposed_link
            other = pair.in_leg if pair.out_leg.id == leg.leg.id else pair.out_leg
            code, candidates = OpenTransferCode.PROPOSED, [by_id[other.id]]
        elif found.card is not None and found.candidates:
            code, candidates = OpenTransferCode.AMBIGUOUS, [by_id[i] for i in found.candidates]
        elif found.card is not None:
            named = leg.leg.counterparty_iban_hmac
            same = [
                o
                for o in others
                if o.leg.amount == -leg.leg.amount
                and (named is None or o.leg.account_iban_hmac in (None, named))
            ]
            code, candidates = OpenTransferCode.UNMATCHED_LEG, same
        else:
            continue
        items.append(
            OpenTransfer(
                code=code,
                leg=leg.view(),
                candidates=[c.view() for c in candidates],
                window_end=transfer_window_end(leg.leg.booked_on),
            )
        )
    return items


_LINK_REASON = {
    OpenTransferCode.AMBIGUOUS: (
        "Für diese Überweisung zwischen zwei eigenen Konten passen mehrere Gegenbuchungen."
    ),
    OpenTransferCode.PROPOSED: (
        "Diese Buchung passt genau zu einer Buchung auf einem anderen eigenen Konto."
    ),
}
_UNMATCHED_REASON = (
    "Zu dieser Überweisung zwischen zwei eigenen Konten fehlt nach drei Bankarbeitstagen die "
    "Gegenbuchung auf dem anderen Konto."
)


def transfer_inbox_items(conn: Connection, today: date) -> list[InboxItem]:
    """Zu prüfen's two transfer cards (D162): transfer_link (pick the counterpart) and
    transfer_unmatched (past the window; link one by hand, or import the other account)."""
    items = []
    for item in open_transfers(conn, today):
        unmatched = item.code is OpenTransferCode.UNMATCHED_LEG
        items.append(
            InboxItem(
                kind=InboxItemKind.TRANSFER_UNMATCHED if unmatched else InboxItemKind.TRANSFER_LINK,
                created_at=datetime.combine(item.window_end, time(), tzinfo=UTC),
                reasoning=_UNMATCHED_REASON if unmatched else _LINK_REASON[item.code],
                question=(
                    "Welche Buchung ist die Gegenbuchung?"
                    if item.candidates
                    else "Ist das Konto der Gegenbuchung schon importiert?"
                ),
                transaction_id=item.leg.id
                if item.leg.source is TransferLegSource.TRANSACTION
                else None,
                account_id=item.leg.account_id,
                transfer=item,
            )
        )
    return items


def saved_in(conn: Connection, *, start: date, end: date) -> Decimal:
    """D162 for the Haushalt screen: what the current links moved into a savings, depot or
    exchange account from a current account, with the in-leg booked between the dates."""
    total = Decimal("0.00")
    for view in list_links(conn, start=start, end=end):
        if not start <= view.in_leg.booked_on <= end:
            continue
        kinds = _kinds(conn, [view.out_leg.account_id, view.in_leg.account_id])
        if (
            kinds.get(view.in_leg.account_id) in _SAVING
            and kinds.get(view.out_leg.account_id) not in _SAVING
        ):
            total += view.in_leg.amount
    return total


_SAVING = (AccountKind.BANK_SAVINGS, AccountKind.DEPOT, AccountKind.EXCHANGE)


def _kinds(conn: Connection, ids: Sequence[UUID]) -> dict[UUID, AccountKind]:
    acc = account_table.c
    return {
        r.id: AccountKind(r.kind)
        for r in conn.execute(select(acc.id, acc.kind).where(acc.id.in_(ids)))
    }


__all__ = [
    "after_booking",
    "link_by_hand",
    "link_leg",
    "list_links",
    "open_transfers",
    "relink",
    "saved_in",
    "transfer_inbox_items",
    "unlink",
]
