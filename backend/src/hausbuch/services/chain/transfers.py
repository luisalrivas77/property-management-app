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
wraps. A linked result is written (matched_by matcher) when:

1. both legs are confirmed; or
2. the two legs share their transaction hash, the link is of kind chain, nothing else fits the
   receiving leg, and each proposed leg may be confirmed by code (D186 (1): an exchange's
   withdrawal by hash, own address to own address): an exchange row with a transfer kind, or
   a leg whose group is a transfer or exchange_transfer that waits only for its link (routed
   routing.no_match, so never a wallet's first sync, which waits for a person) and holds no
   leg but the pair's and its fees. The link confirms them in the same transaction (the
   group, its legs and its fees, each with its event), so the sync's `linked` (which the
   classifiers leave false, §15.1) is filled here, after the fact.

Anything else (an amount-only fit, a bridge, a first sync, an ambiguous fit) waits for a person
on a card of GET /transfers/open: transfer.proposed with the one fit, transfer.ambiguous with
every fit, transfer.unmatched_leg once a transfer leg's 48 hours have passed and a wallet that
could hold its counterpart exists. The person's pick (POST /transfers, link_by_hand) confirms
both legs as the person's answer and writes the link (matched_by user), only for a pair the
matcher itself fits, of the kind it reads.

Currentness is decided on read, as phase 6's: services.transfers._current_links counts a chain,
bridge or wrap link only while both legs are live, whoever wrote it, so re-booking a leg
(superseding it) ends the link (D184 (5)) and the leg's successor is open again. Nothing here
sets transfer_link.superseded_by.

Locks: the workspace's transfer lock (services.transfers._lock); a movement or group row is
locked with SKIP LOCKED by the matcher (a row a person is answering right now is left for the
next run) and with NOWAIT on a link by hand (409), so this module never waits for a row while
it holds the transfer lock. It writes no account row.

Privacy (D179): events carry ids only (transfer.linked, movement.confirmed,
movement_group.confirmed); the log carries counts; a card names the account, the date, the
quantity and the symbol, never an address or a hash.
"""

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
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
from hausbuch.rules.chain.match_transfer import WINDOW, ChainLeg, ChainMatch, match
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.errors import TRANSFER_LEGS_INVALID
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
    """The group's live movements that are not a fee: id, status."""
    m = movement_table.c
    return conn.execute(
        select(m.id, m.status).where(
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
    return {r.id for r in _group_rest(conn, leg.group_id)} <= pair


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


def _confirm_movement(ctx: CommandContext, movement_id: UUID) -> None:
    m = movement_table.c
    row = ctx.conn.execute(
        update(movement_table)
        .where(m.id == movement_id, m.status == MovementStatus.PROPOSED.value)
        .values(status=MovementStatus.CONFIRMED.value)
        .returning(m.account_id, m.asset_id, m.kind, m.source, m.document_id)
    ).one_or_none()
    if row is None:
        return
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


def _confirm(ctx: CommandContext, leg: _Row, pair: set[UUID]) -> int:
    """The leg confirmed; on a chain, its group too once no leg of it but the pair's waits
    (its fee legs with it: the fee is a fact of the transaction); a group asking a question,
    or with another leg still waiting, stays proposed. The count of movements confirmed."""
    count = 0
    if not leg.confirmed:
        _confirm_movement(ctx, leg.id)
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
        _confirm_movement(ctx, fee)
        count += 1
    group = ctx.conn.execute(
        update(group_table)
        .where(g.id == leg.group_id, g.status == GroupStatus.PROPOSED.value)
        .values(status=GroupStatus.CONFIRMED.value)
        .returning(g.account_id, g.chain, g.kind)
    ).one_or_none()
    if group is not None:
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
) -> TransferLink:
    """One transfer_link row of two coin movements and its transfer.linked event (ids only),
    as services.transfers._write writes a EUR link, without its bank-line hooks."""
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
        TransferLinked(kind=kind, matched_by=matched_by, **legs),
    )
    return link


@dataclass
class RelinkResult:
    linked: int = 0
    confirmed: int = 0  # movements the links confirmed (D186 (1))
    skipped_locked: int = 0  # a fit left for the next run: a person was answering a leg
    busy: bool = False  # relink(wait=False) found the transfer lock taken: nothing done


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
    world = _World.read(ctx.conn)
    result = RelinkResult()
    for leg in [r for r in world.open() if r.sends]:
        if leg.id in world.linked:
            continue
        found = world.match(leg, world.receivers(leg))
        if found.result != "linked" or found.counterpart is None or found.link is None:
            continue
        other = next(r for r in world.legs if str(r.id) == found.counterpart.ref)
        if world.rivals(leg, other):
            continue  # two sends fit one receipt: the card asks which
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
        result.confirmed += _confirm(ctx, leg, pair) + _confirm(ctx, other, pair)
        _write(ctx, leg, other, found.link, TransferMatchedBy.MATCHER)
        world.linked |= pair
        result.linked += 1
    if result.linked or result.skipped_locked:
        logger.info(
            "chain transfers relinked",
            extra={
                "fields": {
                    "event": "chain_relink",
                    "linked": result.linked,
                    "confirmed": result.confirmed,
                    "skipped_locked": result.skipped_locked,
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


def link_by_hand(ctx: CommandContext, body: TransferLinkCreate) -> TransferLink:
    """POST /transfers for two coin legs (services.transfers.link_by_hand calls it after its
    locks): the person's pick on a transfer.proposed or transfer.ambiguous card. 404 for a leg
    that is no open coin leg; the pair linked already returns its link; 409 when a leg is in
    another link or being answered right now; 422 transfer.legs_invalid when the matcher does
    not fit the pair or reads another kind than the body names. Both legs are confirmed by the
    person's answer (their groups too, once nothing else of them waits), then linked
    (matched_by user)."""
    from ..transfers import _link_of

    assert body.out_movement_id is not None and body.in_movement_id is not None
    world = _World.read(ctx.conn)
    by_id = {r.id: r for r in world.legs}
    out, in_ = by_id.get(body.out_movement_id), by_id.get(body.in_movement_id)
    if out is None:
        raise NotFoundError("movement", body.out_movement_id)
    if in_ is None:
        raise NotFoundError("movement", body.in_movement_id)
    already = _link_of(ctx.conn, out.id, in_.id)
    if already is not None:
        return already
    if out.id in world.linked or in_.id in world.linked:
        raise ConflictError("a leg of this transfer is linked already")
    found = world.match(out, [in_]) if out.sends and not in_.sends else None
    if found is None or found.result != "linked" or found.link is not body.kind:
        raise RuleViolationError(
            [TRANSFER_LEGS_INVALID], "these two coin legs are no transfer of this kind"
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
            rivals = world.rivals(leg, other)
            code = OpenTransferCode.AMBIGUOUS if rivals else OpenTransferCode.PROPOSED
            candidates = [other.view(found.link)]
        elif found.result == "ambiguous":
            code = OpenTransferCode.AMBIGUOUS
            candidates = [
                by_ref[c.ref].view(world.match(leg, [by_ref[c.ref]]).link) for c in found.candidates
            ]
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
