"""Transfers between own accounts on a chain (docs/phases/phase-7.md D184, D186 (1), §7); built
in step 5. rules/chain/match_transfer decides; this module reads the coin legs, writes the
`transfer_link` rows of kind chain, bridge and wrap, and computes the cards on read.

A coin leg is a live (not superseded) asset_movement, proposed or confirmed, of a crypto asset
that is not spam:

- on an exchange account (phase 6 D178): kind internal_transfer_out or _in (a withdrawal, a
  deposit a person answered as a transfer); the exchange's fee_quantity, network and hash;
- on a wallet account: in a live movement group of kind transfer, exchange_transfer, wrap or
  bridge, kind internal_transfer_out, _in or uncategorized (a transfer whose other side the
  classifier did not know). A group's fee legs are never a leg.

The asset the matcher compares is the priced_as symbol (a token's price twin, else the
registry's, else its own symbol); an asset with none is never a leg.

relink(ctx) runs the matcher over every open sending leg (not in a current link) with every
open receiving leg as a candidate, the workspace's chain_pair rows (D191) as extra bridges and
wraps. A linked result with no rival send is written (matched_by matcher) only when its units
fit and:

1. both legs are confirmed, and a bridge or wrap is one the workspace's chain_pair holds
   (D186 (1); a pair the registry alone knows is always a person's pick); or
2. the two legs share their transaction hash, the link is of kind chain, and each proposed leg
   may be confirmed by code (D186 (1): an exchange's withdrawal by hash, own address to own
   address): an exchange row with a transfer kind, or a leg whose group is a transfer or
   exchange_transfer that waits only for its link (routed routing.no_match, so never a
   wallet's first sync, which waits for a person) and holds no leg but the pair's, its fees
   and the rent of a token account the send opened. The link confirms them in the same
   transaction (the group, its legs and its fees, each with its event; a group with its rent
   leg stays proposed for a person), so the sync's `linked` (which the classifiers leave
   false, §15.1) is filled here, after the fact.

The units fit (D184 (1)(a), review of PR #396) for a pair of kind chain when the sent units
less the exchange's fee_quantity equal the received units within the chain's dust, also under
one hash: a hash names the transaction, not what each own address got of it. A bridge's units
are the matcher's fee-share fit, a wrap's its unit fit.

Anything else (an amount-only fit, a bridge, a wrap, a first sync, an ambiguous fit) waits for
a person on a card of GET /transfers/open: transfer.proposed with the one fit,
transfer.ambiguous with every fit, transfer.partial when the transaction's hash names own legs
of other units (part of the coins went elsewhere, or one send paid several own receipts: they
are shown, no pick is offered, and the wallet's answer for it is not built yet), and
transfer.unmatched_leg once a transfer leg's 48 hours have passed and a wallet that could hold
its counterpart exists. The person's pick (POST /transfers, link_by_hand) confirms both legs
as the person's answer and writes the link (matched_by user), only for a pair the matcher
itself fits, of the kind it reads, whose units fit (else 422 transfer.units_differ).

Currentness is decided on read, as phase 6's: services.transfers._current_links counts a chain,
bridge or wrap link only while both legs are live, whoever wrote it, so re-booking a leg
(superseding it) ends the link (D184 (5)) and the leg's successor is open again. Nothing here
sets transfer_link.superseded_by (services.transfers.unlink does, for a person's replacement).
What a matcher's link confirmed is recorded on its transfer.linked event; once the link is no
longer current, the next relink returns it to proposed (_reopen: proposed copies that supersede
the rows, never a person's answer), so it waits for a person again (review of PR #396).

Locks: the workspace's transfer lock (services.transfers._lock); a movement or group row is
locked with SKIP LOCKED by the matcher (a row a person is answering right now is left for the
next run) and with NOWAIT on a link by hand (409), so this module never waits for a row while
it holds the transfer lock. It writes no account row.

Privacy (D179): events carry ids only (transfer.linked, movement.confirmed,
movement_group.confirmed); the log carries counts; a card names the account, the date, the
quantity and the symbol, never an address or a hash.
"""

import hashlib
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, and_, insert, select, update
from sqlalchemy.orm import aliased

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import chain_pair as pair_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.rules.chain.match_transfer import (
    WINDOW,
    ChainLeg,
    ChainMatch,
    _contract,
    _joins,
    _units_fit,
    match,
)
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.errors import TRANSFER_LEGS_INVALID, TRANSFER_UNITS_DIFFER
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.rules.review import DETERMINISTIC, NO_MATCH
from hausbuch.schemas.allocation import AllocationCategory, TransferLegSource
from hausbuch.schemas.chain import (
    BridgePair,
    Chain,
    ChainSourceKind,
    GroupKind,
    GroupStatus,
    WrapPair,
)
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import (
    EventType,
    MovementGroupRecorded,
    MovementRecorded,
    MovementSuperseded,
    TransferLinked,
)
from hausbuch.schemas.household import (
    AssetKind,
    MovementKind,
    MovementSource,
    MovementStatus,
    OpenTransfer,
    OpenTransferCode,
    TransferLegView,
    TransferLink,
    TransferLinkCreate,
    TransferLinkKind,
    TransferMatchedBy,
)

from ..context import Actor, CommandContext, new_command
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..events import append_event

logger = logging.getLogger("hausbuch.services.chain.transfers")

BERLIN = ZoneInfo("Europe/Berlin")
CHAIN_LINK_KINDS = frozenset(
    {TransferLinkKind.CHAIN, TransferLinkKind.BRIDGE, TransferLinkKind.WRAP}
)
_GROUP_KINDS = frozenset(
    {GroupKind.TRANSFER, GroupKind.EXCHANGE_TRANSFER, GroupKind.WRAP, GroupKind.BRIDGE}
)
# A group whose proposed legs a hash link may confirm by code (D186 (1)): a wrap waits for its
# decoder and a bridge for a learned pair, never for this module.
_CONFIRMABLE_GROUPS = frozenset({GroupKind.TRANSFER, GroupKind.EXCHANGE_TRANSFER})
_TRANSFER_KINDS = frozenset({MovementKind.INTERNAL_TRANSFER_OUT, MovementKind.INTERNAL_TRANSFER_IN})
_CHAIN_KINDS = _TRANSFER_KINDS | {MovementKind.UNCATEGORIZED}
_LIVE = (MovementStatus.PROPOSED.value, MovementStatus.CONFIRMED.value)


# --- Reading legs ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Row:
    """One coin leg with what the cards show of it."""

    id: UUID
    account_id: UUID
    account_label: str
    account_kind: AccountKind
    asset_id: UUID
    symbol: str  # what the card shows
    priced: str  # what the matcher compares (D184: the priced_as symbol)
    chain: Chain | None
    contract: str | None
    decimals: int
    quantity: Decimal
    fee_quantity: Decimal | None
    occurred_at: datetime
    tx_hash: str | None
    network: str | None
    kind: MovementKind
    status: MovementStatus
    group_id: UUID | None
    group_status: GroupStatus | None
    group_kind: GroupKind | None
    group_question: bool

    @property
    def sends(self) -> bool:
        return self.quantity < 0

    @property
    def confirmed(self) -> bool:
        return self.status is MovementStatus.CONFIRMED

    def leg(self) -> ChainLeg:
        return ChainLeg(
            ref=str(self.id),
            account=str(self.account_id),
            asset=self.priced,
            units=abs(self.quantity),
            at=self.occurred_at,
            chain=self.chain,
            contract=self.contract,
            decimals=self.decimals if self.chain is not None else None,
            fee=self.fee_quantity or Decimal(0),
            tx_hash=self.tx_hash,
            network=self.network,
        )

    def view(self, link_kind: TransferLinkKind | None = None) -> TransferLegView:
        transfer = self.kind in _TRANSFER_KINDS
        return TransferLegView(
            source=TransferLegSource.MOVEMENT,
            id=self.id,
            account_id=self.account_id,
            account_label=self.account_label,
            booked_on=self.occurred_at.astimezone(BERLIN).date(),
            amount=None,
            account_kind=self.account_kind,
            booked_category=(
                (
                    AllocationCategory.INTERNAL_TRANSFER_OUT
                    if self.sends
                    else AllocationCategory.INTERNAL_TRANSFER_IN
                )
                if transfer
                else None
            ),
            booked_as_transfer=transfer,
            quantity=self.quantity,
            asset_symbol=self.symbol,
            chain=None if self.chain is None else self.chain.value,
            link_kind=link_kind,
        )


def _priced(symbol: str | None, chain: str | None, contract: str | None) -> str | None:
    """The registry's price twin of a chain token (a WETH prices as ETH), else None."""
    if chain is None or contract is None:
        return None
    try:
        token = asset_registry().token(Chain(chain), contract)
    except ValueError:
        return None
    return None if token is None else asset_registry().priced_as(token).symbol


def _read_legs(conn: Connection, *where) -> list[_Row]:  # noqa: ANN002
    m, a, acc, g = movement_table.c, asset_table.c, account_table.c, group_table.c
    twin = aliased(asset_table)
    rows = conn.execute(
        select(
            m.id,
            m.account_id,
            acc.label.label("account_label"),
            acc.kind.label("account_kind"),
            m.asset_id,
            a.symbol,
            a.name,
            a.chain,
            a.contract_address,
            a.decimals,
            twin.c.symbol.label("twin_symbol"),
            m.quantity,
            m.fee_quantity,
            m.occurred_at,
            m.tx_hash,
            m.network,
            m.kind,
            m.status,
            m.group_id,
            g.status.label("group_status"),
            g.kind.label("group_kind"),
            g.question.label("group_question"),
            g.superseded_by.label("group_superseded_by"),
        )
        .select_from(
            movement_table.join(asset_table, a.id == m.asset_id)
            .join(account_table, acc.id == m.account_id)
            .outerjoin(twin, twin.c.id == a.priced_as_asset_id)
            .outerjoin(group_table, g.id == m.group_id)
        )
        .where(
            m.superseded_by.is_(None),
            m.status.in_(_LIVE),
            m.quantity != 0,
            a.kind == AssetKind.CRYPTO.value,
            a.spam.is_(False),
            acc.kind.in_([AccountKind.EXCHANGE.value, AccountKind.WALLET.value]),
            m.kind.in_([k.value for k in _CHAIN_KINDS]),
            *where,
        )
        .order_by(m.occurred_at, m.id)
    ).all()
    legs = []
    for r in rows:
        kind = MovementKind(r.kind)
        exchange = AccountKind(r.account_kind) is AccountKind.EXCHANGE
        if exchange:
            if kind not in _TRANSFER_KINDS:
                continue
        elif (
            r.group_id is None
            or r.group_superseded_by is not None
            or GroupKind(r.group_kind) not in _GROUP_KINDS
        ):
            continue
        priced = r.twin_symbol or _priced(r.symbol, r.chain, r.contract_address) or r.symbol
        if priced is None:
            continue  # a token without a symbol and no price twin: nothing to compare
        legs.append(
            _Row(
                id=r.id,
                account_id=r.account_id,
                account_label=r.account_label,
                account_kind=AccountKind(r.account_kind),
                asset_id=r.asset_id,
                symbol=r.symbol or r.name,
                priced=priced,
                chain=None if r.chain is None else Chain(r.chain),
                contract=r.contract_address,
                decimals=r.decimals,
                quantity=r.quantity,
                fee_quantity=r.fee_quantity,
                occurred_at=r.occurred_at,
                tx_hash=r.tx_hash,
                network=r.network,
                kind=kind,
                status=MovementStatus(r.status),
                group_id=r.group_id,
                group_status=None if r.group_status is None else GroupStatus(r.group_status),
                group_kind=None if r.group_kind is None else GroupKind(r.group_kind),
                group_question=r.group_question is not None,
            )
        )
    return legs


def _pairs(conn: Connection) -> tuple[list[BridgePair], list[tuple[Chain, WrapPair]]]:
    """The workspace's confirmed chain_pair rows (D191) as the matcher's extras. A bridge row
    without a max_fee_share, or a wrap whose wrapped side has no contract, is left out."""
    p = pair_table.c
    src, dst = aliased(asset_table), aliased(asset_table)
    rows = conn.execute(
        select(
            p.kind,
            p.from_chain,
            p.to_chain,
            p.max_fee_share,
            src.c.contract_address.label("from_contract"),
            src.c.symbol.label("from_symbol"),
            dst.c.contract_address.label("to_contract"),
        )
        .select_from(
            pair_table.join(src, src.c.id == p.from_asset_id).join(dst, dst.c.id == p.to_asset_id)
        )
        .where(p.superseded_by.is_(None))
        .order_by(p.created_at, p.id)
    ).all()
    bridges: list[BridgePair] = []
    wraps: list[tuple[Chain, WrapPair]] = []
    for r in rows:
        try:
            if r.kind == TransferLinkKind.BRIDGE.value and r.max_fee_share is not None:
                bridges.append(
                    BridgePair(
                        from_chain=Chain(r.from_chain),
                        from_contract=r.from_contract,
                        to_chain=Chain(r.to_chain),
                        to_contract=r.to_contract,
                        max_fee_share=r.max_fee_share,
                    )
                )
            elif r.kind == TransferLinkKind.WRAP.value and r.to_contract and r.from_symbol:
                wraps.append(
                    (Chain(r.from_chain), WrapPair(native=r.from_symbol, wrapped=r.to_contract))
                )
        except ValueError:  # a row the registry's shapes refuse: no pair, never a failure
            continue
    return bridges, wraps


@dataclass
class _World:
    """Every open leg and the workspace's pairs, read once per relink or card read."""

    legs: list[_Row]
    linked: set[UUID]
    bridges: list[BridgePair]
    wraps: list[tuple[Chain, WrapPair]]

    @classmethod
    def read(cls, conn: Connection) -> "_World":
        from ..transfers import _linked_ids  # transfers imports this module lazily

        bridges, wraps = _pairs(conn)
        return cls(_read_legs(conn), _linked_ids(conn), bridges, wraps)

    def open(self) -> list[_Row]:
        return [r for r in self.legs if r.id not in self.linked]

    def match(self, leg: _Row, candidates: Sequence[_Row]) -> ChainMatch:
        return match(
            leg.leg(),
            [c.leg() for c in candidates],
            extra_bridges=self.bridges,
            extra_wraps=self.wraps,
        )

    def receivers(self, leg: _Row) -> list[_Row]:
        return [r for r in self.open() if not r.sends and r.id != leg.id]

    def rivals(self, leg: _Row, other: _Row) -> list[_Row]:
        """The other open sending legs `other` would be the one fit of too: a receiving leg two
        sends fit is never linked by the matcher (the card asks which)."""
        return [
            s
            for s in self.open()
            if s.sends and s.id != leg.id and self.match(s, [other]).result == "linked"
        ]


def _same_hash(a: _Row, b: _Row) -> bool:
    from hausbuch.rules.chain.match_transfer import _same_hash as same

    return same(a.leg(), b.leg())


def _fits(out: _Row, in_: _Row, kind: TransferLinkKind | None) -> bool:
    """D184 (1)(a) for a pair of kind chain: the sent units less the exchange's fee_quantity
    against the received units, within the chain's dust (match_transfer._units_fit, the one
    formula). The matcher compares units only without a hash: a hash names the transaction,
    not what each own address got of it, so 1.0 sent and 0.4 received under one hash (0.6 to a
    third party in the same transaction, or a second own receipt) is no transfer of 1.0 (review
    of PR #396). A bridge's units are the matcher's fee-share fit, a wrap's its unit fit."""
    return kind is not TransferLinkKind.CHAIN or _units_fit(out.leg(), in_.leg())


def _learned(world: "_World", out: _Row, in_: _Row, kind: TransferLinkKind) -> bool:
    """D186 (1): a bridge or a wrap is linked by code only once the workspace's chain_pair
    (D191) holds its pair, even with both legs confirmed; a pair the registry alone knows waits
    for a person's pick (review of PR #396). chain_pair is not written yet (§15.1), so today
    this is never true."""
    sent = (out.chain, _contract(out.contract))
    got = (in_.chain, _contract(in_.contract))
    if kind is TransferLinkKind.BRIDGE:
        return any(_joins(p, sent, got) for p in world.bridges)  # type: ignore[arg-type]
    if kind is TransferLinkKind.WRAP:
        return any(
            chain is out.chain
            and w.native == out.priced
            and {sent[1], got[1]} == {None, _contract(w.wrapped)}
            for chain, w in world.wraps
        )
    return True


# --- Confirming legs ------------------------------------------------------------------------


def _group_reason(conn: Connection, group_id: UUID) -> str | None:
    """The routing reason the group's movement_group.proposed event recorded."""
    e = event_table.c
    return conn.execute(
        select(e.payload["reason"].astext)
        .where(e.entity_id == group_id, e.type == EventType.MOVEMENT_GROUP_PROPOSED.value)
        .order_by(e.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _group_rest(conn: Connection, group_id: UUID) -> list:
    """The group's live movements that are not a fee: id, status, kind, index."""
    m = movement_table.c
    return conn.execute(
        select(m.id, m.status, m.kind, m.index).where(
            m.group_id == group_id,
            m.superseded_by.is_(None),
            m.kind != MovementKind.FEE.value,
        )
    ).all()


def _code_may_confirm(conn: Connection, leg: _Row, pair: set[UUID]) -> bool:
    """D186 (1): a proposed leg the matcher may confirm with a hash link."""
    if leg.confirmed:
        return True
    if leg.account_kind is AccountKind.EXCHANGE:
        return leg.kind in _TRANSFER_KINDS
    if (
        leg.group_id is None
        or leg.group_kind not in _CONFIRMABLE_GROUPS
        or leg.group_question
        or leg.group_status is not GroupStatus.PROPOSED
    ):
        return False
    if _group_reason(conn, leg.group_id) != NO_MATCH:
        return False  # a first sync (or anything but a missing link) waits for a person
    return {r.id for r in _group_rest(conn, leg.group_id) if not _rent(r)} <= pair


def _rent(row) -> bool:  # noqa: ANN001 - a _group_rest row
    """The SOL rent an account opener paid into a new own token account (rules/chain/solana:
    an airdrop leg of index `rent`): an own send that creates the receiving wallet's token
    account brings one beside the token. It does not keep the hash link from confirming the
    pair, and it is not confirmed by it: it stays proposed, and its group with it, for a
    person on the wallet's page (review of PR #396)."""
    return row.index == "rent" and row.kind == MovementKind.AIRDROP.value


def _lock_rows(ctx: CommandContext, legs: Iterable[_Row], *, wait_for_none: bool) -> bool:
    """Lock the legs' movement rows and their groups' rows. wait_for_none: SKIP LOCKED (False
    when any row is held by another transaction); else NOWAIT (ConflictError)."""
    m, g = movement_table.c, group_table.c
    ids = sorted({leg.id for leg in legs}, key=str)
    groups = sorted({leg.group_id for leg in legs if leg.group_id is not None}, key=str)
    if wait_for_none:
        got = ctx.conn.execute(
            select(m.id).where(m.id.in_(ids)).with_for_update(skip_locked=True)
        ).all()
        got_groups = (
            ctx.conn.execute(
                select(g.id).where(g.id.in_(groups)).with_for_update(skip_locked=True)
            ).all()
            if groups
            else []
        )
        return len(got) == len(ids) and len(got_groups) == len(groups)
    from sqlalchemy.exc import OperationalError

    try:
        ctx.conn.execute(select(m.id).where(m.id.in_(ids)).with_for_update(nowait=True)).all()
        if groups:
            ctx.conn.execute(
                select(g.id).where(g.id.in_(groups)).with_for_update(nowait=True)
            ).all()
    except OperationalError:
        raise ConflictError("a leg of this transfer is being answered right now") from None
    return True


def _still(conn: Connection, legs: Iterable[_Row]) -> bool:
    """Every leg still live, as read (nothing superseded or answered since)."""
    m = movement_table.c
    ids = [leg.id for leg in legs]
    found = dict(
        conn.execute(select(m.id, m.status).where(m.id.in_(ids), m.superseded_by.is_(None))).all()
    )
    return all(found.get(leg.id) == leg.status.value for leg in legs)


def _confirm_movement(ctx: CommandContext, movement_id: UUID) -> bool:
    m = movement_table.c
    row = ctx.conn.execute(
        update(movement_table)
        .where(m.id == movement_id, m.status == MovementStatus.PROPOSED.value)
        .values(status=MovementStatus.CONFIRMED.value)
        .returning(m.account_id, m.asset_id, m.kind, m.source, m.document_id)
    ).one_or_none()
    if row is None:
        return False
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED,
        movement_id,
        MovementRecorded(
            account_id=row.account_id,
            asset_id=row.asset_id,
            kind=MovementKind(row.kind),
            source=MovementSource(row.source),
            document_id=row.document_id,
        ),
    )
    return True


@dataclass
class _Confirmed:
    """What a matcher's link confirmed, recorded on its transfer.linked event (review of
    PR #396), so _reopen can return it to proposed once the link is no longer current."""

    movements: list[UUID] = field(default_factory=list)
    groups: list[UUID] = field(default_factory=list)


def _confirm(
    ctx: CommandContext, leg: _Row, pair: set[UUID], done: _Confirmed | None = None
) -> int:
    """The leg confirmed; on a chain, its group too once no leg of it but the pair's waits
    (its fee legs with it: the fee is a fact of the transaction); a group asking a question,
    or with another leg still waiting, stays proposed. The count of movements confirmed;
    `done` collects their ids and the group's."""
    done = done if done is not None else _Confirmed()
    count = 0
    if not leg.confirmed and _confirm_movement(ctx, leg.id):
        done.movements.append(leg.id)
        count += 1
    if (
        leg.group_id is None or leg.group_status is not GroupStatus.PROPOSED or leg.group_question
    ):  # a group asking a question (a change address) waits for its answer
        return count
    rest = _group_rest(ctx.conn, leg.group_id)
    if any(r.status != MovementStatus.CONFIRMED.value and r.id not in pair for r in rest):
        return count  # another leg of the transaction still waits: the group waits with it
    m, g = movement_table.c, group_table.c
    fees = ctx.conn.execute(
        select(m.id).where(
            m.group_id == leg.group_id,
            m.superseded_by.is_(None),
            m.kind == MovementKind.FEE.value,
            m.status == MovementStatus.PROPOSED.value,
        )
    ).scalars()
    for fee in list(fees):
        if _confirm_movement(ctx, fee):
            done.movements.append(fee)
            count += 1
    group = ctx.conn.execute(
        update(group_table)
        .where(g.id == leg.group_id, g.status == GroupStatus.PROPOSED.value)
        .values(status=GroupStatus.CONFIRMED.value)
        .returning(g.account_id, g.chain, g.kind)
    ).one_or_none()
    if group is not None:
        done.groups.append(leg.group_id)
        append_event(
            ctx,
            EventType.MOVEMENT_GROUP_CONFIRMED,
            leg.group_id,
            MovementGroupRecorded(
                account_id=group.account_id,
                chain=group.chain,
                kind=group.kind,
                reason=DETERMINISTIC,
            ),
        )
    return count


# --- Writing links --------------------------------------------------------------------------


def _write(
    ctx: CommandContext,
    out: _Row,
    in_: _Row,
    kind: TransferLinkKind,
    matched_by: TransferMatchedBy,
    confirmed: _Confirmed | None = None,
) -> TransferLink:
    """One transfer_link row of two coin movements and its transfer.linked event (ids only),
    as services.transfers._write writes a EUR link, without its bank-line hooks; `confirmed`
    what the matcher's link confirmed (never a person's: that was their answer)."""
    legs = {"out_movement_id": out.id, "in_movement_id": in_.id}
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
        TransferLinked(
            kind=kind,
            matched_by=matched_by,
            confirmed_movements=confirmed.movements if confirmed else [],
            confirmed_groups=confirmed.groups if confirmed else [],
            **legs,
        ),
    )
    return link


# --- Reopening what a dead link confirmed (review of PR #396) -------------------------------


def _lock_ids(ctx: CommandContext, movement_ids: Iterable[UUID], group_ids: Iterable[UUID]) -> bool:
    """_lock_rows' order by ids, SKIP LOCKED: False when any row is held elsewhere."""
    m, g = movement_table.c, group_table.c
    ids, groups = sorted(set(movement_ids), key=str), sorted(set(group_ids), key=str)
    got = (
        ctx.conn.execute(select(m.id).where(m.id.in_(ids)).with_for_update(skip_locked=True)).all()
        if ids
        else []
    )
    got_groups = (
        ctx.conn.execute(
            select(g.id).where(g.id.in_(groups)).with_for_update(skip_locked=True)
        ).all()
        if groups
        else []
    )
    return len(got) == len(ids) and len(got_groups) == len(groups)


def _reopened_hash(old: str, new_id: UUID) -> str:
    """A chain row's copy needs its own movement_hash (0041's unique index spans superseded
    rows, and source chain requires one). The superseded original keeps the sync's hash, and
    sync._stored_hashes reads every row of the account, superseded ones too, so a re-sync of
    the transaction still finds it and writes nothing new."""
    return hashlib.sha256(f"{old}:reopened:{new_id}".encode()).hexdigest()


def _copy_movement(
    ctx: CommandContext, old, *, group_id: UUID | None, status: MovementStatus
) -> UUID:  # noqa: ANN001
    """A copy of the live row `old` with `status` in `group_id` that supersedes it, with its
    events: movement.superseded on the old row, movement.proposed (or .confirmed) on the
    new. A chain row gets a hash of its own (_reopened_hash); an exchange's row none, as
    services.exchange.imports._supersede writes its corrections."""
    m = movement_table.c
    new_id = uuid4()
    skip = {"id", "superseded_by", "created_at", "movement_hash", "status", "group_id"}
    values = {c.name: getattr(old, c.name) for c in movement_table.c if c.name not in skip}
    chain = old.source == MovementSource.CHAIN.value and old.movement_hash is not None
    ctx.conn.execute(
        insert(movement_table).values(
            id=new_id,
            movement_hash=_reopened_hash(old.movement_hash, new_id) if chain else None,
            status=status.value,
            group_id=group_id,
            created_at=ctx.now,
            **values,
        )
    )
    ctx.conn.execute(update(movement_table).where(m.id == old.id).values(superseded_by=new_id))
    append_event(
        ctx, EventType.MOVEMENT_SUPERSEDED, old.id, MovementSuperseded(superseded_by=new_id)
    )
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED
        if status is MovementStatus.CONFIRMED
        else EventType.MOVEMENT_PROPOSED,
        new_id,
        MovementRecorded(
            account_id=old.account_id,
            asset_id=old.asset_id,
            kind=MovementKind(old.kind),
            source=MovementSource(old.source),
            document_id=old.document_id,
        ),
    )
    return new_id


def _reopen_side(
    ctx: CommandContext,
    side_id: UUID,
    movements: set[UUID],
    groups: set[UUID],
    linked: set[UUID],
) -> int:
    """One leg's side of a dead matcher link: the movements of the leg's transaction the
    link confirmed back to proposed, and the group with them when the link confirmed it and no
    leg of it is in a current link. Nothing when the leg was answered by a person (superseded)
    or is in a current link again. The count of movements reopened."""
    m, g = movement_table.c, group_table.c
    side = ctx.conn.execute(
        select(movement_table).where(m.id == side_id, m.superseded_by.is_(None))
    ).one_or_none()
    if side is None or side.id in linked:
        return 0
    same = m.id == side.id if side.group_id is None else m.group_id == side.group_id
    rows = ctx.conn.execute(
        select(movement_table).where(same, m.superseded_by.is_(None)).order_by(m.index, m.id)
    ).all()
    reopen = {
        r.id
        for r in rows
        if r.id in movements and r.id not in linked and r.status == MovementStatus.CONFIRMED.value
    }
    group = None
    if side.group_id in groups:
        group = ctx.conn.execute(
            select(group_table).where(
                g.id == side.group_id,
                g.superseded_by.is_(None),
                g.status == GroupStatus.CONFIRMED.value,
            )
        ).one_or_none()
    if group is not None and any(r.id in linked for r in rows):
        group = None  # a leg of it is in another current link: only the legs, in place
    if not reopen and group is None:
        return 0
    copied = [r for r in rows if group is not None or r.id in reopen]
    if not _lock_ids(ctx, [r.id for r in copied], [group.id] if group is not None else []):
        return 0  # a person is answering it right now: the next relink
    still = {
        r.id: r.status
        for r in ctx.conn.execute(
            select(m.id, m.status).where(
                m.id.in_([r.id for r in copied]), m.superseded_by.is_(None)
            )
        )
    }
    if any(still.get(r.id) != r.status for r in copied):
        return 0  # answered meanwhile
    group_id = side.group_id
    if group is not None:
        group_id = ctx.conn.execute(
            insert(group_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=group.account_id,
                chain=group.chain,
                tx_hash=group.tx_hash,
                occurred_at=group.occurred_at,
                kind=group.kind,
                decoder=group.decoder,
                status=GroupStatus.PROPOSED.value,
                question=group.question,
                created_at=ctx.now,
            )
            .returning(g.id)
        ).scalar_one()
        ctx.conn.execute(update(group_table).where(g.id == group.id).values(superseded_by=group_id))
        append_event(
            ctx,
            EventType.MOVEMENT_GROUP_PROPOSED,
            group_id,
            MovementGroupRecorded(
                account_id=group.account_id, chain=group.chain, kind=group.kind, reason=NO_MATCH
            ),
        )
    for r in copied:
        status = MovementStatus.PROPOSED if r.id in reopen else MovementStatus(r.status)
        _copy_movement(ctx, r, group_id=group_id, status=status)
    return len(reopen)


def _reopen(ctx: CommandContext) -> int:
    """Review of PR #396: what a matcher's coin link confirmed goes back to proposed once the
    link is no longer current (a person re-booked one leg, or replaced the link), so it shows
    again as a card or among the wallet's open groups; never what a person confirmed or
    answered (their rows are not in the link's record, or are superseded already). Append-only:
    each row is superseded by a proposed copy (_copy_movement), a group the link confirmed by a
    proposed copy routed routing.no_match, as a sync would have written it. Idempotent: the
    recorded rows are superseded afterwards. Each run reads every dead matcher coin link; their
    number only grows (§15.1)."""
    from ..transfers import _current_links, _leg_ids

    c, e = link_table.c, event_table.c
    current = _current_links(ctx.conn)
    current_ids = {r.id for r in current}
    linked = {i for r in current for i in _leg_ids(r)}
    dead = [
        r
        for r in ctx.conn.execute(
            select(c.id, c.out_movement_id, c.in_movement_id).where(
                c.matched_by == TransferMatchedBy.MATCHER.value,
                c.kind.in_([k.value for k in CHAIN_LINK_KINDS]),
            )
        ).all()
        if r.id not in current_ids
    ]
    if not dead:
        return 0
    payloads = dict(
        ctx.conn.execute(
            select(e.entity_id, e.payload).where(
                e.type == EventType.TRANSFER_LINKED.value,
                e.entity_id.in_([r.id for r in dead]),
            )
        ).all()
    )
    count = 0
    for link in dead:
        payload = payloads.get(link.id) or {}
        movements = {UUID(i) for i in payload.get("confirmed_movements") or []}
        groups = {UUID(i) for i in payload.get("confirmed_groups") or []}
        if not movements and not groups:
            continue  # it confirmed nothing (both legs were confirmed), or a link before this
        for side in (link.out_movement_id, link.in_movement_id):
            count += _reopen_side(ctx, side, movements, groups, linked)
    return count


@dataclass
class RelinkResult:
    linked: int = 0
    confirmed: int = 0  # movements the links confirmed (D186 (1))
    skipped_locked: int = 0  # a fit left for the next run: a person was answering a leg
    busy: bool = False  # relink(wait=False) found the transfer lock taken: nothing done
    reopened: int = 0  # movements a dead matcher link had confirmed, back to proposed


def relink(ctx: CommandContext, *, wait: bool = True) -> RelinkResult:
    """Every open coin leg of the workspace linked where the module docstring says; the
    counts. Idempotent: a second run finds every written pair linked.

    wait=False (the hook after a person's answer on an exchange's movement, which holds that
    movement's row and not the exchange's account row, so it may not wait for the transfer
    lock: services.transfers._lock's order): when another linker holds the lock nothing is
    done and `busy` says so; the next sync or import links what is left."""
    from ..transfers import _lock, _try_lock

    if wait:
        _lock(ctx)
    elif not _try_lock(ctx):
        return RelinkResult(busy=True)
    result = RelinkResult(reopened=_reopen(ctx))
    world = _World.read(ctx.conn)
    for leg in [r for r in world.open() if r.sends]:
        if leg.id in world.linked:
            continue
        found = world.match(leg, world.receivers(leg))
        if found.result != "linked" or found.counterpart is None or found.link is None:
            continue
        other = next(r for r in world.legs if str(r.id) == found.counterpart.ref)
        if world.rivals(leg, other):
            continue  # two sends fit one receipt: the card asks which
        if not _fits(leg, other, found.link):
            continue  # part of the coins went elsewhere: a transfer.partial card
        if found.link is not TransferLinkKind.CHAIN and not _learned(world, leg, other, found.link):
            continue  # a bridge or wrap the workspace has not confirmed: a person's pick
        pair = {leg.id, other.id}
        if not (leg.confirmed and other.confirmed):
            if found.link is not TransferLinkKind.CHAIN or not _same_hash(leg, other):
                continue  # an amount-only fit, a bridge or a wrap waits for a person
            if not (
                _code_may_confirm(ctx.conn, leg, pair) and _code_may_confirm(ctx.conn, other, pair)
            ):
                continue
        if not _lock_rows(ctx, (leg, other), wait_for_none=True) or not _still(
            ctx.conn, (leg, other)
        ):
            result.skipped_locked += 1
            continue
        done = _Confirmed()
        result.confirmed += _confirm(ctx, leg, pair, done) + _confirm(ctx, other, pair, done)
        _write(ctx, leg, other, found.link, TransferMatchedBy.MATCHER, done)
        world.linked |= pair
        result.linked += 1
    if result.linked or result.skipped_locked or result.reopened:
        logger.info(
            "chain transfers relinked",
            extra={
                "fields": {
                    "event": "chain_relink",
                    "linked": result.linked,
                    "confirmed": result.confirmed,
                    "skipped_locked": result.skipped_locked,
                    "reopened": result.reopened,
                }
            },
        )
    return result


def relink_after_answer(ctx: CommandContext) -> None:
    """The hook after a person confirms or answers an exchange's coin movement: relink in the
    caller's transaction, without waiting for the transfer lock (relink's wait=False)."""
    relink(ctx, wait=False)


def relink_after_sync(
    engine: Engine, workspace_id: UUID, *, actor: Actor, clock: Callable[[], datetime]
) -> None:
    """The hook after a wallet sync job's addresses ran (services.chain.sync.sync_wallets): one
    relink in its own transaction, so the linker never fails a sync; a failure is logged by
    its class name and the next run tries again."""
    try:
        with workspace_transaction(engine, workspace_id) as conn:
            relink(new_command(conn, workspace_id, actor, now=clock()))
    except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
        logger.error(
            "chain transfers not relinked",
            extra={"fields": {"event": "chain_relink_failed", "exc_type": type(exc).__name__}},
        )


# --- A link by hand -------------------------------------------------------------------------


def is_coin_link(conn: Connection, body: TransferLinkCreate) -> bool:
    """Whether POST /transfers names two coin movements (this module's), not EUR legs."""
    if body.out_movement_id is None or body.in_movement_id is None:
        return False
    m, a = movement_table.c, asset_table.c
    kinds = conn.execute(
        select(a.kind)
        .select_from(movement_table.join(asset_table, a.id == m.asset_id))
        .where(m.id.in_([body.out_movement_id, body.in_movement_id]))
    ).scalars()
    return any(k == AssetKind.CRYPTO.value for k in kinds)


def link_by_hand(
    ctx: CommandContext, body: TransferLinkCreate, *, freed: frozenset[UUID] = frozenset()
) -> TransferLink:
    """POST /transfers for two coin legs (services.transfers.link_by_hand calls it after its
    locks): the person's pick on a transfer.proposed or transfer.ambiguous card. 404 for a leg
    that is no open coin leg; the pair linked already returns its link; 409 when a leg is in
    another link or being answered right now; 422 transfer.legs_invalid when the matcher does
    not fit the pair or reads another kind than the body names. Both legs are confirmed by the
    person's answer (their groups too, once nothing else of them waits), then linked
    (matched_by user).

    freed: the legs of the link services.transfers.unlink replaces (that link still current
    while the replacement is written): counted as free, and a replacement of the same pair is
    written anew rather than returned."""
    from ..transfers import _link_of

    assert body.out_movement_id is not None and body.in_movement_id is not None
    world = _World.read(ctx.conn)
    by_id = {r.id: r for r in world.legs}
    out, in_ = by_id.get(body.out_movement_id), by_id.get(body.in_movement_id)
    if out is None:
        raise NotFoundError("movement", body.out_movement_id)
    if in_ is None:
        raise NotFoundError("movement", body.in_movement_id)
    already = None if freed else _link_of(ctx.conn, out.id, in_.id)
    if already is not None:
        return already
    linked = world.linked - freed
    if out.id in linked or in_.id in linked:
        raise ConflictError("a leg of this transfer is linked already")
    found = world.match(out, [in_]) if out.sends and not in_.sends else None
    if found is None or found.result != "linked" or found.link is not body.kind:
        raise RuleViolationError(
            [TRANSFER_LEGS_INVALID], "these two coin legs are no transfer of this kind"
        )
    if not _fits(out, in_, found.link):
        raise RuleViolationError(
            [TRANSFER_UNITS_DIFFER], "the receiving leg holds other units than were sent"
        )
    _lock_rows(ctx, (out, in_), wait_for_none=False)
    if not _still(ctx.conn, (out, in_)):
        raise ConflictError("a leg of this transfer was answered meanwhile")
    pair = {out.id, in_.id}
    _confirm(ctx, out, pair)
    _confirm(ctx, in_, pair)
    return _write(ctx, out, in_, found.link, TransferMatchedBy.USER)


# --- Cards ----------------------------------------------------------------------------------


def _window_end(leg: _Row) -> date:
    return (leg.occurred_at + WINDOW[TransferLinkKind.CHAIN]).astimezone(BERLIN).date()


def _pairable(conn: Connection, leg: _Row) -> bool:
    """Whether a counterpart of an unmatched leg could be in the app: a wallet leg's always
    (the classifier saw the other side as own); an exchange row's when a wallet address
    exists on the chain its network names (any wallet address when it names none). Never on a
    balance-only chain (D181 (c), BNB Smart Chain): it has no movement to pair with, so the
    card would be a dead end; its sync's balance check shows the unexplained deposit instead
    (rules/chain/evm.reconcile_balance_only, transfer.unmatched_leg)."""
    if leg.account_kind is not AccountKind.EXCHANGE:
        return True
    w = address_table.c
    where = [w.active.is_(True)]
    if leg.network is not None:
        chain = registry().network(leg.network)
        if chain is None or registry().chain(chain).source is ChainSourceKind.EVM_RPC_BALANCE:
            return False
        where.append(w.chain == chain.value)
    return conn.execute(select(w.id).where(and_(*where)).limit(1)).first() is not None


def open_transfers(conn: Connection, today: date) -> list[OpenTransfer]:
    """The coin legs that wait for a person, computed now: a fit the matcher did not write
    (transfer.proposed), several fits (transfer.ambiguous, every one listed), and a transfer
    leg past its 48 hours whose counterpart could be in the app (transfer.unmatched_leg, no
    candidate). An uncategorized leg without a fit is a payment, not a transfer: no card."""
    world = _World.read(conn)
    items: list[OpenTransfer] = []
    for leg in [r for r in world.open() if r.sends]:
        found = world.match(leg, world.receivers(leg))
        by_ref = {str(r.id): r for r in world.legs}
        if found.result == "linked" and found.counterpart is not None:
            other = by_ref[found.counterpart.ref]
            if not _fits(leg, other, found.link):
                code = OpenTransferCode.PARTIAL
            elif world.rivals(leg, other):
                code = OpenTransferCode.AMBIGUOUS
            else:
                code = OpenTransferCode.PROPOSED
            candidates = [other.view(found.link)]
        elif found.result == "ambiguous":
            fits = [
                (by_ref[c.ref], world.match(leg, [by_ref[c.ref]]).link) for c in found.candidates
            ]
            kept = [(c, kind) for c, kind in fits if _fits(leg, c, kind)]
            # one send that paid several own receipts under its hash (none of them all of it):
            # every receipt shown, no pick offered (review of PR #396)
            code = OpenTransferCode.AMBIGUOUS if kept else OpenTransferCode.PARTIAL
            candidates = [c.view(kind) for c, kind in (kept or fits)]
        elif (
            leg.kind is MovementKind.INTERNAL_TRANSFER_OUT
            and _window_end(leg) < today
            and _pairable(conn, leg)
        ):
            code, candidates = OpenTransferCode.UNMATCHED_LEG, []
        else:
            continue
        items.append(
            OpenTransfer(
                code=code, leg=leg.view(), candidates=candidates, window_end=_window_end(leg)
            )
        )
    return items


def count_open(conn: Connection) -> int:
    """For the CLI's dry run: the open sending legs."""
    return sum(1 for r in _World.read(conn).open() if r.sends)


__all__ = [
    "CHAIN_LINK_KINDS",
    "RelinkResult",
    "count_open",
    "is_coin_link",
    "link_by_hand",
    "open_transfers",
    "relink",
    "relink_after_answer",
    "relink_after_sync",
]
