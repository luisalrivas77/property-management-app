"""Transfers between the household's own accounts (docs/phases/phase-6.md D162, §6).

A leg is a bank line (transaction) or an exchange or depot EUR movement (asset_movement with a
transfer kind). rules.matching.match_transfer decides; this module reads the legs, writes the
links and computes the cards on read.

- link_leg: after a line is booked (services.allocations.record_set) its transfer is linked
  when exactly one counterpart fits and both are booked as transfers; a leg of another
  account booked as a transfer and still unlinked is tried too, so the counterpart that
  arrives late links it. A counterpart booked otherwise is never linked by the matcher: the
  leg's card proposes it for a link by hand (review F3). One exception (D162 amended
  15 Nov 2026): when the leg names the counterpart's account by IBAN and the counterpart is
  a private bank or depot line of the same amount with no confirmed answer (a depot's cash
  line carries no IBAN, so only the model guessed it), the matcher books the counterpart as
  the transfer's leg and links the pair (_book_named_leg); a confirmed answer is never
  replaced. relink(account_id) runs the same over an account's unlinked transfer legs
  (confirmed and proposed) and the other accounts' transfer lines that name it, for a late
  import (a depot's cash statement calls it, services.statements.import_statement);
  relink_all over every account (`hausbuch relink-transfers`). D162 amended (6): after every
  link the matcher writes, a line of a pair tied by an own IBAN that holds only the model's
  proposal of that transfer is booked as its leg (_confirm_tied_legs); confirm_linked does it
  for the links written before (`hausbuch relink-transfers` too).
- transfer_link is append-only: a wrong link is replaced by a new row and the old one names
  it in superseded_by (unlink). The table has no status (migration 0025 is on main), so a
  link the matcher wrote is current only while both its legs are booked as transfers
  (_current_links, review F3): re-booking or rejecting a leg withdraws it, and booking it as
  a transfer again brings the same row back. D162 amended (7): a link of two lines of one
  account is a returned payment (services.returns.book_return); it counts the same way, and
  list_links never lists it.
- open_transfers: computed on read, never written. A leg inside its window has no card; after
  it transfer.unmatched_leg; two fitting counterparts transfer.ambiguous; a counterpart that
  fits but was not linked (a movement, or a leg booked before the other) transfer.proposed.
  A candidate booked otherwise but tied to the leg by an own IBAN (a card bill answered
  hh_credit_card before the card account existed) is marked rebook_on_link: the card's
  button re-books it as a transfer, then links (link_by_hand with rebook, D176 (4)).
- lines_on_cards: the candidates Zu prüfen shows on their card only (D162 amended (5)): the
  card's button books them, and they hold only the model's proposal.

Every link and every replacement writes its event (transfer.linked, transfer.unlinked). Reads
and writes run under the workspace's row-level security."""

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Connection, Row, Select, and_, func, insert, or_, select, update

from hausbuch import i18n
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.rules.errors import TRANSFER_LEGS_INVALID
from hausbuch.rules.matching import (
    EXCHANGE_DEPOSIT_GAP,
    TRANSFER_CATEGORIES,
    TRANSFER_CODES,
    deposit_fee_text,
    deposit_gap,
    deposit_iban_link_text,
    deposit_iban_offer_text,
    deposit_link_text,
    deposit_split_set,
    ibans_agree,
    legs_fit,
    match_transfer,
    named_transfer_set,
    names_the_other,
    transfer_link_kind,
    transfer_window_end,
)
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
)
from hausbuch.schemas.api import (
    ConfirmAllocationsBody,
    DepositIbanOffer,
    InboxItem,
    InboxItemKind,
)
from hausbuch.schemas.bank_sync import SyncStatus
from hausbuch.schemas.enums import (
    AccountKind,
    AccountPurpose,
    ActorType,
    AllocationStatus,
    ImportStatus,
)
from hausbuch.schemas.events import EventType, TransferLinked, TransferUnlinked
from hausbuch.schemas.household import (
    DepositIbanOutcome,
    MovementKind,
    MovementStatus,
    OpenTransfer,
    OpenTransferCode,
    OwnWalletDepositCreate,
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

    __slots__ = ("leg", "label", "name", "purpose", "booked", "category")

    def __init__(
        self,
        leg: TransferLeg,
        label: str,
        name: str | None,
        purpose: AccountPurpose,
        booked: AllocationCategory | None,
        category: AllocationCategory | None = None,
    ) -> None:
        self.leg = leg
        self.label = label
        self.name = name
        self.purpose = purpose
        self.booked = booked  # the line's current transfer category, None when booked otherwise
        self.category = booked or category  # its one live category of any kind, None for a split

    def view(
        self,
        *,
        rebook_on_link: bool = False,
        deposit_fee: Decimal | None = None,
        deposit_text: str | None = None,
    ) -> TransferLegView:
        return TransferLegView(
            source=self.leg.source,
            id=self.leg.id,
            account_id=self.leg.account_id,
            account_label=self.label,
            booked_on=self.leg.booked_on,
            amount=self.leg.amount,
            counterparty_name=self.name,
            account_kind=self.leg.account_kind,
            booked_category=self.category,
            booked_as_transfer=self.booked is not None,
            rebook_on_link=rebook_on_link,
            deposit_fee=deposit_fee,
            deposit_fee_text=(
                deposit_text
                if deposit_text is not None or deposit_fee is None
                else deposit_fee_text(deposit_fee)
            ),
        )


def _hex(value: bytes | None) -> str | None:
    return None if value is None else bytes(value).hex()


def _leg_ids(row: Row) -> tuple[UUID, ...]:
    return tuple(
        i
        for i in (
            row.out_transaction_id,
            row.out_movement_id,
            row.in_transaction_id,
            row.in_movement_id,
        )
        if i is not None
    )


_COIN_LINK_KINDS = frozenset(
    {TransferLinkKind.CHAIN.value, TransferLinkKind.BRIDGE.value, TransferLinkKind.WRAP.value}
)


def _current_links(conn: Connection) -> list[Row]:
    """The current links (review F3, no migration): not superseded, and, for a link the
    matcher wrote, both legs still booked as a transfer: a line whose live (confirmed or
    proposed) rows are one transfer category (_transfer_booked), a movement live and not
    superseded. So a person who
    re-books or rejects a leg withdraws the matcher's link by that booking, which is its event;
    booked as a transfer again, the same row counts again and no second link is written. A
    link the person made stays current until it is replaced (unlink): its legs need not be
    booked as transfers. A leg in two such links keeps the newer one.

    D162 amended (7), 15 Nov 2026: a returned payment (two lines of one account, written by
    services.returns.book_return) counts like a matcher's link, only while both lines are
    booked as transfers: a person who re-books one of them withdraws the pair, and the
    „Rücküberweisung erkannt“ card offers it again, so no half of it stays silently linked."""
    c = link_table.c
    rows = conn.execute(
        select(link_table)
        .where(c.superseded_by.is_(None))
        .order_by(c.created_at.desc(), c.id.desc())
    ).all()
    returns = _return_link_ids(conn, rows)
    # Phase 7 step 5 (D184 (5)): a chain, bridge or wrap link counts only while both coin legs
    # are live, whoever wrote it, so re-booking a leg ends a person's link too.
    returns = returns | {r.id for r in rows if r.kind in _COIN_LINK_KINDS}
    matcher = [
        r for r in rows if r.matched_by == TransferMatchedBy.MATCHER.value or r.id in returns
    ]
    tx_ids = {i for r in matcher for i in (r.out_transaction_id, r.in_transaction_id) if i}
    mv_ids = {i for r in matcher for i in (r.out_movement_id, r.in_movement_id) if i}
    booked = set(_transfer_booked(conn, sorted(tx_ids)))
    if mv_ids:
        m = movement_table.c
        booked |= set(
            conn.execute(
                select(m.id).where(
                    m.id.in_(mv_ids),
                    m.superseded_by.is_(None),
                    m.status.in_([MovementStatus.PROPOSED.value, MovementStatus.CONFIRMED.value]),
                )
            ).scalars()
        )
    current, used = [], set()
    for r in rows:
        legs = _leg_ids(r)
        checked = r.matched_by == TransferMatchedBy.MATCHER.value or r.id in returns
        if checked and not all(i in booked for i in legs):
            continue
        if used.intersection(legs):
            continue
        used.update(legs)
        current.append(r)
    return current


def matcher_linked_ids(conn: Connection) -> set[UUID]:
    """Every leg of a current link the matcher wrote (_current_links: its legs still booked
    as a transfer). D160 amended (5): such a line is no earlier answer for a name rule
    (services.matching.name_lines)."""
    return {
        i
        for r in _current_links(conn)
        if r.matched_by == TransferMatchedBy.MATCHER.value
        for i in _leg_ids(r)
    }


def _return_link_ids(conn: Connection, rows: Sequence[Row]) -> set[UUID]:
    """D162 amended (7): the links among `rows` whose two legs are bank lines of one account,
    a returned payment and the debit it returns (services.returns)."""
    pairs = [r for r in rows if r.out_transaction_id and r.in_transaction_id]
    if not pairs:
        return set()
    t = transaction_table.c
    ids = {i for r in pairs for i in (r.out_transaction_id, r.in_transaction_id)}
    account = dict(conn.execute(select(t.id, t.account_id).where(t.id.in_(ids))).tuples().all())
    return {
        r.id for r in pairs if account.get(r.out_transaction_id) == account.get(r.in_transaction_id)
    }


def is_return_link(out_leg: TransferLegView, in_leg: TransferLegView) -> bool:
    """D162 amended (7): a link whose two legs are bank lines of one account is a returned
    payment, not a transfer between two accounts."""
    return (
        out_leg.source is TransferLegSource.TRANSACTION
        and in_leg.source is TransferLegSource.TRANSACTION
        and out_leg.account_id == in_leg.account_id
    )


def _linked_ids(conn: Connection) -> set[UUID]:
    """Every leg of a current link (_current_links)."""
    return {i for r in _current_links(conn) for i in _leg_ids(r)}


# D162 amended 15 Nov 2026: a deposit an exchange credited short without a fee row is booked
# on the bank line as these two parts (rules.matching.deposit_split_set); the line is still the
# transfer's out leg.
_DEPOSIT_SPLIT = frozenset(
    {AllocationCategory.INTERNAL_TRANSFER_OUT.value, AllocationCategory.BANK_FEES.value}
)


def _one_category(conn: Connection, ids: Sequence[UUID]) -> dict[UUID, AllocationCategory]:
    """The lines among `ids` whose live rows are one category, with that category. D162
    amended 15 Nov 2026: a line whose live rows are exactly internal_transfer_out and bank_fees,
    the fee more than 0 and at most EXCHANGE_DEPOSIT_GAP, counts as internal_transfer_out (a
    deposit the exchange credited short, _split_and_link), and nothing else of two parts."""
    if not ids:
        return {}
    a = allocation_table.c
    cats: dict[UUID, set[str]] = {}
    fees: dict[UUID, Decimal] = {}
    for tx_id, category, amount in conn.execute(
        select(a.transaction_id, a.category, a.amount).where(
            a.transaction_id.in_(ids), a.status.in_(_LIVE), a.amount != 0
        )
    ):
        cats.setdefault(tx_id, set()).add(category)
        if category == AllocationCategory.BANK_FEES.value:
            fees[tx_id] = fees.get(tx_id, Decimal("0")) + amount
    found_one: dict[UUID, AllocationCategory] = {}
    for tx_id, found in cats.items():
        if len(found) == 1:
            found_one[tx_id] = AllocationCategory(next(iter(found)))
        elif found == _DEPOSIT_SPLIT and Decimal("0") < -fees[tx_id] <= EXCHANGE_DEPOSIT_GAP:
            found_one[tx_id] = AllocationCategory.INTERNAL_TRANSFER_OUT
    return found_one


def _transfer_booked(conn: Connection, ids: Sequence[UUID]) -> dict[UUID, AllocationCategory]:
    """The lines among `ids` whose live rows are one transfer category."""
    return {
        tx_id: category
        for tx_id, category in _one_category(conn, ids).items()
        if category in TRANSFER_CATEGORIES
    }


def _with_transfer_row() -> Select:
    """The lines with a live, non-zero row of a transfer category: the only lines whose leg
    can be booked as a transfer (_one_category, _transfer_booked), so open_transfers reads
    those instead of every line and its rows (14 Nov 2026: most of GET /inbox's time)."""
    a = allocation_table.c
    return select(a.transaction_id).where(
        a.status.in_(_LIVE),
        a.amount != 0,
        a.category.in_([c.value for c in TRANSFER_CATEGORIES]),
    )


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
            acc.payment_iban_hmac,
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
    categories = _one_category(conn, [r.id for r in rows])
    booked = {i: c for i, c in categories.items() if c in TRANSFER_CATEGORIES}
    return [
        _Leg(
            TransferLeg(
                source=TransferLegSource.TRANSACTION,
                id=r.id,
                account_id=r.account_id,
                account_kind=AccountKind(r.kind),
                # D176 (4): a card account is paid to its confirmed payment IBAN, which
                # stands for its account IBAN here, so the paying debit names it (ibans_agree,
                # match_transfer's counterpart filter).
                account_iban_hmac=_hex(r.iban_hmac or r.payment_iban_hmac),
                booked_on=r.booking_date,
                amount=r.amount,
                counterparty_iban_hmac=_hex(r.counterparty_iban_hmac),
                category=booked.get(r.id),
                account_purpose=AccountPurpose(r.purpose),
            ),
            r.label,
            r.counterparty_name,
            AccountPurpose(r.purpose),
            booked.get(r.id),
            categories.get(r.id),
        )
        for r in rows
    ]


def _window_lines(
    conn: Connection, start: date, end: date, with_transfer_row: Sequence[_Leg]
) -> list[_Leg]:
    """What _line_legs(conn, booking_date between start and end) returns, when
    `with_transfer_row` is already _line_legs(conn, id in _with_transfer_row()): those of the
    span taken from it, the span's other lines read, merged in the read's order (booking date,
    then id; Python orders a UUID by its 16 bytes as Postgres does). A leg's category reads its
    own line's rows only (_one_category), so it is the same from either read (phase 10 §7.3 (4):
    open_transfers read these lines twice)."""
    t, a = transaction_table.c, allocation_table.c
    known = [leg for leg in with_transfer_row if start <= leg.leg.booked_on <= end]
    has_row = _with_transfer_row().where(a.transaction_id == t.id).exists()
    rest = _line_legs(conn, t.booking_date >= start, t.booking_date <= end, ~has_row)
    return sorted([*known, *rest], key=lambda leg: (leg.leg.booked_on, leg.leg.id))


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
            acc.iban_hmac,
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
        kind = AccountKind(r.account_kind)
        deposit = category is AllocationCategory.INTERNAL_TRANSFER_IN
        legs.append(
            _Leg(
                TransferLeg(
                    source=TransferLegSource.MOVEMENT,
                    id=r.id,
                    account_id=r.account_id,
                    account_kind=kind,
                    booked_on=r.occurred_at.date(),
                    amount=amount if deposit else -amount,
                    fee_eur=r.fee_eur,
                    category=category,
                    # D162 amended 15 Nov 2026: an exchange's IBAN is the one its deposits are
                    # paid to (learned from the first link by hand), so only a deposit carries
                    # it; a withdrawal comes from another IBAN and stays untied.
                    account_iban_hmac=_hex(r.iban_hmac)
                    if deposit and kind is AccountKind.EXCHANGE
                    else None,
                    account_purpose=AccountPurpose(r.purpose),
                ),
                r.label,
                None,
                AccountPurpose(r.purpose),
                category,
            )
        )
    return legs


def _legs_between(
    conn: Connection, start: date, end: date, *, transfer_booked_only: bool = False
) -> list[_Leg]:
    """The legs booked between `start` and `end`; with `transfer_booked_only`, a line only when
    it has a transfer row (_with_transfer_row), for a caller that skips every other line."""
    t, m = transaction_table.c, movement_table.c
    only = [t.id.in_(_with_transfer_row())] if transfer_booked_only else []
    lines = _line_legs(conn, t.booking_date >= start, t.booking_date <= end, *only)
    moves = _movement_legs(
        conn,
        func.date(m.occurred_at) >= start,
        func.date(m.occurred_at) <= end,
    )
    return lines + moves


class _Window:
    """Every leg booked between two dates, read once, so open_transfers can take each card's
    window from memory instead of reading it again per card (14 Nov 2026: 38 cards, each
    reading two months of every account twice, were 1.6–1.9 s of GET /inbox's 2 s).
    between() returns what _legs_between would for a sub-window: both reads are ordered by a
    unique key (booking date and id, occurred_at and id), so a date filter keeps that order,
    and a movement's booked_on is its occurred_at date as the database's func.date gives it
    (the driver returns timestamptz in the session's time zone). It also keeps own_account_
    hmacs per account, which _context asks for once per leg, all taken from one read of the
    accounts' IBANs (_account_ibans, 21 Nov 2026: one query per account before), and that
    read's _own_accounts and _own_account_sets for the cards without a counterpart.

    `moves`, when given, are every live transfer movement (_movement_legs with no filter):
    the window keeps those booked between the dates, in their order, instead of reading them
    again (the same booked_on equality as between()). `lines`, when given with them, are every
    line with a transfer row (_line_legs over _with_transfer_row), which the window takes from
    memory and reads only the span's other lines (_window_lines, phase 10 §7.3 (4))."""

    def __init__(
        self,
        conn: Connection,
        start: date,
        end: date,
        moves: Sequence[_Leg] | None = None,
        lines: Sequence[_Leg] | None = None,
    ) -> None:
        if moves is None:
            legs = _legs_between(conn, start, end)
        else:
            t = transaction_table.c
            read = (
                _line_legs(conn, t.booking_date >= start, t.booking_date <= end)
                if lines is None
                else _window_lines(conn, start, end, lines)
            )
            legs = read + [leg for leg in moves if start <= leg.leg.booked_on <= end]
        self.lines = [leg for leg in legs if leg.leg.source is TransferLegSource.TRANSACTION]
        self.moves = [leg for leg in legs if leg.leg.source is not TransferLegSource.TRANSACTION]
        self.start, self.end = start, end
        self._hmacs: dict[UUID, list[str]] = {}
        self._accounts: list[Row] | None = None

    def between(self, start: date, end: date) -> list[_Leg]:
        assert self.start <= start and end <= self.end, "window outside the read span"
        return [leg for leg in self.lines if start <= leg.leg.booked_on <= end] + [
            leg for leg in self.moves if start <= leg.leg.booked_on <= end
        ]

    def accounts(self, conn: Connection) -> list[Row]:
        if self._accounts is None:
            self._accounts = _account_ibans(conn)
        return self._accounts

    def hmacs(self, conn: Connection, account_id: UUID) -> list[str]:
        if account_id not in self._hmacs:
            self._hmacs[account_id] = _hmacs_of(self.accounts(conn), except_account=account_id)
        return self._hmacs[account_id]


def _one_leg(conn: Connection, source: TransferLegSource, leg_id: UUID) -> _Leg | None:
    if source is TransferLegSource.TRANSACTION:
        found = _line_legs(conn, transaction_table.c.id == leg_id)
    else:
        found = _movement_legs(conn, movement_table.c.id == leg_id)
    return found[0] if found else None


def own_account_hmacs(conn: Connection, *, except_account: UUID | None = None) -> list[str]:
    """D162 with D176 (4): the IBAN of every own account and the confirmed payment IBAN of every
    card account, hex, except those of `except_account`, without repeats."""
    acc = account_table.c
    query = select(func.coalesce(acc.iban_hmac, acc.payment_iban_hmac)).where(
        or_(acc.iban_hmac.is_not(None), acc.payment_iban_hmac.is_not(None))
    )
    if except_account is not None:
        query = query.where(acc.id != except_account)
    return sorted({bytes(h).hex() for h in conn.execute(query).scalars()})


def _account_ibans(conn: Connection) -> list[Row]:
    """Every account's id, IBAN and payment IBAN (HMACs), read once so own_account_hmacs per
    account (_hmacs_of), _own_accounts and _own_account_sets need no read of their own."""
    acc = account_table.c
    return conn.execute(select(acc.id, acc.iban_hmac, acc.payment_iban_hmac)).all()


def _hmacs_of(rows: Sequence[Row], *, except_account: UUID) -> list[str]:
    """own_account_hmacs(conn, except_account=...) from _account_ibans' rows: each account's
    IBAN, else its payment IBAN, hex, except `except_account`'s, without repeats, sorted."""
    return sorted(
        {
            bytes(iban if iban is not None else payment).hex()
            for account_id, iban, payment in rows
            if account_id != except_account and (iban is not None or payment is not None)
        }
    )


def _context(
    conn: Connection, leg: _Leg, others: Iterable[_Leg], window: _Window | None = None
) -> MatchContext:
    return MatchContext(
        account_purpose=leg.purpose,
        own_account_hmacs=(
            window.hmacs(conn, leg.leg.account_id)
            if window is not None
            else own_account_hmacs(conn, except_account=leg.leg.account_id)
        ),
        open_legs=[o.leg for o in others],
    )


# --- Writing links --------------------------------------------------------------------------


def _lock(ctx: CommandContext) -> None:
    """One linker at a time per workspace, so two lines booked at once never both link the
    same counterpart (released at commit or rollback).

    Lock order (D162 amended (8), fixed 16 Nov 2026): an exchange account's row is always
    taken before this lock, never after it. An exchange import locks its account's row and
    then takes this lock in its relink, so a command that may learn an exchange's deposit IBAN
    (accounts.learn_exchange_deposit_iban locks that row) takes the row first too:
    link_by_hand through _lock_exchanges, a person's answer with the deposit box ticked through
    lock_deposit_exchange."""
    key = func.hashtextextended(f"transfer_link:{ctx.workspace_id}", 0)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(key)))


def _try_lock(ctx: CommandContext) -> bool:
    """_lock without waiting (phase 7 step 5): whether the lock is now held. For a caller
    that holds a movement row and so may not wait for the lock (its order)."""
    key = func.hashtextextended(f"transfer_link:{ctx.workspace_id}", 0)
    return bool(ctx.conn.execute(select(func.pg_try_advisory_xact_lock(key))).scalar_one())


def _lock_exchanges(ctx: CommandContext, movement_ids: Iterable[UUID | None]) -> None:
    """The rows of the exchange accounts the movements `movement_ids` belong to, locked in id
    order before the transfer lock (_lock's lock order): a link by hand of an exchange's
    deposit may learn its IBAN, which locks that row, while an exchange import holds it and
    waits for the transfer lock in its relink. Other accounts are not locked."""
    ids = [m for m in movement_ids if m is not None]
    if not ids:
        return
    acc, m = account_table.c, movement_table.c
    ctx.conn.execute(
        select(acc.id)
        .where(
            acc.id.in_(select(m.account_id).where(m.id.in_(ids))),
            acc.kind == AccountKind.EXCHANGE.value,
        )
        .order_by(acc.id)
        .with_for_update()
    ).all()


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
    *,
    deposit_iban: DepositIbanOutcome | None = None,
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
        TransferLinked(kind=kind, matched_by=matched_by, deposit_iban=deposit_iban, **legs),
    )
    if matched_by is TransferMatchedBy.MATCHER:
        # D162 amended (6): written after the link, so record_set's hook finds the leg linked.
        _confirm_tied_legs(ctx, out, in_)
    return link


def _confirm_tied_legs(ctx: CommandContext, out: TransferLeg, in_: TransferLeg) -> int:
    """D162 amended (6), 15 Nov 2026: after the matcher links a pair tied by an own IBAN (one
    leg names the other's account, names_the_other), each bank or depot line of the pair that
    holds only the model's proposal of that very transfer is booked as the transfer's leg by
    the matcher (_confirm_leg), so it leaves Zahlungen zuordnen. A pair linked by amount and
    date alone is left as it is: the model's guess plus a coincidence decides nothing
    (principle 3). The number of lines booked."""
    if not names_the_other(out, in_):
        return 0
    return sum(_confirm_leg(ctx, leg, other) for leg, other in ((out, in_), (in_, out)))


def _confirm_leg(ctx: CommandContext, leg: TransferLeg, other: TransferLeg) -> bool:
    """One leg of a tied pair the matcher linked (_confirm_tied_legs): a private account's line
    whose current rows are only the model's proposal (_free_for_matcher), all of the transfer
    code the line takes (internal_transfer_out|in), is booked by the matcher as
    named_transfer_set does, or as deposit_split_set for a deposit an exchange credited short
    (auto-confirmed, method matcher_transfer, its events). Never a line holding a person's or a
    rule's answer, a rule's or a code's proposal, a proposal over a confirmed answer, a rental
    line (its phase 2 matchers decide, D40), a line its own account books by code otherwise,
    nor a card's line its reader does not read as the payment (D162 amended (5) (7)(a))."""
    from . import allocations, matching  # matching imports this module

    if leg.source is not TransferLegSource.TRANSACTION:
        return False
    # Skipped while another transaction holds the line (_split_and_link).
    line = matching.load_line(ctx.conn, leg.id, lock=True, skip_locked=True)
    if line is None or not line.private or not line.current:
        return False
    if not line.open_for(rematch=True) or not _free_for_matcher(ctx.conn, line.tx.id, line.current):
        return False
    category = TRANSFER_CODES[(AccountPurpose.PRIVATE, line.tx.amount > 0)]
    if {a.category for a in line.current} != {category}:
        return False
    own = matching.own_code_set(line)
    if own is not None and any(a.category not in TRANSFER_CATEGORIES for a in own.allocations):
        return False
    if own is None and line.account_kind == AccountKind.CREDIT_CARD.value:
        return False
    fee = deposit_gap(leg, other) if leg.amount < 0 else None
    if fee is not None:
        answer = deposit_split_set(line.tx, fee)
    else:
        answer = named_transfer_set(line.tx, AccountPurpose.PRIVATE)
        if leg.counterparty_iban_hmac is not None and (
            leg.counterparty_iban_hmac == other.account_iban_hmac
        ):
            # This line names the other account itself: match_transfer_line's wording.
            answer = answer.model_copy(
                update={"reasoning": "Überweisung zwischen zwei eigenen Konten."}
            )
    allocations.record_set(ctx, answer, confirm=True, auto=True)
    return True


def confirm_linked(ctx: CommandContext) -> int:
    """`hausbuch relink-transfers`, D162 amended (6): the links the matcher wrote before it
    booked their legs (a leg holding the model's proposal of the transfer was linked as it
    was and kept its question in Zu prüfen) get their tied legs booked now (_confirm_tied_legs).
    Idempotent: a booked leg is no longer free for the matcher. The number of lines booked."""
    _lock(ctx)
    rows = [r for r in _current_links(ctx.conn) if r.matched_by == TransferMatchedBy.MATCHER.value]
    tx_ids = {i for r in rows for i in (r.out_transaction_id, r.in_transaction_id) if i}
    if not tx_ids:
        return 0
    a = allocation_table.c
    proposed = set(
        ctx.conn.execute(
            select(a.transaction_id).where(
                a.transaction_id.in_(tx_ids),
                a.status == AllocationStatus.PROPOSED.value,
                a.method == AllocationMethod.LLM.value,
            )
        ).scalars()
    )
    count = 0
    for r in rows:
        if not proposed.intersection({r.out_transaction_id, r.in_transaction_id}):
            continue
        out = _one_leg(
            ctx.conn,
            TransferLegSource.TRANSACTION if r.out_transaction_id else TransferLegSource.MOVEMENT,
            r.out_transaction_id or r.out_movement_id,
        )
        in_ = _one_leg(
            ctx.conn,
            TransferLegSource.TRANSACTION if r.in_transaction_id else TransferLegSource.MOVEMENT,
            r.in_transaction_id or r.in_movement_id,
        )
        if out is not None and in_ is not None:
            count += _confirm_tied_legs(ctx, out.leg, in_.leg)
    return count


def _both_ways(
    conn: Connection,
    leg: _Leg,
    pair: TransferCandidate,
    linked: set[UUID],
    today: date,
    window: _Window | None = None,
) -> bool:
    """D162 from the counterpart's side too: it must see this leg as its only fit, so one of
    two equal legs is never linked while the other's card asks which (transfer.ambiguous)."""
    other_id = pair.in_leg.id if pair.out_leg.id == leg.leg.id else pair.out_leg.id
    start, end = leg.leg.booked_on - 2 * _AROUND, leg.leg.booked_on + 2 * _AROUND
    near = window.between(start, end) if window is not None else _legs_between(conn, start, end)
    counter = next((o for o in near if o.leg.id == other_id), None)
    if counter is None:
        return False
    rest = [o for o in near if o.leg.id not in linked and o.leg.id != other_id]
    back = match_transfer(counter.leg, _context(conn, counter, rest, window), today=today)
    if back.category is not None:
        return back.link is not None and leg.leg.id in (back.link.out_leg.id, back.link.in_leg.id)
    fits = [
        o.leg.id
        for o in rest
        if o.leg.account_id != counter.leg.account_id
        and (o.leg.amount > 0) != (counter.leg.amount > 0)
        and ibans_agree(counter.leg, o.leg)
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
    # The matcher links two transfer legs only (_current_links): a counterpart booked as
    # something else is left to the person (open_transfers proposes it, transfer.proposed).
    other_id = (
        found.link.in_leg.id if found.link.out_leg.id == leg.leg.id else found.link.out_leg.id
    )
    other = next((o for o in others if o.leg.id == other_id), None)
    if leg.booked is None or other is None:
        return None
    if other.booked is None:
        return _book_named_leg(ctx, leg, other, found.link)
    if found.link.deposit_fee is not None:
        bank = leg if leg.leg.id == found.link.out_leg.id else other
        return _split_and_link(ctx, bank, found.link)
    return _write(
        ctx, found.link.out_leg, found.link.in_leg, found.link.kind, TransferMatchedBy.MATCHER
    )


def _held_answer(conn: Connection, transaction_id: UUID) -> frozenset[AllocationCategory] | None:
    """The categories of the latest set a person or a rule confirmed on the line, current or
    superseded since (a superseded row keeps its method, confirmed_at and confirmed_by); None
    when neither ever confirmed one. Review of D162 amended, 15 Nov 2026: the one-off
    household re-sort writes a proposal over a person's confirmed fallback row, so a line whose
    current rows are proposals may still hold a person's answer underneath. Every row of the
    line is read, which covers the supersedes_id chain and the old rows a shorter set left
    unnamed (services.allocations.record_set). A current confirmed set wins over every
    superseded one (two answers may carry the same confirmed_at)."""
    a = allocation_table.c
    rows = conn.execute(
        select(a.category, a.confirmed_at, a.status)
        .where(
            a.transaction_id == transaction_id,
            a.confirmed_at.is_not(None),
            or_(
                a.confirmed_by.is_not(None),
                a.method.in_([AllocationMethod.USER.value, AllocationMethod.RULE.value]),
            ),
        )
        .order_by(a.confirmed_at.desc())
    ).all()
    return _held_of(rows)


def _held_answers(
    conn: Connection, ids: Sequence[UUID]
) -> dict[UUID, frozenset[AllocationCategory] | None]:
    """_held_answer for every line among `ids` in one read (21 Nov 2026: two reads per
    candidate line in lines_on_cards); a line with no such row maps to None."""
    a = allocation_table.c
    rows: dict[UUID, list[Row]] = {i: [] for i in ids}
    for r in conn.execute(
        select(a.transaction_id, a.category, a.confirmed_at, a.status)
        .where(
            a.transaction_id.in_(ids),
            a.confirmed_at.is_not(None),
            or_(
                a.confirmed_by.is_not(None),
                a.method.in_([AllocationMethod.USER.value, AllocationMethod.RULE.value]),
            ),
        )
        .order_by(a.transaction_id, a.confirmed_at.desc())
    ):
        rows[r.transaction_id].append(r)
    return {i: _held_of(found) for i, found in rows.items()}


def _held_of(rows: Sequence[Row]) -> frozenset[AllocationCategory] | None:
    """_held_answer from its rows, newest confirmation first."""
    if not rows:
        return None
    current = [r for r in rows if r.status != AllocationStatus.SUPERSEDED.value]
    if current:
        return frozenset(AllocationCategory(r.category) for r in current)
    latest = rows[0].confirmed_at
    return frozenset(AllocationCategory(r.category) for r in rows if r.confirmed_at == latest)


def _free_for_matcher(conn: Connection, line_id: UUID, current: Sequence) -> bool:
    """Whether the transfer matcher may book the line itself (D162 amended 15 Nov 2026,
    review): it holds nothing or only the model's proposal (method llm; a rule's or a code
    matcher's proposal is not the model's), and no person or rule ever confirmed an answer on
    it (_held_answer)."""
    if not _only_the_models(current):
        return False
    return _held_answer(conn, line_id) is None


def _only_the_models(current: Sequence) -> bool:
    """Whether every current row is the model's proposal (_free_for_matcher's first half)."""
    return not any(
        r.status != AllocationStatus.PROPOSED or r.method != AllocationMethod.LLM for r in current
    )


# The answers a bank line may hold for an untied deposit link by hand (review, 15 Nov 2026):
# the whole line a transfer out, or the transfer plus its fee; a fee alone is another answer.
_DEPOSIT_ANSWERS = (
    frozenset({AllocationCategory.INTERNAL_TRANSFER_OUT}),
    frozenset({AllocationCategory.INTERNAL_TRANSFER_OUT, AllocationCategory.BANK_FEES}),
)


def _answered_otherwise(conn: Connection, a: TransferLeg, b: TransferLeg) -> bool:
    """Review of D162 amended, 15 Nov 2026: whether `a` and `b` are an UNTIED deposit an
    exchange credited short (deposit_gap, and the bank line does not name the exchange's
    deposit IBAN, which the exchange does not hold yet) whose bank line holds an answer a
    person or a rule confirmed as something other than a transfer or its fee (_held_answer).
    Such a pair is neither offered on a card nor linked by hand: the first link would replace
    that answer and teach the exchange the IBAN on an amount and a date alone. A tied pair (the
    IBAN learned) stays offered with its one click."""
    out, in_ = _out_in(a, b)
    if deposit_gap(out, in_) is None or names_the_other(out, in_):
        return False
    held = _held_answer(conn, out.id)
    return held is not None and held not in _DEPOSIT_ANSWERS


def _deposit_text(conn: Connection, leg: _Leg, other: _Leg, fee: Decimal | None) -> str | None:
    """The card's text for a deposit credited `fee` short (rules.matching.deposit_link_text),
    naming the exchange and, when the pair is untied, what the link does with the IBAN. D162
    amended (8): for an exact pair, what the link does with the IBAN when the bank line is a
    person's confirmed transfer out (rules.matching.deposit_iban_link_text), else None."""
    from . import accounts  # accounts imports services that import this module

    exchange, bank = (leg, other) if leg.leg.amount > 0 else (other, leg)
    if fee is None:
        # D162 amended (8): an exact pair whose bank line a person confirmed as a transfer out
        # teaches the exchange the line's IBAN when linked; the card says so before the click.
        return deposit_iban_link_text(exchange.label, _hand_link_deposit_iban(conn, bank, exchange))
    iban = (
        None
        if names_the_other(bank.leg, exchange.leg)
        else accounts.deposit_iban_outcome(conn, exchange.leg.account_id, bank.leg.id)
    )
    return deposit_link_text(fee, exchange.label, iban)


def _is_split(line_rows: Sequence, fee: Decimal) -> bool:
    """Whether the line is booked as internal_transfer_out plus bank_fees of exactly `fee`."""
    rows = [r for r in line_rows if r.amount != 0]
    categories = {r.category for r in rows}
    if categories != {AllocationCategory.INTERNAL_TRANSFER_OUT, AllocationCategory.BANK_FEES}:
        return False
    kept = -sum((r.amount for r in rows if r.category == AllocationCategory.BANK_FEES), Decimal(0))
    return kept == fee


def _deposit_state(
    conn: Connection, line_id: UUID, line_rows: Sequence, amount: Decimal, fee: Decimal
) -> str | None:
    """How a bank line paying a deposit credited `fee` short is booked: "split" (already
    internal_transfer_out plus bank_fees of exactly the fee), "whole" (one internal_transfer_out
    row of the whole amount the matcher wrote, or only the model's proposal of it, and no
    person's or rule's answer underneath, _held_answer), None otherwise (a person's or a rule's
    answer, which the matcher never replaces)."""
    if _is_split(line_rows, fee):
        return "split"
    rows = [r for r in line_rows if r.amount != 0]
    whole = {r.category for r in rows} == {AllocationCategory.INTERNAL_TRANSFER_OUT} and sum(
        (r.amount for r in rows), Decimal(0)
    ) == amount
    if not whole:
        return None
    by_matcher = all(
        r.method == AllocationMethod.MATCHER_TRANSFER and r.status == AllocationStatus.CONFIRMED
        for r in line_rows
    )
    if by_matcher:
        return "whole" if _held_answer(conn, line_id) is None else None
    return "whole" if _free_for_matcher(conn, line_id, line_rows) else None


def _split_and_link(
    ctx: CommandContext, bank: _Leg, pair: TransferCandidate
) -> TransferLink | None:
    """D162 amended 15 Nov 2026: the pair is tied by the exchange's deposit IBAN and the
    exchange credited the deposit `pair.deposit_fee` short without a fee row. The bank line,
    booked as one internal_transfer_out by the matcher (or only proposed), is booked again by
    code as the credited amount internal_transfer_out plus the gap as bank_fees
    (rules.matching.deposit_split_set, method matcher_transfer, auto-confirmed), then the pair
    is linked. A line a person or a rule booked otherwise is never replaced: no link, and the
    card proposes it for a link by hand (link_by_hand splits it as the person's answer)."""
    from . import allocations, matching  # matching imports this module

    fee = pair.deposit_fee
    assert fee is not None
    # Review, 15 Nov 2026: the advisory lock is held here, so a line another transaction holds
    # (its own job, which takes the advisory lock in its hook) is skipped, never waited for: the
    # other order deadlocked. That job's hook (after_booking) finishes the pair.
    line = matching.load_line(ctx.conn, bank.leg.id, lock=True, skip_locked=True)
    if line is None:
        return None
    state = _deposit_state(ctx.conn, line.tx.id, line.current, line.tx.amount, fee)
    if state is None:
        return None
    if state == "whole":
        allocations.record_set(ctx, deposit_split_set(line.tx, fee), confirm=True, auto=True)
        # record_set's hook (after_booking) links the pair itself when it sees it.
        found = _link_of(ctx.conn, pair.out_leg.id, pair.in_leg.id)
        if found is not None:
            return found
        linked = _linked_ids(ctx.conn)
        if pair.out_leg.id in linked or pair.in_leg.id in linked:
            return None
    return _write(ctx, pair.out_leg, pair.in_leg, pair.kind, TransferMatchedBy.MATCHER)


def _book_named_leg(
    ctx: CommandContext, leg: _Leg, other: _Leg, pair: TransferCandidate
) -> TransferLink | None:
    """D162 amended 15 Nov 2026: `leg` is booked as a transfer and names `other`'s account
    by IBAN, the pair fits both ways and to the cent, and `other` is a private bank or depot
    line not booked as a transfer (a depot's cash line has no IBAN, so the model guessed it).
    When `other` holds no confirmed answer (only the model's proposal, or nothing), the
    matcher books it as the transfer's leg (rules.matching.named_transfer_set, auto-confirmed
    as any matcher set at confidence 1.0) and links the pair. A person's or a rule's confirmed
    answer is never replaced: that line keeps its card (transfer.proposed, with its re-book
    button). The rental account is left to its phase 2 matchers."""
    from . import allocations, matching  # matching imports this module

    fee = pair.deposit_fee
    private_line = (
        other.leg.source is TransferLegSource.TRANSACTION
        and other.purpose is AccountPurpose.PRIVATE
    )
    names_other = (
        leg.leg.counterparty_iban_hmac is not None
        and leg.leg.counterparty_iban_hmac == other.leg.account_iban_hmac
        and leg.leg.amount == -other.leg.amount
    )
    # D162 amended 15 Nov 2026: an exchange's deposit row and the bank line that names the
    # exchange's deposit IBAN, the deposit credited short without a fee row: the bank line is
    # booked as the credited transfer plus the gap as bank fees.
    deposit = (
        fee is not None and other.leg.id == pair.out_leg.id and names_the_other(leg.leg, other.leg)
    )
    if not private_line or not (names_other or deposit):
        return None
    # Review, 15 Nov 2026: skipped while another transaction holds the line (_split_and_link).
    line = matching.load_line(ctx.conn, other.leg.id, lock=True, skip_locked=True)
    if line is None or not line.open_for(rematch=True):
        return None
    if not _free_for_matcher(ctx.conn, line.tx.id, line.current):
        # Review, 15 Nov 2026: only nothing or the model's own proposal is replaced, never a
        # rule's proposal nor a proposal written over a person's or a rule's answer.
        return None
    own = matching.own_code_set(line)
    if own is not None and any(a.category not in TRANSFER_CATEGORIES for a in own.allocations):
        # Review, 15 Nov 2026: relink runs at a depot import before the lines' own jobs, so a
        # line its own account books by code otherwise (a „Wertpapier Verkauf“) is left to
        # that job, never pre-empted by an equal debit that names the depot.
        return None
    if own is None and line.account_kind == AccountKind.CREDIT_CARD.value:
        # Review of D162 amended (5), 15 Nov 2026: a card's line is its settlement leg only
        # when the card reader's own code reads it as the payment („Bezahlung“); a merchant's
        # refund („Gutschrift“) of the same amount, a few banking days before a debit naming
        # the payment IBAN, was booked as the transfer and the real payment left with a dead
        # card. Such a pair goes to the person as a proposed card (principle 3).
        return None
    answer = (
        deposit_split_set(line.tx, fee)
        if deposit and fee is not None
        else named_transfer_set(line.tx, other.purpose)
    )
    allocations.record_set(ctx, answer, confirm=True, auto=True)
    # record_set's hook (after_booking) links the pair itself when it sees it: that link is
    # returned, never a second row.
    found = _link_of(ctx.conn, pair.out_leg.id, pair.in_leg.id)
    if found is not None:
        return found
    linked = _linked_ids(ctx.conn)
    if leg.leg.id in linked or other.leg.id in linked:
        return None
    return _write(ctx, pair.out_leg, pair.in_leg, pair.kind, TransferMatchedBy.MATCHER)


def after_booking(
    ctx: CommandContext, transaction_id: UUID, categories: set[AllocationCategory]
) -> None:
    """services.allocations.record_set's hook: a line booked as a transfer is linked; it
    may also be the late counterpart of another account's unlinked transfer leg near its
    date, which is tried then (link_leg links two transfer legs only)."""
    if categories & TRANSFER_CATEGORIES and link_leg(ctx, transaction_id) is not None:
        return
    leg = _one_leg(ctx.conn, TransferLegSource.TRANSACTION, transaction_id)
    if leg is None:
        return
    linked = _linked_ids(ctx.conn)
    if transaction_id in linked:
        return
    # Only a leg booked as a transfer is tried below, so the window reads those lines only.
    around = _legs_between(
        ctx.conn,
        leg.leg.booked_on - _AROUND,
        leg.leg.booked_on + _AROUND,
        transfer_booked_only=True,
    )
    for other in around:
        if other.leg.account_id == leg.leg.account_id or other.booked is None:
            continue
        if other.leg.id in linked:
            continue
        found = link_leg(ctx, other.leg.id, other.leg.source)
        if found is None:
            continue
        # D162 amended 15 Nov 2026: another leg's link may book and link a third line it
        # names (_book_named_leg), so the search goes on until this line is linked.
        if transaction_id in (found.out_transaction_id, found.in_transaction_id):
            return
        linked = _linked_ids(ctx.conn)


def relink(ctx: CommandContext, account_id: UUID) -> int:
    """D162 after an import: every unlinked transfer leg of the account (confirmed or
    proposed) is linked when one counterpart fits now; the number linked. D162 amended
    15 Nov 2026: also every other account's unlinked transfer line that names this account's
    IBAN, so a depot's cash lines imported after the bank's debits are booked and linked by
    those debits (_book_named_leg), whether or not the model has answered them yet."""
    t, acc = transaction_table.c, account_table.c
    legs = _line_legs(ctx.conn, t.account_id == account_id)
    legs += _movement_legs(ctx.conn, movement_table.c.account_id == account_id)
    iban = ctx.conn.execute(
        select(func.coalesce(acc.iban_hmac, acc.payment_iban_hmac)).where(acc.id == account_id)
    ).scalar_one_or_none()
    if iban is not None:
        legs += _line_legs(ctx.conn, t.account_id != account_id, t.counterparty_iban_hmac == iban)
    linked = _linked_ids(ctx.conn)
    count = 0
    for leg in legs:
        if leg.booked is None or leg.leg.id in linked:
            continue
        if link_leg(ctx, leg.leg.id, leg.leg.source) is not None:
            count += 1
            linked = _linked_ids(ctx.conn)
    return count


def relink_all(ctx: CommandContext) -> int:
    """`hausbuch relink-transfers`: relink over every account of the workspace, oldest
    first; the number linked."""
    acc = account_table.c
    ids = ctx.conn.execute(select(acc.id).order_by(acc.created_at, acc.id)).scalars().all()
    return sum(relink(ctx, account_id) for account_id in ids)


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


def _tied(a: TransferLeg, b: TransferLeg) -> bool:
    """Whether one leg names the other's own IBAN as its counterparty (for a card, its
    confirmed payment IBAN stands as its account IBAN, _line_legs): the pair is a transfer
    by an own IBAN, not only by its amount and date."""
    return names_the_other(a, b)


def _rebook_on_link(leg: _Leg, other: _Leg) -> bool:
    """D176 (4), review of step 2b: `leg` is a bank line booked as something other than a
    transfer (or not yet booked) and tied to `other` by an own IBAN, so linking them re-books
    it as a transfer first (TransferLinkRequest.rebook)."""
    return (
        leg.booked is None
        and leg.leg.source is TransferLegSource.TRANSACTION
        and _tied(leg.leg, other.leg)
    )


def link_by_hand(
    ctx: CommandContext, body: TransferLinkCreate, *, iban_key: bytes | None = None
) -> TransferLink:
    """POST /transfers: the user links two legs (matched_by user). The two legs linked to
    each other already (a second click, or the matcher linked them since the card was shown)
    return that link; 409 when either leg is linked to another; 422 transfer.legs_invalid for
    two legs that are no transfer.

    With rebook (TransferLinkRequest, D176 (4)) each leg not booked as a transfer is first
    re-booked as internal_transfer_out or _in through the person's answer
    (review.allocate_transaction: method user, the old rows superseded, its events), so a card
    bill answered hh_credit_card no longer counts as spending beside the card's own purchases;
    422 transfer.legs_invalid when such a leg is not tied to the other by an own IBAN. The
    re-booking may let the matcher link the pair itself (allocations.record_set's hook): that
    link is returned; a link of either leg to another counterpart is a 409 and nothing is
    written.

    The exchange's row is locked before the transfer lock (_lock's lock order, D162 amended
    (8)), so a link by hand and an exchange import never wait for each other in a circle."""
    _lock_exchanges(ctx, (body.out_movement_id, body.in_movement_id))
    _lock(ctx)
    from .chain import transfers as chain_transfers  # it imports this module

    if chain_transfers.is_coin_link(ctx.conn, body):
        # Phase 7 step 5 (D184): two coin legs, linked by the chain matcher's rules.
        return chain_transfers.link_by_hand(ctx, body)
    out, in_ = _pair(ctx.conn, body)
    # D162 amended 15 Nov 2026: a private bank line paying a deposit the exchange credited up
    # to 2 € short without a fee row. Linking it books the line as the credited transfer plus
    # the gap as bank fees (the person's answer, whatever `rebook` says) and records the line's
    # counterparty IBAN as the exchange's deposit IBAN, so the next deposits link by it.
    fee = deposit_gap(out.leg, in_.leg)
    if fee is not None:
        return _link_deposit_by_hand(ctx, body, out, in_, fee, iban_key)
    # The matcher may have linked exactly this pair since the card was shown (a re-check after
    # an import reconciled): the user's answer is already true, so it is not a conflict.
    already = _link_of(ctx.conn, out.leg.id, in_.leg.id)
    if already is not None:
        if getattr(body, "rebook", False) and iban_key is not None:
            # A leg the model only proposed as a transfer is confirmed by this click.
            for leg, other in ((out, in_), (in_, out)):
                if leg.booked is None and _rebook_on_link(leg, other):
                    _rebook(ctx, leg, iban_key)
        # D162 amended (8): the person's click on a pair the matcher linked by amount and date.
        _learn_on_hand_link(ctx, out, in_)
        return _link_of(ctx.conn, out.leg.id, in_.leg.id) or already
    linked = _linked_ids(ctx.conn)
    if out.leg.id in linked or in_.leg.id in linked:
        raise ConflictError("a leg of this transfer is linked already")
    if getattr(body, "rebook", False):
        legs = [(leg, other) for leg, other in ((out, in_), (in_, out)) if leg.booked is None]
        if any(not _rebook_on_link(leg, other) for leg, other in legs):
            raise RuleViolationError(
                [TRANSFER_LEGS_INVALID], "only a leg tied to the other by an own IBAN is re-booked"
            )
        if legs and iban_key is None:
            raise ValueError("re-booking a leg needs the IBAN key")
        for leg, _ in legs:
            _rebook(ctx, leg, iban_key)  # type: ignore[arg-type]
        found = _link_of(ctx.conn, out.leg.id, in_.leg.id)
        if found is not None:
            # A re-booked leg is tied by an own IBAN, so an exchange here holds its IBAN
            # already: nothing to learn (D162 amended (8)).
            return found
        linked = _linked_ids(ctx.conn)
        if out.leg.id in linked or in_.leg.id in linked:
            raise ConflictError("a leg of this transfer is linked already")
    # D162 amended (8): an exact pair of a bank line a person confirmed as a transfer out and an
    # exchange's deposit teaches the exchange the line's IBAN; read before the link is written,
    # so its transfer.linked names the outcome, learned after it (as _link_deposit_by_hand).
    iban = _hand_link_deposit_iban(ctx.conn, out, in_)
    found = _write(ctx, out.leg, in_.leg, body.kind, TransferMatchedBy.USER, deposit_iban=iban)
    if iban is DepositIbanOutcome.LEARNED:
        from . import accounts  # accounts imports services that import this module

        accounts.learn_exchange_deposit_iban(ctx, in_.leg.account_id, out.leg.id)
    return found


def _link_deposit_by_hand(
    ctx: CommandContext,
    body: TransferLinkCreate,
    out: _Leg,
    in_: _Leg,
    fee: Decimal,
    iban_key: bytes | None,
) -> TransferLink:
    """link_by_hand for a bank line (`out`, private) and an exchange's deposit row (`in_`)
    credited `fee` short without a fee row (D162 amended 15 Nov 2026). Unless the line is
    booked as that split already, it is booked as internal_transfer_out of the credited amount
    and bank_fees of the gap through the person's answer (review.allocate_transaction: method
    user, the old rows superseded, its events), whatever `rebook` says: the click confirms both
    parts. Then the pair is linked (matched_by user) and the line's counterparty IBAN is
    recorded as the exchange's deposit IBAN (accounts.learn_exchange_deposit_iban), when the
    exchange has none yet and no other account holds it. The same two legs linked already
    return their link; 409 when either is linked to another counterpart."""
    from . import accounts, matching  # matching imports this module

    already = _link_of(ctx.conn, out.leg.id, in_.leg.id)
    linked = _linked_ids(ctx.conn)
    if already is None and (out.leg.id in linked or in_.leg.id in linked):
        raise ConflictError("a leg of this transfer is linked already")
    line = matching.load_line(ctx.conn, out.leg.id, lock=True)
    assert line is not None  # _pair read it
    if already is None and _answered_otherwise(ctx.conn, out.leg, in_.leg):
        # Review, 15 Nov 2026: the card never offers this pair; a person re-answers the line
        # as a transfer first, so the first link never replaces a confirmed answer unseen.
        raise RuleViolationError(
            [TRANSFER_LEGS_INVALID],
            "a line a person or a rule answered otherwise is linked to a deposit credited short"
            " only once it is answered as a transfer",
        )
    if not _is_split([r for r in line.current if r.status == AllocationStatus.CONFIRMED], fee):
        if iban_key is None:
            raise ValueError("re-booking a leg needs the IBAN key")
        _answer(ctx, deposit_split_set(line.tx, fee, method=AllocationMethod.USER), iban_key)
    # Read before the link is written, so its transfer.linked names what happens to the IBAN;
    # learned after it (learning re-matches the waiting lines naming the IBAN).
    iban = accounts.deposit_iban_outcome(ctx.conn, in_.leg.account_id, out.leg.id)
    found = already or _link_of(ctx.conn, out.leg.id, in_.leg.id)
    if found is None:
        linked = _linked_ids(ctx.conn)
        if out.leg.id in linked or in_.leg.id in linked:
            raise ConflictError("a leg of this transfer is linked already")
        found = _write(ctx, out.leg, in_.leg, body.kind, TransferMatchedBy.USER, deposit_iban=iban)
    if iban is DepositIbanOutcome.LEARNED:
        accounts.learn_exchange_deposit_iban(ctx, in_.leg.account_id, out.leg.id)
    return found


# --- The exchange's deposit IBAN from a person's confirmation (D162 amended (8)) ------------


def _confirmed_transfer_out(conn: Connection, transaction_id: UUID) -> bool:
    """Whether the line's live rows are all confirmed and resolve to internal_transfer_out:
    the whole line, or the transfer plus its fee within EXCHANGE_DEPOSIT_GAP (_one_category)."""
    a = allocation_table.c
    statuses = set(
        conn.execute(
            select(a.status).where(a.transaction_id == transaction_id, a.status.in_(_LIVE))
        ).scalars()
    )
    if statuses != {AllocationStatus.CONFIRMED.value}:
        return False
    found = _one_category(conn, [transaction_id]).get(transaction_id)
    return found is AllocationCategory.INTERNAL_TRANSFER_OUT


def _confirmed_by_person(conn: Connection, transaction_id: UUID) -> bool:
    """Whether the line's current confirmed set is a person's: its latest transaction.allocated
    or transaction.reallocated event (every confirmation writes one) is not auto and was
    written by a person's command. The rows cannot tell: a person who confirms a proposal
    keeps the proposer's method, and confirmed_by stays null for an actor without an app_user
    row."""
    e = event_table.c
    latest = conn.execute(
        select(e.actor_type, e.payload)
        .where(
            e.entity_id == transaction_id,
            e.type.in_(
                [EventType.TRANSACTION_ALLOCATED.value, EventType.TRANSACTION_REALLOCATED.value]
            ),
        )
        .order_by(e.seq.desc())
        .limit(1)
    ).first()
    return (
        latest is not None
        and latest.actor_type == ActorType.USER.value
        and latest.payload.get("auto") is False
    )


def _exchange_deposit(conn: Connection, transaction_id: UUID) -> _Leg | None:
    """The exchange's EUR deposit that is the in leg of the current link whose out leg is the
    bank line `transaction_id` (_current_links, written by the matcher or a person); None when
    there is no such link. Only a deposit's out leg: a withdrawal's bank credit never names the
    exchange's deposit IBAN (its payouts come from another one)."""
    row = next(
        (r for r in _current_links(conn) if r.out_transaction_id == transaction_id),
        None,
    )
    if row is None or row.in_movement_id is None:
        return None
    in_ = _one_leg(conn, TransferLegSource.MOVEMENT, row.in_movement_id)
    if in_ is None or in_.leg.account_kind is not AccountKind.EXCHANGE:
        return None
    return in_


def deposit_iban_offer(conn: Connection, transaction_id: UUID) -> DepositIbanOffer | None:
    """D162 amended (8), review (15 Nov 2026): what the allocation panel's box „IBAN als
    Einzahlungskonto von <Börse> merken“ names for the private bank debit `transaction_id`
    (the caller passes only such a line): the exchange's deposit it is the out leg of a
    current link to, when accounts.deposit_iban_outcome would learn the line's IBAN; None
    otherwise (no such link, the exchange holds an IBAN, the IBAN carries other payees or is
    another own account's), so a refused outcome shows no box. Read only, whatever the line's
    current rows: a link a person made stays current while the line is proposed otherwise."""
    from . import accounts  # accounts imports services that import this module

    deposit = _exchange_deposit(conn, transaction_id)
    if deposit is None:
        return None
    outcome = accounts.deposit_iban_outcome(conn, deposit.leg.account_id, transaction_id)
    if outcome is not DepositIbanOutcome.LEARNED:
        return None
    return DepositIbanOffer(
        deposit_movement_id=deposit.leg.id,
        exchange_account_id=deposit.leg.account_id,
        exchange_label=deposit.label,
        deposit_date=deposit.leg.booked_on,
        deposit_amount=deposit.leg.amount,
        text=deposit_iban_offer_text(deposit.label, deposit.leg.booked_on, deposit.leg.amount),
    )


def lock_deposit_exchange(ctx: CommandContext, transaction_id: UUID) -> UUID | None:
    """D162 amended (8), lock order (fixed 16 Nov 2026): review.allocate_transaction calls it
    first, before the line's lock, when a person's answer ticks the box „IBAN als
    Einzahlungskonto von <Börse> merken“: the row of the exchange the panel would name
    (deposit_iban_offer, read only) is locked, so learn_deposit_iban_on_confirm takes no row
    lock after the transfer lock the answer's own hook may take (_lock's lock order). The
    exchange's id, or None when no box would be shown (nothing locked: an exchange that holds
    an IBAN already is never locked by a person's answer). The caller learns only for the
    exchange locked here: a link committed between this read and the line's lock names
    another or none, and teaches nothing this time (the box shows again)."""
    offer = deposit_iban_offer(ctx.conn, transaction_id)
    if offer is None:
        return None
    _lock_exchanges(ctx, (offer.deposit_movement_id,))
    return offer.exchange_account_id


def learn_deposit_iban_on_confirm(
    ctx: CommandContext, transaction_id: UUID, offer: DepositIbanOffer
) -> DepositIbanOutcome | None:
    """D162 amended (8), 15 Nov 2026 (Luis: „Yes the app should learn Bison's account
    number“), as changed by its review the same day: review.allocate_transaction calls it after
    a person's answer is written, only when the answer ticked the box
    (ConfirmAllocationsBody.learn_deposit_iban) the panel showed for `offer`, read before the
    answer was written. When the line, a private bank debit now confirmed as
    internal_transfer_out (the whole line or the transfer plus its fee), is still the out leg
    of the current link to that same deposit, the line's counterparty IBAN becomes the
    exchange's deposit IBAN. The outcome is read first (accounts.deposit_iban_outcome, no
    lock) and learned under the exchange's row lock (accounts.learn_exchange_deposit_iban reads
    it again there), which lock_deposit_exchange took before the line's (the lock order): an
    exchange that holds an IBAN already is never locked by a person's answer. A link the
    answer itself lets the matcher write was not on the panel and teaches nothing. Only a
    person's command. The outcome, or None when nothing was to learn."""
    from . import accounts  # accounts imports services that import this module

    if ctx.actor.actor_type is not ActorType.USER:
        return None
    if not _confirmed_transfer_out(ctx.conn, transaction_id):
        return None
    deposit = _exchange_deposit(ctx.conn, transaction_id)
    if deposit is None or deposit.leg.id != offer.deposit_movement_id:
        return None
    outcome = accounts.deposit_iban_outcome(ctx.conn, deposit.leg.account_id, transaction_id)
    if outcome is not DepositIbanOutcome.LEARNED:
        return outcome
    return accounts.learn_exchange_deposit_iban(ctx, deposit.leg.account_id, transaction_id)


def _hand_link_deposit_iban(conn: Connection, out: _Leg, in_: _Leg) -> DepositIbanOutcome | None:
    """D162 amended (8): what a person's link by hand of the exact pair `out` (a bank line) and
    `in_` (an exchange's EUR deposit) does with the line's IBAN, read only: None unless the
    line's live rows are confirmed as internal_transfer_out by a person (_confirmed_by_person:
    a line the matcher, a rule or the model booked teaches nothing; a line still only proposed
    teaches when the person confirms it later with the panel's box ticked,
    learn_deposit_iban_on_confirm); otherwise
    accounts.deposit_iban_outcome."""
    from . import accounts  # accounts imports services that import this module

    if out.leg.source is not TransferLegSource.TRANSACTION:
        return None
    if in_.leg.source is not TransferLegSource.MOVEMENT:
        return None
    if in_.leg.account_kind is not AccountKind.EXCHANGE:
        return None
    if not _confirmed_transfer_out(conn, out.leg.id):
        return None
    if not _confirmed_by_person(conn, out.leg.id):
        return None
    return accounts.deposit_iban_outcome(conn, in_.leg.account_id, out.leg.id)


def _learn_on_hand_link(ctx: CommandContext, out: _Leg, in_: _Leg) -> None:
    """D162 amended (8): learn the exchange's deposit IBAN after a person's link by hand of an
    exact pair, when _hand_link_deposit_iban says it is learned (accounts.learn_exchange_
    deposit_iban reads it again under the account's lock)."""
    from . import accounts  # accounts imports services that import this module

    if _hand_link_deposit_iban(ctx.conn, out, in_) is DepositIbanOutcome.LEARNED:
        accounts.learn_exchange_deposit_iban(ctx, in_.leg.account_id, out.leg.id)


def _answer(ctx: CommandContext, answer: AllocationSet, iban_key: bytes) -> None:
    """The person's answer for a line, no rule learned (review.allocate_transaction)."""
    from . import review  # imports services.matching, which imports this module

    review.allocate_transaction(
        ctx,
        answer.transaction_id,
        ConfirmAllocationsBody(allocation_set=answer, learn_rule=False),
        iban_key=iban_key,
    )


def _rebook(ctx: CommandContext, leg: _Leg, iban_key: bytes) -> None:
    """The person's answer booking the whole line as one transfer part (no rule learned)."""
    from . import review  # imports services.matching, which imports this module

    category = (
        AllocationCategory.INTERNAL_TRANSFER_OUT
        if leg.leg.amount < 0
        else AllocationCategory.INTERNAL_TRANSFER_IN
    )
    answer = AllocationSet(
        transaction_id=leg.leg.id,
        allocations=[AllocationDraft(amount=leg.leg.amount, category=category)],
        method=AllocationMethod.USER,
        confidence=Decimal(1),
    )
    review.allocate_transaction(
        ctx,
        leg.leg.id,
        ConfirmAllocationsBody(allocation_set=answer, learn_rule=False),
        iban_key=iban_key,
    )


def _link_of(conn: Connection, out_id: UUID, in_id: UUID) -> TransferLink | None:
    """The current link joining exactly these two legs, if any."""
    for row in _current_links(conn):
        if {out_id, in_id} == set(_leg_ids(row)):
            return TransferLink.model_validate(row._asdict())
    return None


def unlink(ctx: CommandContext, link_id: UUID, replacement: TransferLinkCreate) -> TransferLink:
    """POST /transfers/{id}/unlink: the wrong link is replaced by the one the user picks; the
    old row names the new one in superseded_by (append-only), events transfer.unlinked and
    transfer.linked. 404 for an unknown link; 409 when it is superseded already or a leg of
    the replacement belongs to another current link.

    Phase 7 step 5, review of PR #396: a replacement of two coin legs is checked and written as
    a coin link by hand (chain_transfers.link_by_hand, the old link's legs counted as free),
    whatever kind the old link was; the exchange's row is locked before the transfer lock, as
    link_by_hand's order."""
    from .chain import transfers as chain_transfers  # it imports this module

    coin = chain_transfers.is_coin_link(ctx.conn, replacement)
    if coin:
        _lock_exchanges(ctx, (replacement.out_movement_id, replacement.in_movement_id))
    _lock(ctx)
    c = link_table.c
    old = ctx.conn.execute(select(link_table).where(c.id == link_id)).one_or_none()
    if old is None:
        raise NotFoundError("transfer", link_id)
    if old.superseded_by is not None:
        raise ConflictError("this link was replaced already")
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
    if coin:
        new = chain_transfers.link_by_hand(ctx, replacement, freed=frozenset(freed))
    else:
        out, in_ = _pair(ctx.conn, replacement)
        linked = _linked_ids(ctx.conn) - freed
        if out.leg.id in linked or in_.leg.id in linked:
            raise ConflictError("a leg of the replacement is linked already")
        new = _write(ctx, out.leg, in_.leg, replacement.kind, TransferMatchedBy.USER)
    ctx.conn.execute(update(link_table).where(c.id == link_id).values(superseded_by=new.id))
    append_event(ctx, EventType.TRANSFER_UNLINKED, link_id, TransferUnlinked(superseded_by=new.id))
    if coin:  # a deposit the app wrote for the replaced link ends with it (D181 (c) amended)
        chain_transfers.end_orphaned_deposits(ctx)
    return new


def release(ctx: CommandContext, link_id: UUID) -> None:
    """POST /transfers/{id}/release: „Verknüpfung lösen“ on a coin link code wrote from a
    learned exchange address or withdrawal pair (phase 7 D261, review fixes 16 Nov 2026,
    chain_transfers.release_learned_link). The exchange's row is locked before the transfer
    lock, as unlink's order."""
    from .chain import transfers as chain_transfers  # it imports this module

    c = link_table.c
    legs = ctx.conn.execute(
        select(c.out_movement_id, c.in_movement_id).where(c.id == link_id)
    ).one_or_none()
    if legs is None:
        raise NotFoundError("transfer", link_id)
    _lock_exchanges(ctx, (legs.out_movement_id, legs.in_movement_id))
    _lock(ctx)
    chain_transfers.release_learned_link(ctx, link_id)


def own_wallet_deposit(ctx: CommandContext, body: OwnWalletDepositCreate) -> TransferLink:
    """POST /transfers/own-wallet-deposit: „An eigene Wallet“ on an exchange's withdrawal to a
    balance-only chain (phase 7 D181 (c) amended 16 Nov 2026,
    chain_transfers.write_own_wallet_deposit). The exchange's row is locked before the
    transfer lock, as link_by_hand's order."""
    from .chain import transfers as chain_transfers  # it imports this module

    _lock_exchanges(ctx, (body.withdrawal_movement_id,))
    _lock(ctx)
    return chain_transfers.write_own_wallet_deposit(ctx, body)


# --- Reading links and cards ----------------------------------------------------------------


def list_links(conn: Connection, *, start: date, end: date) -> list[TransferLinkView]:
    """The current links with a leg booked between `start` and `end`, by the out leg's date.
    D162 amended (7): never a returned payment (two lines of one account): it moved nothing
    between accounts, so the Haushalt's „Gespart, nicht ausgegeben“ (and saved_in) never lists
    it; Konten › Umsätze names each half's partner instead (services.returns)."""
    rows = _current_links(conn)
    # Only the links' own legs are read (21 Nov 2026: every line of the range and its rows
    # were read to look up a few, 12 months of every private account for GET /health). A
    # leg's reading does not depend on the other legs read with it (_line_legs, _one_category).
    t, m = transaction_table.c, movement_table.c
    first, last = start - _AROUND, end + _AROUND
    tx_ids = sorted({i for r in rows for i in (r.out_transaction_id, r.in_transaction_id) if i})
    mv_ids = sorted({i for r in rows for i in (r.out_movement_id, r.in_movement_id) if i})
    found = (
        _line_legs(conn, t.booking_date >= first, t.booking_date <= last, t.id.in_(tx_ids))
        if tx_ids
        else []
    )
    if mv_ids:
        found += _movement_legs(
            conn,
            func.date(m.occurred_at) >= first,
            func.date(m.occurred_at) <= last,
            m.id.in_(mv_ids),
        )
    legs = {leg.leg.id: leg for leg in found}
    out = []
    for r in rows:
        a = legs.get(r.out_transaction_id or r.out_movement_id)
        b = legs.get(r.in_transaction_id or r.in_movement_id)
        if a is None or b is None:
            continue
        if is_return_link(a.view(), b.view()):
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
    its window without a counterpart. The coin legs' cards (phase 7 step 5, D184) follow the
    EUR legs'."""
    from .chain import transfers as chain_transfers  # it imports this module

    # Phase 10 §7.3 (4): the current links read once for both, nothing written in between.
    linked = _linked_ids(conn)
    return _open_eur_transfers(conn, today, linked) + chain_transfers.open_transfers(
        conn, today, linked=linked
    )


def _open_eur_transfers(conn: Connection, today: date, linked: set[UUID]) -> list[OpenTransfer]:
    moves = _movement_legs(conn)
    lines = _line_legs(conn, transaction_table.c.id.in_(_with_transfer_row()))
    waiting = [leg for leg in lines + moves if leg.booked is not None and leg.leg.id not in linked]
    items = []
    if not waiting:
        return items
    # One read for every card's window (±_BY_HAND, which covers _both_ways' ±2·_AROUND); its
    # movements are those just read.
    window = _Window(
        conn,
        min(leg.leg.booked_on for leg in waiting) - _BY_HAND,
        max(leg.leg.booked_on for leg in waiting) + _BY_HAND,
        moves,
        lines,
    )
    # Read once, only when a leg has no counterpart: each account's first and last bank line,
    # and the account(s) each own IBAN belongs to.
    firsts: dict[UUID, date] | None = None
    own: dict[str, UUID] = {}
    payers: dict[UUID, set[UUID]] = {}
    lasts: dict[UUID, date] = {}
    named_by: dict[str, set[UUID]] = {}
    for leg in waiting:
        start, end = leg.leg.booked_on - _BY_HAND, leg.leg.booked_on + _BY_HAND
        others = [
            o
            for o in window.between(start, end)
            if o.leg.id not in linked and o.leg.account_id != leg.leg.account_id
        ]
        near = [o for o in others if abs(o.leg.booked_on - leg.leg.booked_on) <= _AROUND]
        found = match_transfer(leg.leg, _context(conn, leg, near, window), today=today)
        by_id = {o.leg.id: o for o in others}
        if found.link is not None and not _both_ways(conn, leg, found.link, linked, today, window):
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
                if (
                    o.leg.amount == -leg.leg.amount
                    or (
                        (o.leg.amount > 0) != (leg.leg.amount > 0)
                        and deposit_gap(*_out_in(leg.leg, o.leg)) is not None
                    )
                )
                and (named is None or o.leg.account_iban_hmac in (None, named))
            ]
            if not same:
                if firsts is None:
                    accounts = window.accounts(conn)
                    firsts, own = _first_days(conn), _own_accounts_of(accounts)
                    payers = _card_payers(conn)
                    lasts, named_by = _last_days(conn), _own_account_sets_of(accounts)
                if _before_every_history(leg, firsts, own, payers):
                    continue
                if _after_every_history(leg, lasts, named_by, payers):
                    continue
            code, candidates = OpenTransferCode.UNMATCHED_LEG, same
        else:
            continue
        # Review, 15 Nov 2026: an untied short deposit whose bank line a person or a rule
        # answered otherwise is no candidate (_answered_otherwise). A proposal left without one
        # waits for its window like a leg without a counterpart.
        kept = [c for c in candidates if not _answered_otherwise(conn, leg.leg, c.leg)]
        if len(kept) < len(candidates):
            if not kept and code is OpenTransferCode.PROPOSED:
                if found.card is None:
                    continue
                code = OpenTransferCode.UNMATCHED_LEG
            candidates = kept
        items.append(
            OpenTransfer(
                code=code,
                leg=leg.view(),
                candidates=[
                    c.view(
                        rebook_on_link=_rebook_on_link(c, leg),
                        deposit_fee=(fee := deposit_gap(*_out_in(leg.leg, c.leg))),
                        deposit_text=_deposit_text(conn, leg, c, fee),
                    )
                    for c in candidates
                ],
                window_end=transfer_window_end(leg.leg.booked_on),
            )
        )
    return items


def _first_days(conn: Connection) -> dict[UUID, date]:
    """The first day of each account's bank lines: its earliest line of a reconciled import,
    per account id. Movements are left out: an exchange's export carries its history from
    years before the bank accounts' (D178), so an old trade would keep every card."""
    t = transaction_table.c
    rows = conn.execute(
        select(t.account_id, func.min(t.booking_date))
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            t.excluded_as_duplicate_of.is_(None),
            import_table.c.status == ImportStatus.RECONCILED.value,
        )
        .group_by(t.account_id)
    ).all()
    return {account_id: day for account_id, day in rows}


def _last_days(conn: Connection) -> dict[UUID, date]:
    """D162 amended (9), 15 Nov 2026: the day each account's imported history reaches, per
    account id: its latest line of a reconciled import (the filters of _first_days), or for an
    account synced over FinTS the end of its latest successful run (`requested_to` of a run that
    ended nothing_new, or done with a reconciled import), whichever is later. A run that brings
    no new line keeps no import (phase 5 D127), so the lines alone would never move forward for
    a quiet account. Movements are left out, as in _first_days."""
    t = transaction_table.c
    rows = conn.execute(
        select(t.account_id, func.max(t.booking_date))
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            t.excluded_as_duplicate_of.is_(None),
            import_table.c.status == ImportStatus.RECONCILED.value,
        )
        .group_by(t.account_id)
    ).all()
    found: dict[UUID, date] = {account_id: day for account_id, day in rows}
    s = sync_table.c
    synced = conn.execute(
        select(s.account_id, func.max(s.requested_to))
        .outerjoin(import_table, import_table.c.id == s.import_id)
        .where(
            s.requested_to.is_not(None),
            or_(
                s.status == SyncStatus.NOTHING_NEW.value,
                and_(
                    s.status == SyncStatus.DONE.value,
                    import_table.c.status == ImportStatus.RECONCILED.value,
                ),
            ),
        )
        .group_by(s.account_id)
    ).all()
    for account_id, day in synced:
        found[account_id] = max(found.get(account_id, day), day)
    return found


def _own_accounts(conn: Connection) -> dict[str, UUID]:
    """The account each own IBAN belongs to (hex HMAC → account id): an account's own IBAN, a
    credit card's payment IBAN, an exchange's learned deposit IBAN."""
    return _own_accounts_of(_account_ibans(conn))


def _own_accounts_of(rows: Sequence[Row]) -> dict[str, UUID]:
    """_own_accounts from _account_ibans' rows (a later row wins an IBAN two accounts hold)."""
    found: dict[str, UUID] = {}
    for account_id, iban, payment in rows:
        for value in (iban, payment):
            if (key := _hex(value)) is not None:
                found[key] = account_id
    return found


def _own_account_sets(conn: Connection) -> dict[str, set[UUID]]:
    """D162 amended (9): every account each own IBAN belongs to (hex HMAC → account ids), as
    _own_accounts reads them, but keeping all of them: two credit cards can share one payment
    IBAN, and a debit naming it may pay either."""
    return _own_account_sets_of(_account_ibans(conn))


def _own_account_sets_of(rows: Sequence[Row]) -> dict[str, set[UUID]]:
    """_own_account_sets from _account_ibans' rows."""
    found: dict[str, set[UUID]] = {}
    for account_id, iban, payment in rows:
        for value in (iban, payment):
            if (key := _hex(value)) is not None:
                found.setdefault(key, set()).add(account_id)
    return found


def _card_payers(conn: Connection) -> dict[UUID, set[UUID]]:
    """D162 amended (6), 15 Nov 2026: the accounts that pay each credit card, per card account
    id: the other accounts with a line of a reconciled import whose counterparty IBAN is the
    card's confirmed payment IBAN (the card's settlements are debited there). A card whose
    payment IBAN is unknown, or that no line pays yet, is absent."""
    t, card = transaction_table.c, account_table.c
    rows = conn.execute(
        select(card.id, t.account_id)
        .distinct()
        .select_from(transaction_table)
        .join(import_table, import_table.c.id == t.import_id)
        .join(account_table, card.payment_iban_hmac == t.counterparty_iban_hmac)
        .where(
            card.kind == AccountKind.CREDIT_CARD.value,
            t.account_id != card.id,
            t.excluded_as_duplicate_of.is_(None),
            import_table.c.status == ImportStatus.RECONCILED.value,
        )
    ).all()
    found: dict[UUID, set[UUID]] = {}
    for card_id, account_id in rows:
        found.setdefault(card_id, set()).add(account_id)
    return found


def card_payments_after(conn: Connection, cards: dict[UUID, date]) -> dict[UUID, list[date]]:
    """Phase 5 D122 amended (6), 15 Nov 2026: per credit card id in `cards`, the booking days
    of the debits that pay it after the given day (its covered day): lines on another own
    account of a reconciled import, not excluded as a duplicate, with a negative amount,
    naming an IBAN that belongs to the card (_own_account_sets: its payment IBAN, or its own),
    and not a leg of a current link (_linked_ids), whose counterpart is already in the app:
    two cards can share one payment IBAN, and the other card's linked settlements are no use
    of this one. A card no such line pays is absent."""
    if not cards:
        return {}
    named_by = _own_account_sets(conn)
    keys: dict[bytes, set[UUID]] = {}
    for key, accounts in named_by.items():
        for card_id in accounts & cards.keys():
            keys.setdefault(bytes.fromhex(key), set()).add(card_id)
    if not keys:
        return {}
    t = transaction_table.c
    rows = conn.execute(
        select(t.id, t.account_id, t.counterparty_iban_hmac, t.booking_date)
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            t.counterparty_iban_hmac.in_(list(keys)),
            t.amount < 0,
            t.booking_date > min(cards.values()),
            t.excluded_as_duplicate_of.is_(None),
            import_table.c.status == ImportStatus.RECONCILED.value,
        )
    ).all()
    if not rows:
        return {}
    linked = _linked_ids(conn)
    found: dict[UUID, list[date]] = {}
    for line_id, account_id, iban, day in rows:
        if line_id in linked:
            continue
        for card_id in keys[bytes(iban)]:
            if account_id != card_id and day > cards[card_id]:
                found.setdefault(card_id, []).append(day)
    return {card_id: sorted(days) for card_id, days in found.items()}


def _before_every_history(
    leg: _Leg,
    firsts: dict[UUID, date],
    own: dict[str, UUID],
    payers: dict[UUID, set[UUID]] | None = None,
) -> bool:
    """D162 amended (5), 15 Nov 2026 (Luis: „There are some credit card deposits that were not
    directly linked. Can you check?“): a leg without a counterpart and without a same-amount
    line in its by-hand window (±_BY_HAND), whose window ends before the first bank line of
    every other account (_first_days), can have no counterpart yet, so it gets no card: a card
    statement's payment of 2 Dec 2025 while every bank account's lines start in January 2026.
    When no other account holds a line the card stays. An exchange's or depot's movements do
    not count, so an exchange deposit older than every bank line gets no card either. An older
    statement imported later brings the counterpart into reach: the card comes back proposing
    it, and booked as a transfer (matcher, rule or person) it is linked by after_booking.

    Review of D162 amended (5), 15 Nov 2026: a leg that names an own account by its IBAN
    (`own`) is decided by that account alone: the card stays while that account has no bank
    line yet (a debit to the other owner's account, whose statements are not imported), and
    goes only when that account's first line is after the window. Every other account counts
    only for a leg that names none, or an unrelated account with later lines would hide it.

    D162 amended (6), 15 Nov 2026 (Luis: a card payment from before the uploaded bank
    statements): a credit card's line names no IBAN, so it fell to every other account and the
    ±31 days, and a settlement credit of 2 Dec 2025 kept its card while the account paying the
    card starts on 2 Jan 2026 (the household's history starts 1 Jan 2026). Such a leg is
    decided by the accounts that pay the card (_card_payers): no card when every one's first
    line is after the leg's D162 window (transfer_window_end: the latest day its counterpart
    could be booked, the debit up to three banking days after an early credit). A card whose
    payer is unknown keeps the rule above."""
    paying = (payers or {}).get(leg.leg.account_id)
    if leg.leg.account_kind is AccountKind.CREDIT_CARD and paying:
        days = [firsts[a] for a in paying if a in firsts]
        return bool(days) and transfer_window_end(leg.leg.booked_on) < min(days)
    named = leg.leg.counterparty_iban_hmac
    account = None if named is None else own.get(named)
    if account is not None and account != leg.leg.account_id:
        first = firsts.get(account)
        return first is not None and leg.leg.booked_on + _BY_HAND < first
    others = [day for account_id, day in firsts.items() if account_id != leg.leg.account_id]
    return bool(others) and leg.leg.booked_on + _BY_HAND < min(others)


def _after_every_history(
    leg: _Leg,
    lasts: dict[UUID, date],
    named_by: dict[str, set[UUID]],
    payers: dict[UUID, set[UUID]] | None = None,
) -> bool:
    """D162 amended (9), 15 Nov 2026 (Luis: „There are two transfers from the dkb account to the
    Quirion Depot that weren't automatically booked.“), the mirror of _before_every_history: a
    leg without a counterpart and without a same-amount line in its by-hand window gets no card
    while the account it names has no imported history up to the leg's D162 window
    (transfer_window_end): a monthly savings debit of 3 Nov naming a depot whose cash statements
    are imported up to 31 Oct cannot be paired before the November statement is uploaded.

    The accounts are those holding the IBAN the leg names (_own_account_sets, the leg's own
    account left out), or for a credit card's leg, which names no IBAN, the accounts that pay
    the card (_card_payers). No card only when there is at least one such account and every one
    has a history (_last_days) that ends before the window end; an account with no line and no
    sync keeps the card, as in _before_every_history. A leg naming no own account keeps its
    card: an unrelated account whose statements stop would otherwise hide every card. Nothing
    is stored: once the named account's next import reaches the window end without a
    counterpart, the card shows again on the next read."""
    end = transfer_window_end(leg.leg.booked_on)
    paying = (payers or {}).get(leg.leg.account_id)
    if leg.leg.account_kind is AccountKind.CREDIT_CARD and paying:
        accounts = set(paying)
    else:
        named = leg.leg.counterparty_iban_hmac
        accounts = set() if named is None else named_by.get(named, set()) - {leg.leg.account_id}
    if not accounts:
        return False
    days = [lasts.get(account) for account in accounts]
    return all(day is not None and day < end for day in days)


def lines_on_cards(conn: Connection, items: Iterable[InboxItem]) -> set[UUID]:
    """D162 amended (5), 15 Nov 2026 (Luis: „Aren't the zu prüfen the bank lines that are also
    in transfers between own accounts redundant?“): the bank lines among the transfer cards'
    candidates (`items`, transfer_inbox_items) that Zu prüfen lists on their card only, not
    among the lines to allocate. A candidate is one when the card's own button books it and the
    card's leg is surely its counterpart: a line tied to the leg by an own IBAN
    (rebook_on_link: the link re-books it as a transfer), or an exchange deposit credited short
    (deposit_fee: the link books the split) that is the one candidate of a proposed card, so
    it fits the leg inside the three banking days of D162; and the line holds only the model's
    proposal, never a person's or a rule's answer, current or superseded (_free_for_matcher
    with at least one row, so a line holding nothing stays listed for „KI-Vorschläge holen“).
    Review, 15 Nov 2026: a short candidate found by its amount alone on a „Gegenbuchung fehlt“
    card (any private debit up to 2 € above an unlinked deposit, ±31 days) stays listed, or
    unrelated debits were parked behind that card where „Empfänger suchen“ and the Haushalt's
    „N ohne Kategorie“ could not find them. An untied candidate of the same amount is not one
    either: a plain „Verknüpfen“ leaves the model's proposal on it, so it would come back
    after the link. Reads two queries for all such candidate lines together (21 Nov 2026: two
    per line before): their current rows and their held answers."""
    from . import allocations  # allocations imports services that import this module

    ids: list[UUID] = []
    seen: set[UUID] = set()
    for item in items:
        if item.transfer is None:
            continue
        proposed = item.transfer.code is OpenTransferCode.PROPOSED
        for candidate in item.transfer.candidates:
            if candidate.source is not TransferLegSource.TRANSACTION or candidate.id in seen:
                continue
            if not candidate.rebook_on_link and not (
                proposed and candidate.deposit_fee is not None
            ):
                continue
            seen.add(candidate.id)
            ids.append(candidate.id)
    if not ids:
        return set()
    a = allocation_table.c
    current: dict[UUID, list] = {i: [] for i in ids}
    # allocations.current_allocations for every line at once, in its order per line.
    for row in conn.execute(
        select(allocation_table)
        .where(a.transaction_id.in_(ids), a.status != AllocationStatus.SUPERSEDED.value)
        .order_by(a.transaction_id, a.recorded_at, a.id)
    ):
        current[row.transaction_id].append(allocations.to_allocation(row))
    held = _held_answers(conn, ids)
    return {i for i in ids if current[i] and _only_the_models(current[i]) and held[i] is None}


# The cards' texts, German beside English (phase 10 D284); chosen when Zu prüfen is read.
# Phase 7 step 5 (D184): a coin leg's cards. A chain transfer arrives within hours, so its
# window is 48 hours, not three banking days. Review of PR #396 (D184 (1)(a)): when the
# transaction's hash names an own leg of other units no link is offered; one candidate or
# several, said plainly. The wallet's own answer for such a transaction is not built yet
# (docs/phases/phase-7.md §15.1).
TEXTS = i18n.Texts(
    "transfers",
    {
        "link.ambiguous": (
            "Für diese Überweisung zwischen zwei eigenen Konten passen mehrere Gegenbuchungen.",
            "Several matching transactions fit this transfer between own accounts.",
        ),
        "link.proposed": (
            "Diese Buchung passt genau zu einer Buchung auf einem anderen eigenen Konto.",
            "This transaction matches exactly one transaction on another own account.",
        ),
        "deposit": (
            "Diese Einzahlung auf ein eigenes Börsenkonto passt bis auf eine kleine"
            " Einzahlungsgebühr zu einer Buchung auf einem anderen eigenen Konto.",
            "This deposit to an own exchange account matches a transaction on another own"
            " account, apart from a small deposit fee.",
        ),
        "unmatched": (
            "Zu dieser Überweisung zwischen zwei eigenen Konten fehlt nach drei"
            " Bankarbeitstagen die Gegenbuchung auf dem anderen Konto.",
            "After three banking days, the matching transaction on the other account is still"
            " missing for this transfer between own accounts.",
        ),
        "coin.ambiguous": (
            "Für diese Krypto-Überweisung zwischen zwei eigenen Konten passen mehrere"
            " Gegenbuchungen.",
            "Several matching transactions fit this crypto transfer between own accounts.",
        ),
        "coin.proposed": (
            "Diese Krypto-Buchung passt nach Menge und Zeit zu einer Buchung auf einem anderen"
            " eigenen Konto.",
            "This crypto transaction matches a transaction on another own account by amount"
            " and time.",
        ),
        "coin.unmatched": (
            "Zu dieser Krypto-Überweisung zwischen zwei eigenen Konten fehlt nach 48 Stunden"
            " die Gegenbuchung auf dem anderen Konto oder Wallet.",
            "After 48 hours, the matching transaction on the other account or wallet is still"
            " missing for this crypto transfer between own accounts.",
        ),
        "coin.partial": (
            "In derselben Transaktion kam auf dem anderen eigenen Konto eine andere Menge an,"
            " als hier abging: ein Teil der Coins ging an eine andere Adresse. Ein Übertrag"
            " verbindet nur gleich große Mengen, deshalb wird hier nichts verknüpft; die"
            " Transaktion wird auf der Seite der Wallet beantwortet.",
            "In the same transaction a different amount arrived on the other own account than"
            " left here: part of the coins went to another address. A transfer links only"
            " equal amounts, so nothing is linked here; the transaction is answered on the"
            " wallet's page.",
        ),
        "coin.split": (
            "Diese Sendung ging in einer Transaktion an mehrere eigene Empfänge (unten"
            " aufgeführt). Ein Übertrag verbindet eine Sendung mit genau einem gleich großen"
            " Empfang, deshalb ist keine Auswahl richtig; die Transaktion wird auf der Seite"
            " der Wallet beantwortet.",
            "This sending went to several own receipts in one transaction (listed below). A"
            " transfer links one sending with exactly one receipt of the same size, so no"
            " choice is right; the transaction is answered on the wallet's page.",
        ),
        "question.partial": (
            "Wohin gingen die Coins dieser Transaktion?",
            "Where did the coins of this transaction go?",
        ),
        "question.pick": (
            "Welche Buchung ist die Gegenbuchung?",
            "Which transaction is the matching transaction?",
        ),
        "question.import": (
            "Ist das Konto der Gegenbuchung schon importiert?",
            "Has the account of the matching transaction been imported yet?",
        ),
    },
)
_LINK_KEY = {OpenTransferCode.AMBIGUOUS: "ambiguous", OpenTransferCode.PROPOSED: "proposed"}


def _coin_reason(item: OpenTransfer) -> str:
    if item.code is OpenTransferCode.UNMATCHED_LEG:
        return TEXTS("coin.unmatched")
    if item.code is OpenTransferCode.PARTIAL:
        return TEXTS("coin.split" if len(item.candidates) > 1 else "coin.partial")
    return TEXTS(f"coin.{_LINK_KEY[item.code]}")


def transfer_inbox_items(conn: Connection, today: date) -> list[InboxItem]:
    """Zu prüfen's two transfer cards (D162): transfer_link (pick the counterpart) and
    transfer_unmatched (past the window; link one by hand, or import the other account)."""
    items = []
    for item in open_transfers(conn, today):
        unmatched = item.code is OpenTransferCode.UNMATCHED_LEG
        coin = item.leg.amount is None
        items.append(
            InboxItem(
                kind=InboxItemKind.TRANSFER_UNMATCHED if unmatched else InboxItemKind.TRANSFER_LINK,
                created_at=datetime.combine(item.window_end, time(), tzinfo=UTC),
                reasoning=_coin_reason(item)
                if coin
                else TEXTS("unmatched")
                if unmatched
                else TEXTS("deposit")
                if any(c.deposit_fee is not None for c in item.candidates)
                else TEXTS(f"link.{_LINK_KEY[item.code]}"),
                question=TEXTS(
                    "question.partial"
                    if item.code is OpenTransferCode.PARTIAL
                    else "question.pick"
                    if item.candidates
                    else "question.import"
                ),
                transaction_id=item.leg.id
                if item.leg.source is TransferLegSource.TRANSACTION
                else None,
                account_id=item.leg.account_id,
                transfer=item,
            )
        )
    return items


def saving_links(conn: Connection, *, start: date, end: date) -> list[TransferLinkView]:
    """The current links that moved money into a savings, depot or exchange account from a
    current account, with the in-leg booked between the dates (what `saved_in` sums, by the
    in-leg's month). Phase 9's savings plans (services.plan.plan_lines) read a wider range and
    bucket each link by its out-leg's booking month instead."""
    out = []
    for view in list_links(conn, start=start, end=end):
        if not start <= view.in_leg.booked_on <= end:
            continue
        # Each leg's account kind is read with the leg (_line_legs, _movement_legs join the
        # account), so no query per link (21 Nov 2026: 69 account reads per GET /health).
        if view.in_leg.account_kind in _SAVING and view.out_leg.account_kind not in _SAVING:
            out.append(view)
    return out


def saved_in(conn: Connection, *, start: date, end: date) -> Decimal:
    """D162 for the Haushalt screen: what the current links moved into a savings, depot or
    exchange account from a current account, with the in-leg booked between the dates."""
    return sum((v.in_leg.amount for v in saving_links(conn, start=start, end=end)), Decimal("0.00"))


_SAVING = (AccountKind.BANK_SAVINGS, AccountKind.DEPOT, AccountKind.EXCHANGE)


__all__ = [
    "after_booking",
    "is_return_link",
    "own_account_hmacs",
    "link_by_hand",
    "link_leg",
    "lines_on_cards",
    "list_links",
    "open_transfers",
    "relink",
    "relink_all",
    "saved_in",
    "saving_links",
    "transfer_inbox_items",
    "unlink",
]
