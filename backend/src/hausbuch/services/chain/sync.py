"""One wallet address read from its chain (docs/phases/phase-7.md D182, D186, D187, D192).

`sync_address` is one run of one address, in three steps so no database transaction stays
open while a provider answers:

1. *Start* (a short transaction): the address, its cursor (the last succeeded run's
   `cursor_after`), whether its history was ever read to the end, the context the family
   classifier needs (every own address on the chain, the account's own addresses with their
   ids, the native coin's asset, the workspace's decoders, the held tokens), the source
   (services/chain/base.source_for; an unset key ends here with `wallet.key_missing` and no
   row), and the `wallet_sync` row as `running`.
2. *Read* (no transaction): the source's pages from the cursor until a page brings nothing
   new or the run's budget ends (`deferred`, the page note `wallet.sync_deferred`), each
   transaction classified by its family (rules/chain/classify); then, when the history is
   read to its end, the balances the source reconciles against (of the address, or of every
   own address of the account when the source's `reconcile_scope` is `account`).
3. *Write* (one transaction): each leg hashed (D182 (2)) with the HMAC of the own address it
   sits on, and only legs not stored yet written, joining the live group of the same
   transaction when another own address already wrote one; each new group routed by D186 (1)
   and written proposed or confirmed with its movements (a group with a question and no legs
   is written once, without movements); when at least one movement is new, the provider's
   records stored as the run's original (`document(subtype = chain_sync)`, NDJSON) with its
   `movement_import(format = chain_sync)` row (D182 (3)); Σ movements reconciled against the
   balances in base units (D186 (2)); the row finished once.

A failure ends its own address's row only (`failed`, `throttled` on a 429, `refused` on a
401 or 403) and writes nothing else: the cursor does not move, so the next run reads the same
range again. Nothing logged, raised or stored in an event holds an address, a cursor, a
transaction hash, a URL or a key: the logs carry the chain, the outcome, counts and the
exception class.

How a run's row reads (step 1's wallet page):

- `succeeded`, `balances` null: the budget ended first (`wallet.sync_deferred`); nothing was
  reconciled, since a half-read history cannot match the balance.
- `succeeded`, `balances` set, `reconciled` true or false: the history is read to its end;
  false raises `wallet.unreconciled` with `difference` = balance − Σ per asset.
- `succeeded`, `balances` set, `reconciled` null: a group of the account waits for a person's
  answer with no movements yet (a Bitcoin change card), so the difference is not a mismatch
  but „wartet auf Antwort“ (the event's `waiting`).

Readings of the spec (step 2 core):

- *First sync* (D186 (1)) is per address: true until a run of this address has read its
  history to the end (a succeeded run with `balances` set).
- *Priced* and *registry assets* (D186 (1)) mean every leg's asset is the chain's native coin
  or a registry token, the assets a price source covers (D180 (3)); the price rows
  themselves are step 6's.
- A chain asset is created on first sight: the native coin by the chain registry, a registry
  token by its row, any other token with the symbol the chain gives it (spam: no symbol and
  the masked label, D187 (2)). `priced_as_asset_id` points at the off-chain asset of the
  coin when the workspace has it; creating that asset is task "assets"'s.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, Engine, and_, exists, func, insert, null, select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import chain_decoder as decoder_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import movement_import as import_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.rules.chain import movement_hash
from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, ClassifyContext, classify
from hausbuch.rules.chain.reconcile import reconcile, sum_balance_parts
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.chain.spam import listed, token_label
from hausbuch.rules.errors import (
    WALLET_KEY_MISSING,
    WALLET_SOURCE_REFUSED,
    WALLET_SOURCE_THROTTLED,
    WALLET_SYNC_ERROR,
)
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.rules.review import route_group
from hausbuch.schemas.allocation import RoutingDecision
from hausbuch.schemas.chain import (
    AssetBalance,
    AssetKey,
    Chain,
    ChainSource,
    ChainTx,
    DecoderRow,
    GroupRoutingInput,
    GroupStatus,
    WalletSyncStatus,
)
from hausbuch.schemas.common import TAX_YEAR_MIN
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import DocumentSource, DocumentSubtype, DocumentType, ImportStatus
from hausbuch.schemas.events import (
    AssetRecorded,
    EventType,
    MovementGroupRecorded,
    MovementImportRecorded,
    MovementRecorded,
    WalletSyncRecorded,
)
from hausbuch.schemas.household import (
    AssetKind,
    MovementImportFormat,
    MovementSource,
    MovementStatus,
)
from hausbuch.services.chain.base import (
    WALLET_SOURCE_FAILED,
    ChainSourceError,
    RequestBudget,
    budget_limit,
    error_from,
    reconcile_scope,
    source_for,
)
from hausbuch.services.context import Actor, CommandContext, new_command
from hausbuch.services.documents import type_document, upload_document
from hausbuch.services.events import append_event
from hausbuch.services.prices.http import SourceError
from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

logger = logging.getLogger("hausbuch.services.chain.sync")

_STATUS_OF = {
    WALLET_SOURCE_THROTTLED: WalletSyncStatus.THROTTLED,
    WALLET_SOURCE_REFUSED: WalletSyncStatus.REFUSED,
}
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_MAX_CURSOR = 200
_NDJSON = "application/x-ndjson"
# asset_movement.quantity's scale: an asset with more decimals cannot be stored exactly
# (numeric(28,10) until task "assets"'s migration 0042 widens it); such a run fails rather
# than round a quantity.
_QUANTITY_SCALE = movement_table.c.quantity.type.scale

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """One address's run, for the job's log line and the route (no address, no cursor)."""

    address_id: UUID
    chain: Chain
    sync_id: UUID | None  # None: no row (wallet.key_missing)
    status: WalletSyncStatus | None
    error_code: str | None = None
    txs_seen: int = 0
    movements_new: int = 0
    deferred: bool = False
    reconciled: bool | None = None
    waiting: bool = False


@dataclass(frozen=True, slots=True)
class _Own:
    id: UUID
    hmac: str  # hex, as the movement hash takes it


@dataclass(frozen=True, slots=True)
class _Start:
    sync_id: UUID
    address_id: UUID
    account_id: UUID
    chain: Chain
    address: str
    own: dict[str, _Own]  # the account's active addresses on the chain
    cursor: str | None
    first_sync: bool
    started_at: datetime
    host: str
    scope: str


@dataclass(slots=True)
class _Read:
    txs: list[ChainTx] = field(default_factory=list)
    groups: list[ChainGroup] = field(default_factory=list)
    cursor: str | None = None
    deferred: bool = False
    balances: list[AssetBalance] | None = None


# --- Assets ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainAsset:
    id: UUID
    decimals: int
    spam: bool


def _priced_as(conn: Connection, symbol: str) -> UUID | None:
    return conn.execute(
        select(asset_table.c.id).where(
            asset_table.c.symbol == symbol,
            asset_table.c.chain.is_(None),
            asset_table.c.contract_address.is_(None),
        )
    ).scalar_one_or_none()


def _label_ok(symbol: str | None) -> bool:
    return (
        bool(symbol) and len(symbol) <= 32 and not any(ord(c) < 32 or ord(c) == 127 for c in symbol)
    )


def chain_asset(ctx: CommandContext, key: AssetKey, leg: ChainLeg | None = None) -> ChainAsset:
    """The workspace's asset of a chain and contract, created on first sight (D180, D187)."""
    by_key = and_(
        asset_table.c.chain == key.chain.value,
        asset_table.c.contract_address.is_(None)
        if key.contract is None
        else asset_table.c.contract_address == key.contract,
    )
    found = ctx.conn.execute(
        select(asset_table.c.id, asset_table.c.decimals, asset_table.c.spam).where(by_key)
    ).one_or_none()
    if found is not None:
        return ChainAsset(found.id, found.decimals, found.spam)
    spam = False
    if key.contract is None:
        chain_row = registry().chain(key.chain)
        symbol, decimals = chain_row.native_symbol, chain_row.native_decimals
        name = f"{symbol} ({key.chain.value})"
        priced = _priced_as(ctx.conn, symbol)
    else:
        token = asset_registry().token(key.chain, key.contract)
        if token is not None:
            symbol, decimals = token.symbol, token.decimals
            name = token.name or f"{token.symbol} ({key.chain.value})"
            priced = _priced_as(ctx.conn, asset_registry().priced_as(token).symbol)
        else:
            spam = bool(leg and leg.spam)
            told = leg.symbol if leg else None
            decimals = (leg.decimals if leg else None) or 0
            name = token_label(key, told)
            symbol = None if spam or not _label_ok(told) else told
            priced = None
    name = name[:200]
    asset_id = ctx.conn.execute(
        insert(asset_table)
        .values(
            workspace_id=ctx.workspace_id,
            kind=AssetKind.CRYPTO.value,
            symbol=symbol,
            name=name,
            decimals=decimals,
            chain=key.chain.value,
            contract_address=key.contract,
            priced_as_asset_id=priced,
            spam=spam,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(asset_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.ASSET_CREATED,
        asset_id,
        AssetRecorded(kind=AssetKind.CRYPTO, symbol=symbol, name=name),
    )
    return ChainAsset(asset_id, decimals, spam)


def _units_to_quantity(units: int, decimals: int) -> Decimal:
    if decimals > _QUANTITY_SCALE:
        raise ValueError("a chain asset with more decimals than a stored quantity holds")
    return Decimal(units).scaleb(-decimals)


def _quantity_to_units(quantity: Decimal, decimals: int) -> int:
    scaled = quantity.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError("a chain quantity below its asset's smallest unit")
    return int(scaled)


# --- Step 1: start ---------------------------------------------------------------------------


def _last_cursor(conn: Connection, address_id: UUID) -> tuple[str | None, bool]:
    """The cursor of the address's last succeeded run, and whether any run read the history
    to its end (one that read the balances)."""
    done = and_(
        sync_table.c.address_id == address_id,
        sync_table.c.status == WalletSyncStatus.SUCCEEDED.value,
    )
    last = conn.execute(
        select(sync_table.c.cursor_after)
        .where(done)
        .order_by(sync_table.c.started_at.desc(), sync_table.c.created_at.desc())
        .limit(1)
    ).one_or_none()
    caught_up = conn.execute(
        select(exists().where(done, sync_table.c.balances.is_not(None)))
    ).scalar_one()
    return (None if last is None else last.cursor_after), bool(caught_up)


def _own_of(conn: Connection, account_id: UUID, chain: Chain) -> dict[str, _Own]:
    return {
        r.address: _Own(r.id, bytes(r.address_hmac).hex())
        for r in conn.execute(
            select(address_table.c.id, address_table.c.address, address_table.c.address_hmac).where(
                address_table.c.account_id == account_id,
                address_table.c.chain == chain.value,
                address_table.c.active.is_(True),
            )
        )
    }


def _context(
    conn: Connection,
    chain: Chain,
    address: str,
    account_id: UUID,
    own: dict[str, _Own],
    native_asset_id: UUID,
) -> ClassifyContext:
    every_own = conn.execute(
        select(address_table.c.address).where(
            address_table.c.chain == chain.value, address_table.c.active.is_(True)
        )
    ).scalars()
    decoders = [
        DecoderRow(chain=chain, program=r.program, method=r.method, kind=r.kind)
        for r in conn.execute(
            select(decoder_table).where(
                decoder_table.c.chain == chain.value, decoder_table.c.superseded_by.is_(None)
            )
        )
    ]
    held = conn.execute(
        select(asset_table.c.contract_address)
        .distinct()
        .select_from(
            movement_table.join(asset_table, movement_table.c.asset_id == asset_table.c.id)
        )
        .where(
            asset_table.c.chain == chain.value,
            asset_table.c.contract_address.is_not(None),
            asset_table.c.spam.is_(False),
            movement_table.c.status == MovementStatus.CONFIRMED.value,
            movement_table.c.superseded_by.is_(None),
        )
    ).scalars()
    asset_ids = {
        AssetKey(chain=chain, contract=r.contract_address): r.id
        for r in conn.execute(
            select(asset_table.c.id, asset_table.c.contract_address).where(
                asset_table.c.chain == chain.value
            )
        )
    }
    return ClassifyContext(
        chain=chain,
        address=address,
        own_addresses=frozenset(every_own),
        decoders=tuple(decoders),
        held=frozenset(AssetKey(chain=chain, contract=c) for c in held),
        account_id=account_id,
        own_address_ids={a: o.id for a, o in own.items()},
        native_asset_id=native_asset_id,
        asset_ids=asset_ids,
    )


def _host(source: ChainSource) -> str:
    host = str(getattr(source, "host", "") or "").lower()
    return host if _HOST.fullmatch(host) else "unknown"


# --- Step 2: read ----------------------------------------------------------------------------


def _read(source: ChainSource, start: _Start, ctx: ClassifyContext, budget: RequestBudget) -> _Read:
    out = _Read(cursor=start.cursor)
    seen: set[str] = set()
    while True:
        page = source.transactions(start.address, out.cursor)
        for tx in page.items:
            out.txs.append(tx)
            if tx.hash in seen:
                continue
            seen.add(tx.hash)
            group = classify(tx, ctx)
            if group is not None and (group.legs or group.question):
                out.groups.append(group)
        moved = page.next_cursor is not None and page.next_cursor != out.cursor
        if moved:
            if len(page.next_cursor) > _MAX_CURSOR:
                raise ChainSourceError(WALLET_SOURCE_FAILED)
            out.cursor = page.next_cursor
        if not moved:  # the tip reached: None, or the cursor it was given
            break
        if budget.exhausted:
            out.deferred = True
            break
    if not out.deferred:
        addresses = list(start.own) if start.scope == "account" else [start.address]
        out.balances = [b for a in addresses for b in source.balances(a)]
    return out


# --- Step 3: write ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Plan:
    """A group's legs not stored yet, with their own address and hash."""

    group: ChainGroup
    legs: list[tuple[ChainLeg, _Own, str]]


def _own_for(start: _Start, leg: ChainLeg) -> _Own:
    found = start.own.get(leg.address or start.address)
    if found is None:
        raise ValueError("a leg on an address the account does not hold")
    return found


def _stored_hashes(ctx: CommandContext, account_id: UUID, hashes: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(hashes), 1000):
        found.update(
            ctx.conn.execute(
                select(movement_table.c.movement_hash).where(
                    movement_table.c.account_id == account_id,
                    movement_table.c.movement_hash.in_(hashes[i : i + 1000]),
                )
            ).scalars()
        )
    return found


def _plan(ctx: CommandContext, start: _Start, read: _Read) -> list[_Plan]:
    plans: list[_Plan] = []
    hashes: list[str] = []
    for group in read.groups:
        legs = []
        for leg in group.legs:
            own = _own_for(start, leg)
            digest = movement_hash(start.chain, group.tx_hash, own.hmac, leg.index, leg.role)
            legs.append((leg, own, digest))
        plans.append(_Plan(group, legs))
        hashes.extend(h for _, _, h in legs)
    stored = _stored_hashes(ctx, start.account_id, hashes)
    out: list[_Plan] = []
    planned: set[str] = set()
    for plan in plans:
        if not plan.legs:  # a question without movements: written once (see _write_group)
            out.append(plan)
            continue
        new = [(leg, own, h) for leg, own, h in plan.legs if h not in stored and h not in planned]
        planned.update(h for _, _, h in new)
        if new:
            out.append(_Plan(plan.group, new))
    return out


def _live_group(ctx: CommandContext, start: _Start, tx_hash: str):
    return ctx.conn.execute(
        select(group_table.c.id, group_table.c.status)
        .where(
            group_table.c.account_id == start.account_id,
            group_table.c.chain == start.chain.value,
            group_table.c.tx_hash == tx_hash,
            group_table.c.superseded_by.is_(None),
        )
        .order_by(group_table.c.created_at, group_table.c.id)
        .limit(1)
    ).one_or_none()


def _route(start: _Start, group: ChainGroup):
    every_listed = all(listed(leg.asset_key) for leg in group.legs)
    return route_group(
        GroupRoutingInput(
            kind=group.kind,
            fee_only=group.fee_only,
            decoder_listed=group.decoder_listed,
            priced=every_listed,
            registry_assets=every_listed,
            first_sync=start.first_sync,
            linked=group.linked,
            pair_learned=group.pair_learned,
            unregistered_output=group.unregistered_output,
        )
    )


def _write_group(ctx: CommandContext, start: _Start, plan: _Plan, document_id: UUID | None) -> int:
    group = plan.group
    live = _live_group(ctx, start, group.tx_hash)
    if live is not None and not plan.legs:
        return 0  # the question is already asked
    assets = [chain_asset(ctx, leg.asset_key, leg) for leg, _, _ in plan.legs]
    if live is not None:
        group_id, status = live.id, GroupStatus(live.status)
    else:
        routing = _route(start, group)
        status = (
            GroupStatus.CONFIRMED
            if routing.decision is RoutingDecision.AUTO_CONFIRM
            else GroupStatus.PROPOSED
        )
        group_id = ctx.conn.execute(
            insert(group_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=start.account_id,
                chain=start.chain.value,
                tx_hash=group.tx_hash,
                occurred_at=group.occurred_at,
                kind=group.kind.value,
                decoder=group.decoder,
                status=status.value,
                question=None if status is GroupStatus.CONFIRMED else group.question,
                created_at=ctx.now,
            )
            .returning(group_table.c.id)
        ).scalar_one()
        append_event(
            ctx,
            EventType.MOVEMENT_GROUP_CONFIRMED
            if status is GroupStatus.CONFIRMED
            else EventType.MOVEMENT_GROUP_PROPOSED,
            group_id,
            MovementGroupRecorded(
                account_id=start.account_id,
                chain=start.chain.value,
                kind=group.kind.value,
                reason=routing.reason,
                sync_id=start.sync_id,
            ),
        )
    movement_status = (
        MovementStatus.CONFIRMED if status is GroupStatus.CONFIRMED else MovementStatus.PROPOSED
    )
    for (leg, own, digest), asset in zip(plan.legs, assets, strict=True):
        movement_id = ctx.conn.execute(
            insert(movement_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=start.account_id,
                asset_id=asset.id,
                kind=leg.kind.value,
                quantity=_units_to_quantity(leg.signed, asset.decimals),
                occurred_at=group.occurred_at,
                tx_hash=group.tx_hash,
                movement_hash=digest,
                source=MovementSource.CHAIN.value,
                document_id=document_id,
                status=movement_status.value,
                group_id=group_id,
                address_id=own.id,
                index=leg.index,
                created_at=ctx.now,
            )
            .returning(movement_table.c.id)
        ).scalar_one()
        append_event(
            ctx,
            EventType.MOVEMENT_CONFIRMED
            if movement_status is MovementStatus.CONFIRMED
            else EventType.MOVEMENT_PROPOSED,
            movement_id,
            MovementRecorded(
                account_id=start.account_id,
                asset_id=asset.id,
                kind=leg.kind,
                source=MovementSource.CHAIN,
                document_id=document_id,
            ),
        )
    return len(plan.legs)


def _store_original(
    ctx: CommandContext, store: BlobStore, start: _Start, read: _Read, last: datetime
) -> UUID:
    lines = [json.dumps(tx.raw, sort_keys=True, separators=(",", ":")) for tx in read.txs]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    document = upload_document(
        ctx,
        store,
        data,
        media_type=_NDJSON,
        source=DocumentSource.BANK_SYNC,
        original_filename=f"chain-sync-{start.chain.value}-{last.date().isoformat()}.ndjson",
    )
    if document.typing is None:
        day = last.date()
        type_document(
            ctx,
            document.id,
            DocumentTyping(
                type=DocumentType.STATEMENT,
                subtype=DocumentSubtype.CHAIN_SYNC,
                document_date=day,
                tax_year=day.year if day.year >= TAX_YEAR_MIN else None,
            ),
        )
    return document.id


def _key_text(key: AssetKey) -> str:
    return key.contract or "native"


def _spam_keys(conn: Connection, chain: Chain, keys: set[AssetKey]) -> set[AssetKey]:
    contracts = [k.contract for k in keys if k.contract is not None]
    if not contracts:
        return set()
    found = conn.execute(
        select(asset_table.c.contract_address).where(
            asset_table.c.chain == chain.value,
            asset_table.c.contract_address.in_(contracts),
            asset_table.c.spam.is_(True),
        )
    ).scalars()
    return {AssetKey(chain=chain, contract=c) for c in found}


def _reconcile(
    ctx: CommandContext, start: _Start, balances: list[AssetBalance]
) -> tuple[dict[str, str], dict[str, str]]:
    """D186 (2): Σ confirmed and proposed movements (spam left out) of the address, or of the
    account's addresses on the chain when the source reconciles per account, against the
    balance's parts, per asset in base units. Returns (balances, difference) as stored."""
    scope = (
        movement_table.c.address_id.in_([o.id for o in start.own.values()])
        if start.scope == "account"
        else movement_table.c.address_id == start.address_id
    )
    rows = ctx.conn.execute(
        select(
            asset_table.c.contract_address,
            asset_table.c.decimals,
            asset_table.c.spam,
            func.sum(movement_table.c.quantity).label("total"),
        )
        .select_from(
            movement_table.join(asset_table, movement_table.c.asset_id == asset_table.c.id)
        )
        .where(scope, movement_table.c.superseded_by.is_(None))
        .group_by(asset_table.c.contract_address, asset_table.c.decimals, asset_table.c.spam)
    ).all()
    sums: dict[AssetKey, int] = {}
    spam_keys: set[AssetKey] = set()
    for r in rows:
        key = AssetKey(chain=start.chain, contract=r.contract_address)
        if r.spam:
            spam_keys.add(key)
            continue
        sums[key] = sums.get(key, 0) + _quantity_to_units(r.total, r.decimals)
    spam_keys |= _spam_keys(ctx.conn, start.chain, {b.asset_key for b in balances})
    have = sum_balance_parts(
        (b.asset_key, b.units) for b in balances if b.asset_key not in spam_keys
    )
    diff = reconcile(sums, have)
    stored = {_key_text(k): str(v) for k, v in sorted(have.items(), key=lambda i: _key_text(i[0]))}
    return stored, {_key_text(k): str(v) for k, v in diff.items()}


def _waiting(ctx: CommandContext, start: _Start) -> bool:
    """A live proposed group of the account on the chain with a question and no movement."""
    no_movement = ~exists().where(movement_table.c.group_id == group_table.c.id)
    return bool(
        ctx.conn.execute(
            select(
                exists().where(
                    group_table.c.account_id == start.account_id,
                    group_table.c.chain == start.chain.value,
                    group_table.c.superseded_by.is_(None),
                    group_table.c.status == GroupStatus.PROPOSED.value,
                    group_table.c.question.is_not(None),
                    no_movement,
                )
            )
        ).scalar_one()
    )


def _finish(ctx: CommandContext, start: _Start, values: dict, payload: WalletSyncRecorded) -> None:
    ctx.conn.execute(
        update(sync_table)
        .where(sync_table.c.id == start.sync_id)
        .values(finished_at=max(ctx.now, start.started_at), **values)
    )
    event = (
        EventType.WALLET_SYNC_COMPLETED
        if payload.status == WalletSyncStatus.SUCCEEDED.value
        else EventType.WALLET_SYNC_FAILED
    )
    append_event(ctx, event, start.account_id, payload)


def _write(ctx: CommandContext, store: BlobStore, start: _Start, read: _Read) -> SyncOutcome:
    plans = _plan(ctx, start, read)
    moving = [p for p in plans if p.legs]
    document_id = import_id = None
    new = sum(len(p.legs) for p in moving)
    if moving:
        last = max(p.group.occurred_at for p in moving)
        first = min(p.group.occurred_at for p in moving)
        document_id = _store_original(ctx, store, start, read, last)
        total = sum(len(g.legs) for g in read.groups)
        import_id = ctx.conn.execute(
            insert(import_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=start.account_id,
                document_id=document_id,
                format=MovementImportFormat.CHAIN_SYNC.value,
                rows_total=total,
                rows_new=new,
                rows_skipped=total - new,
                first_at=first,
                last_at=last,
                status=ImportStatus.IMPORTED.value,
                created_at=ctx.now,
                updated_at=ctx.now,
            )
            .returning(import_table.c.id)
        ).scalar_one()
        append_event(
            ctx,
            EventType.ACCOUNT_MOVEMENTS_IMPORTED,
            start.account_id,
            MovementImportRecorded(
                import_id=import_id,
                document_id=document_id,
                format=MovementImportFormat.CHAIN_SYNC,
                rows_total=total,
                rows_new=new,
                rows_skipped=total - new,
            ),
            effective_date=last.date(),
        )
    for plan in plans:
        _write_group(ctx, start, plan, document_id if plan.legs else None)
    balances = difference = reconciled = None
    waiting = False
    block = max((tx.block for tx in read.txs), default=None)
    if read.balances is not None:
        balances, difference = _reconcile(ctx, start, read.balances)
        reconciled = not difference
        if difference and _waiting(ctx, start):
            reconciled, difference, waiting = None, None, True
        block = max((b.at_block for b in read.balances), default=block)
    _finish(
        ctx,
        start,
        {
            "status": WalletSyncStatus.SUCCEEDED.value,
            "cursor_after": read.cursor,
            "block": block,
            "txs_seen": len(read.txs),
            "movements_new": new,
            # JSONB: a Python None would store JSON null, not SQL NULL (the CHECKs read NULL)
            "balances": null() if balances is None else balances,
            "reconciled": reconciled,
            "difference": difference or null(),
            "import_id": import_id,
            "document_id": document_id,
        },
        WalletSyncRecorded(
            sync_id=start.sync_id,
            address_id=start.address_id,
            status="succeeded",
            source_host=start.host,
            txs_seen=len(read.txs),
            movements_new=new,
            reconciled=reconciled,
            deferred=read.deferred,
            waiting=waiting,
            document_id=document_id,
        ),
    )
    return SyncOutcome(
        start.address_id,
        start.chain,
        start.sync_id,
        WalletSyncStatus.SUCCEEDED,
        txs_seen=len(read.txs),
        movements_new=new,
        deferred=read.deferred,
        reconciled=reconciled,
        waiting=waiting,
    )


# --- The run ---------------------------------------------------------------------------------


def _log(outcome: SyncOutcome, exc: BaseException | None = None) -> None:
    fields = {
        "event": "wallet_sync",
        "chain": outcome.chain.value,
        "status": None if outcome.status is None else outcome.status.value,
        "error_code": outcome.error_code,
        "txs_seen": outcome.txs_seen,
        "movements_new": outcome.movements_new,
        "deferred": outcome.deferred,
        "reconciled": outcome.reconciled,
        "waiting": outcome.waiting,
    }
    if exc is not None:
        fields["exc_type"] = type(exc).__name__
    level = logging.INFO if outcome.error_code is None else logging.WARNING
    logger.log(level, "a wallet address was read from its chain", extra={"fields": fields})


def _fail(
    engine: Engine,
    workspace_id: UUID,
    actor: Actor,
    clock: Clock,
    start: _Start,
    code: str,
    txs_seen: int,
    exc: BaseException,
) -> SyncOutcome:
    status = _STATUS_OF.get(code, WalletSyncStatus.FAILED)
    with workspace_transaction(engine, workspace_id) as conn:
        _finish(
            new_command(conn, workspace_id, actor, now=clock()),
            start,
            {"status": status.value, "error_code": code, "txs_seen": txs_seen},
            WalletSyncRecorded(
                sync_id=start.sync_id,
                address_id=start.address_id,
                status=status.value,
                source_host=start.host,
                txs_seen=txs_seen,
                movements_new=0,
                error_code=code,
            ),
        )
    outcome = SyncOutcome(
        start.address_id, start.chain, start.sync_id, status, error_code=code, txs_seen=txs_seen
    )
    _log(outcome, exc)
    return outcome


def _begin(
    engine: Engine,
    workspace_id: UUID,
    address_id: UUID,
    settings: Settings,
    actor: Actor,
    clock: Clock,
    budgets: dict,
) -> tuple[_Start, ChainSource, ClassifyContext, RequestBudget] | SyncOutcome:
    with workspace_transaction(engine, workspace_id) as conn:
        row = conn.execute(
            select(address_table).where(address_table.c.id == address_id)
        ).one_or_none()
        if row is None:
            raise LookupError("wallet address not found")
        chain = Chain(row.chain)
        kind = registry().chain(chain).source
        budget = budgets.get(kind)
        if budget is None:
            budget = budgets[kind] = RequestBudget(budget_limit(kind, settings))
        try:
            source = source_for(chain, settings, budget)
        except ChainSourceError as exc:
            outcome = SyncOutcome(address_id, chain, None, None, error_code=exc.code)
            _log(outcome)
            return outcome
        now = clock()
        native = chain_asset(new_command(conn, workspace_id, actor, now=now), AssetKey(chain=chain))
        own = _own_of(conn, row.account_id, chain)
        own.setdefault(row.address, _Own(row.id, bytes(row.address_hmac).hex()))
        cursor, caught_up = _last_cursor(conn, address_id)
        ctx = _context(conn, chain, row.address, row.account_id, own, native.id)
        host = _host(source)
        sync_id = conn.execute(
            insert(sync_table)
            .values(
                workspace_id=workspace_id,
                account_id=row.account_id,
                address_id=address_id,
                started_at=now,
                status=WalletSyncStatus.RUNNING.value,
                source_host=host,
                cursor_before=cursor,
                created_at=now,
            )
            .returning(sync_table.c.id)
        ).scalar_one()
    start = _Start(
        sync_id=sync_id,
        address_id=address_id,
        account_id=row.account_id,
        chain=chain,
        address=row.address,
        own=own,
        cursor=cursor,
        first_sync=not caught_up,
        started_at=now,
        host=host,
        scope=reconcile_scope(source),
    )
    return start, source, ctx, budget


def sync_address(
    engine: Engine,
    workspace_id: UUID,
    address_id: UUID,
    *,
    settings: Settings,
    store: BlobStore,
    actor: Actor,
    clock: Clock,
    budgets: dict | None = None,
) -> SyncOutcome:
    """One run of one address (see the module docstring). Never raises for the provider's or
    its own failure: the address's row says it, and the next address runs. `budgets` holds
    one RequestBudget per source kind, shared by the addresses of one job run."""
    begun = _begin(
        engine,
        workspace_id,
        address_id,
        settings,
        actor,
        clock,
        {} if budgets is None else budgets,
    )
    if isinstance(begun, SyncOutcome):
        return begun
    start, source, ctx, budget = begun
    try:
        read = _read(source, start, ctx, budget)
    except ChainSourceError as exc:
        return _fail(engine, workspace_id, actor, clock, start, exc.code, 0, exc)
    except SourceError as exc:
        return _fail(engine, workspace_id, actor, clock, start, error_from(exc).code, 0, exc)
    except Exception as exc:  # noqa: BLE001 - a classifier's failure: its class name only
        return _fail(engine, workspace_id, actor, clock, start, WALLET_SYNC_ERROR, 0, exc)
    try:
        with workspace_transaction(engine, workspace_id) as conn:
            ctx_w = new_command(conn, workspace_id, actor, now=clock())
            outcome = _write(ctx_w, store, start, read)
    except Exception as exc:  # noqa: BLE001 - the write rolled back; its class name only
        return _fail(
            engine, workspace_id, actor, clock, start, WALLET_SYNC_ERROR, len(read.txs), exc
        )
    _log(outcome)
    return outcome


# --- A wallet, a workspace -------------------------------------------------------------------


def addresses_to_sync(conn: Connection, account_id: UUID | None = None) -> list[UUID]:
    """The active addresses (of one wallet, or of every wallet), the one synced longest ago
    first, never synced before all (D182 (1))."""
    last = (
        select(sync_table.c.address_id, func.max(sync_table.c.started_at).label("at"))
        .group_by(sync_table.c.address_id)
        .subquery()
    )
    query = (
        select(address_table.c.id)
        .select_from(address_table.outerjoin(last, last.c.address_id == address_table.c.id))
        .where(address_table.c.active.is_(True))
        .order_by(last.c.at.asc().nulls_first(), address_table.c.created_at, address_table.c.id)
    )
    if account_id is not None:
        query = query.where(address_table.c.account_id == account_id)
    return list(conn.execute(query).scalars())


def sync_wallets(
    engine: Engine,
    workspace_id: UUID,
    *,
    settings: Settings,
    store: BlobStore,
    actor: Actor,
    clock: Clock,
    account_id: UUID | None = None,
    budgets: dict | None = None,
) -> list[SyncOutcome]:
    """Every active address of one wallet (account_id) or of the workspace, oldest sync
    first, each in its own run; the budgets are shared per source kind (D182 (1))."""
    budgets = {} if budgets is None else budgets
    with workspace_transaction(engine, workspace_id) as conn:
        ids = addresses_to_sync(conn, account_id)
    return [
        sync_address(
            engine,
            workspace_id,
            address_id,
            settings=settings,
            store=store,
            actor=actor,
            clock=clock,
            budgets=budgets,
        )
        for address_id in ids
    ]


__all__ = [
    "WALLET_KEY_MISSING",
    "WALLET_SYNC_ERROR",
    "ChainAsset",
    "SyncOutcome",
    "addresses_to_sync",
    "chain_asset",
    "sync_address",
    "sync_wallets",
]
