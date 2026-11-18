"""Lamports sent to an own token account, read again from the stored originals
(docs/phases/phase-7.md D186 (2) amended 18 Nov 2026).

A stranger's address-poisoning transaction sent 1 lamport to the owner's main account and 1
lamport to the owner's own token account, with no token balance row in the record. Until the
amendment the classifier knew an own token account only through the record's token rows, so it
booked the main account's lamport and never the token account's, and the address stayed
unreconciled by 1 lamport. The classifier now counts that lamport and the reader keeps the own
token accounts beside such a record (rules/chain/solana.moves_rowless_token_account), but a
sync never reads an old transaction again, and the leg it stored holds the movement hash the
corrected leg would have (D182 (2) covers no amount), so a re-read could never write it.

`repair_token_account_lamports(ctx, store, hmac_key=..., dry_run=...)`, run once by the worker
(jobs.lamports_repair, queued by `hausbuch repair-solana-lamports`): for each active Solana
address, its stored originals are read (never the provider, D182 (3), D192), the own token
accounts are taken from them as the source takes them (services/chain/solana_rpc
`owned_token_accounts` and the accounts kept under solana.SYNC_KEY), and every record in which
such an account's lamports change with no token row naming it is classified again with them.
Where the result differs from the transaction's live movements on the address in quantity only
(the same legs: index, asset and direction), the live group is superseded by a new group:

- a confirmed Kleinstbetrag (D262: a transfer whose one leg is a confirmed 0 € acquisition)
  whose corrected receipt is still dust by `wallet_groups.dust_groups` keeps the person's
  confirmation: the new group is confirmed under routing.person, its leg the same 0 €
  acquisition at the corrected quantity;
- anything else is proposed for a person, as the classifier reads it now (routing.chain_reread).

A transaction with no live group and no stored movement gets a new proposed group whose
movements carry the sync's own movement hash, so a later read of it writes nothing twice. A
linked leg, another address's leg in the group, a legless card, or legs that differ in more than
their quantity are left as they are and counted (`skipped`).

Each corrected movement copies the row it supersedes (source, address, index, time; a chain row
gets a hash of its own, transfers._reopened_hash, so the superseded row keeps the sync's hash
and a re-read still skips it) and points at a new stored original holding the amended records
(the record with the own token accounts under SYNC_KEY), so a replay of that movement's
document needs no provider (D192); the earlier original stays as it was (principle 6). No
wallet_sync row is written: the next sync reads the balance and records the reconciliation.

Idempotent: a second run finds the live movements equal to the classification and changes
nothing. Nothing here logs or puts into an event a signature, an address or a token account."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.rules.chain import movement_hash, solana
from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, classify
from hausbuch.rules.lots import berlin_day
from hausbuch.schemas.chain import AssetKey, Chain, GroupKind, GroupStatus
from hausbuch.schemas.common import TAX_YEAR_MIN
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import DocumentSource, DocumentSubtype, DocumentType
from hausbuch.schemas.events import (
    EventType,
    MovementGroupRecorded,
    MovementRecorded,
    MovementSuperseded,
)
from hausbuch.schemas.household import MovementKind, MovementSource, MovementStatus
from hausbuch.services.context import CommandContext
from hausbuch.services.documents import read_original, type_document, upload_document
from hausbuch.services.events import append_event
from hausbuch.services.wallet_groups import PERSON, DustCandidate, dust_groups
from hausbuch.storage import BlobStore

from .solana_rpc import SolanaRpc, owned_token_accounts
from .sync import (
    _NDJSON,
    _context,
    _find_asset,
    _Own,
    _own_of,
    _units_to_quantity,
    chain_asset,
)
from .transfers import _reopened_hash

# The routing reason of a group this repair proposes for a person (MovementGroupRecorded).
CHAIN_REREAD = "routing.chain_reread"
TASK = "repair_solana_lamports"


@dataclass
class LamportsRepaired:
    addresses: int = 0  # active Solana addresses read
    records: int = 0  # stored records in which an own token account's lamports move rowless
    unchanged: int = 0  # their live movements already hold those lamports
    carried: int = 0  # corrected, the person's Kleinstbetrag confirmation carried (D262)
    proposed: int = 0  # corrected (or written), proposed for a person
    skipped: int = 0  # left as they are (a linked leg, another shape, another address's leg)


@dataclass(slots=True)
class _Fix:
    record: dict[str, Any]
    group: ChainGroup
    legs: list[ChainLeg]
    live: Any  # the live group row, None when the transaction has none
    moves: list  # its live movements, matched to `legs` by position
    carry: bool


# --- reading ----------------------------------------------------------------------------------


def _records(ctx: CommandContext, store: BlobStore, address_id: UUID) -> Iterator[dict[str, Any]]:
    """The address's stored originals in the order the runs stored them, record by record."""
    documents = ctx.conn.execute(
        select(sync_table.c.document_id)
        .where(sync_table.c.address_id == address_id, sync_table.c.document_id.is_not(None))
        .order_by(sync_table.c.started_at, sync_table.c.id)
    ).scalars()
    for document_id in list(documents):
        for line in read_original(ctx.conn, store, document_id).splitlines():
            if line.strip():
                yield json.loads(line)


def _amend(record: dict[str, Any], accounts: set[str]) -> dict[str, Any]:
    """The record as the reader now stores it: the own token accounts kept under SYNC_KEY,
    whatever else an earlier run kept there (the block's time) kept with them."""
    kept = record.get(solana.SYNC_KEY)
    out = dict(record)
    merged = dict(kept) if isinstance(kept, dict) else {}
    merged["own_token_accounts"] = sorted(accounts)
    out[solana.SYNC_KEY] = merged
    return out


def _key(index: str | None, asset_id: UUID, incoming: bool) -> tuple:
    return (index, asset_id, incoming)


def _leg_key(ctx: CommandContext, leg: ChainLeg) -> tuple | None:
    asset = _find_asset(ctx.conn, leg.asset_key)
    if asset is None:
        return None
    return _key(leg.index, asset.id, leg.role == "in")


def _move_key(move) -> tuple:
    return _key(move.index, move.asset_id, move.quantity > 0)


def _quantity(ctx: CommandContext, leg: ChainLeg) -> Decimal:
    asset = chain_asset(ctx, leg.asset_key, leg)
    return _units_to_quantity(leg.signed, asset.decimals)


def _linked(ctx: CommandContext, movement_ids: list[UUID]) -> bool:
    k = link_table.c
    return (
        ctx.conn.execute(
            select(k.id)
            .where(
                k.superseded_by.is_(None),
                (k.out_movement_id.in_(movement_ids)) | (k.in_movement_id.in_(movement_ids)),
            )
            .limit(1)
        ).first()
        is not None
    )


def _dust_shape(live, moves: list) -> bool:
    """D262's booked Kleinstbetrag: a confirmed transfer without a question whose one live
    leg is a confirmed 0 € acquisition (wallet_groups._as_dust)."""
    if (
        live.status != GroupStatus.CONFIRMED.value
        or live.kind != GroupKind.TRANSFER.value
        or live.question is not None
        or len(moves) != 1
    ):
        return False
    (move,) = moves
    return (
        move.kind == MovementKind.ACQUISITION.value
        and move.status == MovementStatus.CONFIRMED.value
        and move.eur_amount is not None
        and move.eur_amount == 0
    )


def _plan(
    ctx: CommandContext,
    record: dict[str, Any],
    group: ChainGroup | None,
    address: str,
    address_id: UUID,
    account_id: UUID,
    own: _Own,
) -> _Fix | str:
    """What one re-classified record needs: a _Fix, or "unchanged" / "skipped"."""
    signature = solana.signature(record)
    legs = [] if group is None else [x for x in group.legs if (x.address or address) == address]
    m, g = movement_table.c, group_table.c
    moves = list(
        ctx.conn.execute(
            select(movement_table)
            .where(m.account_id == account_id, m.tx_hash == signature, m.superseded_by.is_(None))
            .order_by(m["index"], m.id)
            .with_for_update()
        )
    )
    lives = list(
        ctx.conn.execute(
            select(group_table)
            .where(
                g.account_id == account_id,
                g.chain == Chain.SOLANA.value,
                g.tx_hash == signature,
                g.superseded_by.is_(None),
            )
            .with_for_update()
        )
    )
    if group is None or not legs:
        return "unchanged" if not moves else "skipped"
    if not moves:
        if lives:
            return "skipped"  # a card without movements (a question) is answered by a person
        digests = [
            movement_hash(Chain.SOLANA, signature, own.hmac, leg.index, leg.role) for leg in legs
        ]
        stored = ctx.conn.execute(
            select(m.id).where(m.account_id == account_id, m.movement_hash.in_(digests)).limit(1)
        ).first()
        if stored is not None:
            return "skipped"
        return _Fix(record, group, legs, None, [], carry=False)
    if len(lives) != 1 or any(
        x.address_id != address_id or x.group_id != lives[0].id for x in moves
    ):
        return "skipped"
    keys = [_leg_key(ctx, leg) for leg in legs]
    if None in keys or len(set(keys)) != len(keys):
        return "skipped"
    by_key = {_move_key(x): x for x in moves}
    if len(by_key) != len(moves) or set(by_key) != set(keys):
        return "skipped"
    ordered = [by_key[k] for k in keys]
    wanted = [_quantity(ctx, leg) for leg in legs]
    if all(x.quantity == q for x, q in zip(ordered, wanted, strict=True)):
        return "unchanged"
    if _linked(ctx, [x.id for x in ordered]):
        return "skipped"
    (live,) = lives
    carry = False
    if (
        _dust_shape(live, ordered)
        and group.kind is GroupKind.TRANSFER
        and group.question is None
        and len(legs) == 1
        and legs[0].role == "in"
        and legs[0].kind is MovementKind.UNCATEGORIZED
    ):
        candidate = DustCandidate(
            group_id=live.id,
            movement_id=ordered[0].id,
            asset_id=ordered[0].asset_id,
            quantity=wanted[0],
            day=berlin_day(live.occurred_at),
            tx_hash=signature,
        )
        carry = bool(dust_groups(ctx.conn, [candidate]))
    return _Fix(record, group, legs, live, ordered, carry)


# --- writing ----------------------------------------------------------------------------------


def _store(ctx: CommandContext, store: BlobStore, fixes: list[_Fix]) -> UUID:
    """The amended records as one original of their own (as sync._store_original writes one)."""
    lines = [json.dumps(f.record, sort_keys=True, separators=(",", ":")) for f in fixes]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    last = max(f.group.occurred_at for f in fixes)
    document = upload_document(
        ctx,
        store,
        data,
        media_type=_NDJSON,
        source=DocumentSource.BANK_SYNC,
        original_filename=f"chain-sync-{Chain.SOLANA.value}-{last.date().isoformat()}.ndjson",
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


def _new_group(
    ctx: CommandContext,
    account_id: UUID,
    fix: _Fix,
    *,
    kind: GroupKind,
    status: GroupStatus,
    question: str | None,
    reason: str,
) -> UUID:
    group = fix.group
    group_id = ctx.conn.execute(
        insert(group_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            chain=Chain.SOLANA.value,
            tx_hash=group.tx_hash,
            occurred_at=fix.live.occurred_at if fix.live is not None else group.occurred_at,
            kind=kind.value,
            decoder=None if fix.carry else group.decoder,
            status=status.value,
            question=question,
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
            account_id=account_id, chain=Chain.SOLANA.value, kind=kind.value, reason=reason
        ),
    )
    if fix.live is not None:
        ctx.conn.execute(
            update(group_table)
            .where(group_table.c.id == fix.live.id)
            .values(superseded_by=group_id)
        )
    return group_id


def _recorded(ctx: CommandContext, movement_id: UUID, values: dict, document_id: UUID) -> None:
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED
        if values["status"] == MovementStatus.CONFIRMED.value
        else EventType.MOVEMENT_PROPOSED,
        movement_id,
        MovementRecorded(
            account_id=values["account_id"],
            asset_id=values["asset_id"],
            kind=MovementKind(values["kind"]),
            source=MovementSource(values["source"]),
            document_id=document_id,
        ),
    )


def _write(
    ctx: CommandContext,
    account_id: UUID,
    address_id: UUID,
    own: _Own,
    fix: _Fix,
    document_id: UUID,
) -> None:
    group = fix.group
    if fix.carry:
        status, kind, question, reason = GroupStatus.CONFIRMED, GroupKind.TRANSFER, None, PERSON
    else:
        status, kind, question, reason = (
            GroupStatus.PROPOSED,
            group.kind,
            group.question,
            CHAIN_REREAD,
        )
    group_id = _new_group(
        ctx, account_id, fix, kind=kind, status=status, question=question, reason=reason
    )
    m = movement_table.c
    if fix.live is None:  # a transaction no group held: the sync's own rows, proposed
        for leg in fix.legs:
            asset = chain_asset(ctx, leg.asset_key, leg)
            values = {
                "account_id": account_id,
                "asset_id": asset.id,
                "kind": leg.kind.value,
                "source": MovementSource.CHAIN.value,
                "status": MovementStatus.PROPOSED.value,
            }
            movement_id = ctx.conn.execute(
                insert(movement_table)
                .values(
                    workspace_id=ctx.workspace_id,
                    quantity=_units_to_quantity(leg.signed, asset.decimals),
                    occurred_at=group.occurred_at,
                    tx_hash=group.tx_hash,
                    movement_hash=movement_hash(
                        Chain.SOLANA, group.tx_hash, own.hmac, leg.index, leg.role
                    ),
                    document_id=document_id,
                    group_id=group_id,
                    address_id=address_id,
                    index=leg.index,
                    created_at=ctx.now,
                    **values,
                )
                .returning(m.id)
            ).scalar_one()
            _recorded(ctx, movement_id, values, document_id)
        return
    skip = {"id", "superseded_by", "created_at", "movement_hash", "group_id"}
    for move, leg in zip(fix.moves, fix.legs, strict=True):
        values = {c.name: getattr(move, c.name) for c in movement_table.c if c.name not in skip}
        values["quantity"] = _quantity(ctx, leg)
        values["document_id"] = document_id
        if not fix.carry:  # as the classifier reads it now, for a person to confirm
            values["kind"] = leg.kind.value
            values["status"] = MovementStatus.PROPOSED.value
            values["eur_amount"] = None
        new_id = uuid4()
        chain_row = move.source == MovementSource.CHAIN.value and move.movement_hash is not None
        ctx.conn.execute(
            insert(movement_table).values(
                id=new_id,
                movement_hash=_reopened_hash(move.movement_hash, new_id) if chain_row else None,
                group_id=group_id,
                created_at=ctx.now,
                **values,
            )
        )
        ctx.conn.execute(update(movement_table).where(m.id == move.id).values(superseded_by=new_id))
        append_event(
            ctx,
            EventType.MOVEMENT_SUPERSEDED,
            move.id,
            MovementSuperseded(superseded_by=new_id, document_id=document_id),
        )
        _recorded(ctx, new_id, values, document_id)


# --- the command ------------------------------------------------------------------------------


def repair_token_account_lamports(
    ctx: CommandContext, store: BlobStore, *, hmac_key: bytes | None, dry_run: bool = False
) -> LamportsRepaired:
    """See the module docstring. `dry_run` counts and writes nothing (no original either)."""
    out = LamportsRepaired()
    a = address_table.c
    addresses = ctx.conn.execute(
        select(a.id, a.account_id, a.address, a.address_hmac)
        .where(a.chain == Chain.SOLANA.value, a.active.is_(True))
        .order_by(a.created_at, a.id)
    ).all()
    for row in addresses:
        out.addresses += 1
        # The wallet's row first, as the sync's write and a person's answer lock it.
        ctx.conn.execute(
            select(account_table.c.id).where(account_table.c.id == row.account_id).with_for_update()
        )
        records = list(_records(ctx, store, row.id))
        known: set[str] = set()
        for record in records:
            known |= owned_token_accounts(record, row.address)
            known |= set(solana.recorded_token_accounts(record) or ())
        own = _own_of(ctx.conn, row.account_id, Chain.SOLANA)
        own.setdefault(row.address, _Own(row.id, bytes(row.address_hmac).hex()))
        native = chain_asset(ctx, AssetKey(chain=Chain.SOLANA))
        cctx = _context(
            ctx.conn, Chain.SOLANA, row.address, row.account_id, own, native.id, None, hmac_key
        )
        seen: set[str] = set()
        fixes: list[_Fix] = []
        for record in records:
            if not solana.readable_meta(record) or not known:
                continue
            signature = solana.signature(record)
            if signature in seen:
                continue
            if not solana.moves_rowless_token_account(record, known):
                continue
            seen.add(signature)
            out.records += 1
            amended = _amend(record, known)
            tx = SolanaRpc.chain_tx(
                amended, {row.address}, solana.recorded_token_accounts(amended), strict=False
            )
            planned = _plan(
                ctx,
                amended,
                classify(tx, cctx),
                row.address,
                row.id,
                row.account_id,
                own[row.address],
            )
            if planned == "unchanged":
                out.unchanged += 1
            elif planned == "skipped":
                out.skipped += 1
            else:
                assert isinstance(planned, _Fix)
                fixes.append(planned)
        for fix in fixes:
            if fix.carry:
                out.carried += 1
            else:
                out.proposed += 1
        if dry_run or not fixes:
            continue
        document_id = _store(ctx, store, fixes)
        for fix in fixes:
            _write(ctx, row.account_id, row.id, own[row.address], fix, document_id)
    return out
