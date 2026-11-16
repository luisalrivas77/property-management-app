"""A Binance import's times moved to UTC (docs/phases/phase-6.md D178 amended (4), 16 Nov 2026).

Binance writes its exports in the time zone the person chose when exporting, and names that zone
only in the file names (binance_csv.utc_offset). Until the amendment every time was read as UTC,
so the household's UTC+1 exports put every Binance row an hour late: a coin withdrawn at 15:04
in the file stood an hour after the chain's receipt at 14:03 UTC, the lots placed the transfer
before the purchase that funded it, and the wallet's lot had no cost (Luis: „I'm still confused
with the price transaction part“).

An import's zone is known when its account.movements_imported event names an offset that was
not assumed (the import read the zone from a name), or when an account.movement_times_corrected
event names the import (its times were moved here). Every other Binance import, each one read
before the amendment and each one whose names named no zone, is unknown; the exchange page shows
the to-do exchange.time_zone_unknown while one is (`zone_unknown`).

- `correct_by_name(ctx, account_id)`: each unknown import whose stored file name names a zone
  moved by it. Run by `hausbuch repair-exchange-times` and at the start of every Binance upload.
  The member name inside a ZIP is not stored, so an import whose upload name named no zone stays
  unknown here.
- `correct_overlapping(ctx, account_id, hashes, offset, document_id)`: a later export whose
  name names its zone and that carries rows of an unknown import (the same movement_hash, which
  is built from the time as the file writes it, so the same rows of the same zone) moves that
  import by its zone: the to-do's answer, „Transaktionsverlauf erneut hochladen“.
- `repair_times(ctx)`: the command over every Binance account of the workspace, then the
  relinks the import runs, so nothing a link needs is left open.

How one import is moved (`_correct_import`), append-only and keeping everything a person or the
matcher decided: every live row descending from a row the import wrote (by its movement_hash and
document) whose time is still the one the file wrote is superseded by a copy at the UTC time,
status, kind, amounts and hash fields unchanged; a trade's group is superseded by one at the new
time holding the copies; a balance correction booked at the import's last row moves with it; a
deposit the app wrote for a withdrawal on a balance-only chain (phase 7 D181 (c) amended) moves
with its withdrawal; every current transfer link that holds a moved row is replaced by one
holding the copy, matched_by, kind, reason and what it confirmed kept (the transfer.linked
payload with the ids moved), the old link superseded by it with its transfer.unlinked. Then the
import's first and last row move, and account.movement_times_corrected records the zone. Each
superseded row has its own movement.superseded naming the file that gave the zone; nothing names
an amount, an address or a file name.

The stored movement_hash never changes: it is built from the time as the file writes it
(rules.exchange.binance_movements), so the same export uploaded again, or a longer one of the
same zone, still skips every stored row.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, Row, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import movement_import as import_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.rules import exchange as rules
from hausbuch.rules.review import CHAIN_WRITTEN_DEPOSIT
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import (
    EventType,
    ExchangeTradeGrouped,
    MovementGroupRecorded,
    MovementRecorded,
    MovementSuperseded,
    MovementTimesCorrected,
    TransferLinked,
    TransferUnlinked,
)
from hausbuch.schemas.household import (
    MovementImportFormat,
    MovementKind,
    MovementSource,
    MovementStatus,
)

from ..context import CommandContext
from ..events import append_event
from . import binance_csv


def _minutes(offset: timedelta) -> int:
    return int(offset.total_seconds() // 60)


# --- Which imports have a known zone ---------------------------------------------------------


def _binance_imports(conn: Connection, account_id: UUID) -> list[Row]:
    i = import_table.c
    return conn.execute(
        select(import_table)
        .where(i.account_id == account_id, i.format == MovementImportFormat.BINANCE_CSV.value)
        .order_by(i.first_at, i.created_at, i.id)
    ).all()


def _known(conn: Connection, account_id: UUID) -> set[UUID]:
    """The ids of the account's imports whose zone is known (the module docstring)."""
    e = event_table.c
    rows = conn.execute(
        select(e.type, e.payload).where(
            e.entity_id == account_id,
            e.type.in_(
                [
                    EventType.ACCOUNT_MOVEMENTS_IMPORTED.value,
                    EventType.ACCOUNT_MOVEMENT_TIMES_CORRECTED.value,
                ]
            ),
        )
    ).all()
    known = set()
    for r in rows:
        payload = r.payload or {}
        if r.type == EventType.ACCOUNT_MOVEMENT_TIMES_CORRECTED.value or (
            payload.get("time_zone_assumed") is False
            and payload.get("utc_offset_minutes") is not None
        ):
            known.add(UUID(payload["import_id"]))
    return known


def unknown_imports(conn: Connection, account_id: UUID) -> list[Row]:
    """The account's Binance imports whose zone is unknown, oldest first."""
    known = _known(conn, account_id)
    return [r for r in _binance_imports(conn, account_id) if r.id not in known]


def zone_unknown(conn: Connection, account_id: UUID) -> bool:
    """Whether the exchange page shows exchange.time_zone_unknown."""
    return bool(unknown_imports(conn, account_id))


def account_offset(conn: Connection, account_id: UUID) -> timedelta | None:
    """The zone of the account's latest import whose zone is known, for a withdrawal history
    whose names name none (Binance writes both files of one account in the zone chosen in its
    settings); None when no import's zone is known."""
    known = _known(conn, account_id)
    e = event_table.c
    offset = None
    for payload in conn.execute(
        select(e.payload)
        .where(
            e.entity_id == account_id,
            e.type.in_(
                [
                    EventType.ACCOUNT_MOVEMENTS_IMPORTED.value,
                    EventType.ACCOUNT_MOVEMENT_TIMES_CORRECTED.value,
                ]
            ),
        )
        .order_by(e.seq)
    ).scalars():
        payload = payload or {}
        minutes = payload.get("utc_offset_minutes")
        if (
            minutes is not None
            and UUID(payload["import_id"]) in known
            and payload.get("time_zone_assumed") is not True
        ):
            offset = timedelta(minutes=minutes)
    return offset


# --- Moving one import ------------------------------------------------------------------------


@dataclass
class _Moves:
    """What one correction moved: old live id → new id, for movements and groups."""

    movements: dict[UUID, UUID] = field(default_factory=dict)
    groups: dict[UUID, UUID] = field(default_factory=dict)
    links: int = 0


def _tips(conn: Connection, account_id: UUID, roots: Sequence[Row]) -> dict[UUID, Row]:
    """Each root's live descendant (superseded_by followed to its end) → the root."""
    m = movement_table.c
    chain = dict(
        conn.execute(select(m.id, m.superseded_by).where(m.account_id == account_id)).tuples().all()
    )
    out: dict[UUID, Row] = {}
    for root in roots:
        at, seen = root.id, set()
        while chain.get(at) is not None and at not in seen:
            seen.add(at)
            at = chain[at]
        out[at] = root
    return out


def _copy_movement(ctx: CommandContext, old: Row, changes: dict, document_id: UUID | None) -> UUID:
    """A copy of the live row `old` with `changes` that supersedes it, every other column kept
    (the movement_hash stays on the original, so a re-import still finds it), with its
    movement.superseded (naming the file that gave the zone) and the copy's movement.confirmed
    or .proposed."""
    m = movement_table.c
    skip = {"id", "superseded_by", "created_at", "movement_hash"}
    values = {c.name: getattr(old, c.name) for c in movement_table.c if c.name not in skip}
    values |= changes
    new_id = uuid4()
    ctx.conn.execute(
        insert(movement_table).values(id=new_id, movement_hash=None, created_at=ctx.now, **values)
    )
    ctx.conn.execute(update(movement_table).where(m.id == old.id).values(superseded_by=new_id))
    append_event(
        ctx,
        EventType.MOVEMENT_SUPERSEDED,
        old.id,
        MovementSuperseded(superseded_by=new_id, document_id=document_id),
    )
    status = MovementStatus(values["status"])
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED
        if status is MovementStatus.CONFIRMED
        else EventType.MOVEMENT_PROPOSED,
        new_id,
        MovementRecorded(
            account_id=values["account_id"],
            asset_id=values["asset_id"],
            kind=MovementKind(values["kind"]),
            source=MovementSource(values["source"]),
            document_id=values["document_id"],
        ),
    )
    return new_id


def _copy_group(ctx: CommandContext, group: Row, occurred_at: datetime) -> UUID:
    """A copy of the live group at `occurred_at` that supersedes it (0041's group_forward lets
    only status and superseded_by change)."""
    g = group_table.c
    skip = {"id", "superseded_by", "created_at", "occurred_at"}
    values = {c.name: getattr(group, c.name) for c in group_table.c if c.name not in skip}
    new_id = uuid4()
    ctx.conn.execute(
        insert(group_table).values(id=new_id, occurred_at=occurred_at, created_at=ctx.now, **values)
    )
    ctx.conn.execute(update(group_table).where(g.id == group.id).values(superseded_by=new_id))
    return new_id


def _move_rows(
    ctx: CommandContext, targets: dict[UUID, datetime], document_id: UUID, moves: _Moves
) -> None:
    """Every live row of `targets` (id → its UTC time) superseded at that time; a row in a
    group moves with its whole group: a trade's (chain None) by its trade_recorded event, a
    written deposit's by a movement_group.confirmed routed chain_written_deposit, as the
    import and phase 7 wrote them."""
    m, g = movement_table.c, group_table.c
    rows = {
        r.id: r
        for r in ctx.conn.execute(
            select(movement_table)
            .where(m.id.in_(list(targets)), m.superseded_by.is_(None))
            .order_by(m.id)
            .with_for_update()
        )
    }
    group_ids = sorted({r.group_id for r in rows.values() if r.group_id is not None}, key=str)
    for gid in group_ids:
        group = ctx.conn.execute(select(group_table).where(g.id == gid).with_for_update()).one()
        legs = ctx.conn.execute(
            select(movement_table)
            .where(m.group_id == gid, m.superseded_by.is_(None))
            .order_by(m.occurred_at, m.id)
            .with_for_update()
        ).all()
        at = min(targets.get(leg.id, leg.occurred_at) for leg in legs)
        new_group = group.id
        if group.superseded_by is None and at != group.occurred_at:
            new_group = _copy_group(ctx, group, at)
            moves.groups[group.id] = new_group
        new_ids = []
        for leg in legs:
            changes: dict = {"group_id": new_group}
            if leg.id in targets:
                changes["occurred_at"] = targets[leg.id]
            if new_group == group.id and "occurred_at" not in changes:
                new_ids.append(leg.id)
                continue
            new_ids.append(_copy_movement(ctx, leg, changes, document_id))
            moves.movements[leg.id] = new_ids[-1]
        if new_group == group.id:
            continue
        if group.chain is None:
            append_event(
                ctx,
                EventType.MOVEMENT_GROUP_TRADE_RECORDED,
                new_group,
                ExchangeTradeGrouped(
                    account_id=group.account_id,
                    kind=group.kind,
                    movement_ids=new_ids,
                    document_id=document_id,
                ),
            )
        else:
            append_event(
                ctx,
                EventType.MOVEMENT_GROUP_CONFIRMED,
                new_group,
                MovementGroupRecorded(
                    account_id=group.account_id,
                    chain=group.chain,
                    kind=group.kind,
                    reason=CHAIN_WRITTEN_DEPOSIT,
                ),
            )
    for row in rows.values():
        if row.group_id is None and row.id not in moves.movements:
            moves.movements[row.id] = _copy_movement(
                ctx, row, {"occurred_at": targets[row.id]}, document_id
            )


def _written_deposit_targets(
    conn: Connection, links: Sequence[Row], targets: dict[UUID, datetime]
) -> dict[UUID, datetime]:
    """The deposits the app wrote for a moved withdrawal (phase 7 D181 (c) amended), still at
    the withdrawal's old time, each → the withdrawal's new time."""
    from ..chain.transfers import _written_deposits

    other = {}
    for link in links:
        if link.out_movement_id in targets and link.in_movement_id is not None:
            other[link.in_movement_id] = link.out_movement_id
    if not other:
        return {}
    m = movement_table.c
    old_at = dict(
        conn.execute(select(m.id, m.occurred_at).where(m.id.in_(list(set(other.values())))))
        .tuples()
        .all()
    )
    return {
        d.id: targets[other[d.id]]
        for d in _written_deposits(conn, other)
        if d.occurred_at == old_at.get(other[d.id])
    }


def _move_links(
    ctx: CommandContext, links: Sequence[Row], moves: _Moves, payloads: dict[UUID, dict]
) -> None:
    """Every current link that holds a moved row replaced by one holding the copy: kind,
    matched_by and its transfer.linked payload (reason, what it confirmed, a deposit IBAN's
    outcome) kept with the ids moved; the old link superseded by it, with transfer.unlinked."""
    c = link_table.c
    ids = moves.movements | moves.groups
    for link in links:
        legs = {
            "out_transaction_id": link.out_transaction_id,
            "out_movement_id": moves.movements.get(link.out_movement_id, link.out_movement_id),
            "in_transaction_id": link.in_transaction_id,
            "in_movement_id": moves.movements.get(link.in_movement_id, link.in_movement_id),
        }
        if (legs["out_movement_id"], legs["in_movement_id"]) == (
            link.out_movement_id,
            link.in_movement_id,
        ):
            continue
        new_id = uuid4()
        ctx.conn.execute(
            insert(link_table).values(
                id=new_id,
                workspace_id=ctx.workspace_id,
                kind=link.kind,
                matched_by=link.matched_by,
                created_at=ctx.now,
                **legs,
            )
        )
        payload = TransferLinked.model_validate(
            payloads.get(link.id) or {"kind": link.kind, "matched_by": link.matched_by}
        )
        append_event(
            ctx,
            EventType.TRANSFER_LINKED,
            new_id,
            payload.model_copy(
                update={
                    **legs,
                    "confirmed_movements": [ids.get(i, i) for i in payload.confirmed_movements],
                    "confirmed_groups": [ids.get(i, i) for i in payload.confirmed_groups],
                }
            ),
        )
        ctx.conn.execute(update(link_table).where(c.id == link.id).values(superseded_by=new_id))
        append_event(
            ctx, EventType.TRANSFER_UNLINKED, link.id, TransferUnlinked(superseded_by=new_id)
        )
        moves.links += 1


def _link_payloads(conn: Connection, link_ids: Iterable[UUID]) -> dict[UUID, dict]:
    ids = list(link_ids)
    if not ids:
        return {}
    e = event_table.c
    return {
        r.entity_id: (r.payload or {})
        for r in conn.execute(
            select(e.entity_id, e.payload)
            .where(e.type == EventType.TRANSFER_LINKED.value, e.entity_id.in_(ids))
            .order_by(e.seq)
        )
    }


def _corrections(conn: Connection, imported: Row) -> list[UUID]:
    """The balance corrections booked on the import (account.balance_corrected)."""
    e = event_table.c
    return [
        UUID(p["movement_id"])
        for p in conn.execute(
            select(e.payload).where(
                e.entity_id == imported.account_id,
                e.type == EventType.ACCOUNT_BALANCE_CORRECTED.value,
                e.payload["import_id"].astext == str(imported.id),
            )
        ).scalars()
    ]


def _correct_import(
    ctx: CommandContext, imported: Row, offset: timedelta, document_id: UUID
) -> int:
    """The import moved by `offset` (the module docstring); the rows moved."""
    from ..transfers import _current_links, _leg_ids, _lock

    _lock(ctx)  # links are replaced below (services.transfers._lock's order: the account first)
    m = movement_table.c
    moves = _Moves()
    targets: dict[UUID, datetime] = {}
    if offset:
        roots = ctx.conn.execute(
            select(m.id, m.occurred_at).where(
                m.account_id == imported.account_id,
                m.document_id == imported.document_id,
                m.movement_hash.is_not(None),
            )
        ).all()
        tips = _tips(ctx.conn, imported.account_id, roots)
        corrections = _tip_ids(ctx.conn, imported)
        now_at = dict(
            ctx.conn.execute(
                select(m.id, m.occurred_at).where(
                    m.id.in_([*tips, *corrections]), m.superseded_by.is_(None)
                )
            )
            .tuples()
            .all()
        )
        # A row whose time is no longer the file's (moved already) keeps it: idempotent.
        targets = {
            tip: root.occurred_at - offset
            for tip, root in tips.items()
            if now_at.get(tip) == root.occurred_at
        }
        targets |= {
            tip: imported.last_at - offset
            for tip in corrections
            if now_at.get(tip) == imported.last_at
        }
    if targets:
        links = [r for r in _current_links(ctx.conn) if set(_leg_ids(r)) & set(targets)]
        deposits = _written_deposit_targets(ctx.conn, links, targets)
        links += [
            r
            for r in _current_links(ctx.conn)
            if set(_leg_ids(r)) & set(deposits) and r not in links
        ]
        payloads = _link_payloads(ctx.conn, [r.id for r in links])
        _move_rows(ctx, targets | deposits, document_id, moves)
        _move_links(ctx, links, moves, payloads)
        ctx.conn.execute(
            update(import_table)
            .where(import_table.c.id == imported.id)
            .values(
                first_at=imported.first_at - offset,
                last_at=imported.last_at - offset,
                updated_at=ctx.now,
            )
        )
    append_event(
        ctx,
        EventType.ACCOUNT_MOVEMENT_TIMES_CORRECTED,
        imported.account_id,
        MovementTimesCorrected(
            import_id=imported.id,
            document_id=document_id,
            utc_offset_minutes=_minutes(offset),
            movements_corrected=len(moves.movements),
            links_moved=moves.links,
        ),
    )
    return len(moves.movements)


def _tip_ids(conn: Connection, imported: Row) -> list[UUID]:
    """The live rows of the import's balance corrections (dated at its last row)."""
    roots = _corrections(conn, imported)
    if not roots:
        return []
    m = movement_table.c
    rows = conn.execute(select(m.id, m.occurred_at).where(m.id.in_(roots))).all()
    return list(_tips(conn, imported.account_id, rows))


# --- The three entries --------------------------------------------------------------------------


@dataclass(frozen=True)
class TimesCorrected:
    """What a run moved: the imports whose zone it recorded, the rows it moved, the imports
    left unknown (their names named no zone: the exchange page's to-do)."""

    imports: int = 0
    movements: int = 0
    unknown: int = 0

    def __add__(self, other: "TimesCorrected") -> "TimesCorrected":
        return TimesCorrected(
            self.imports + other.imports,
            self.movements + other.movements,
            self.unknown + other.unknown,
        )


def correct_by_name(ctx: CommandContext, account_id: UUID) -> TimesCorrected:
    """Each unknown import of the account whose stored upload name names a zone moved by it.
    The caller holds the account's row (the upload's lock, or repair_times')."""
    d = document_table.c
    imports = moved = unknown = 0
    for imported in unknown_imports(ctx.conn, account_id):
        name = ctx.conn.execute(
            select(d.original_filename).where(d.id == imported.document_id)
        ).scalar_one_or_none()
        offset = binance_csv.utc_offset(None, name)
        if offset is None:
            unknown += 1
            continue
        moved += _correct_import(ctx, imported, offset, imported.document_id)
        imports += 1
    return TimesCorrected(imports, moved, unknown)


def correct_overlapping(
    ctx: CommandContext,
    account_id: UUID,
    hashes: Iterable[str],
    offset: timedelta,
    document_id: UUID,
) -> TimesCorrected:
    """Each unknown import of the account that wrote a row of `hashes` (a new export of the
    zone `offset`, stored as `document_id`) moved by that zone: the same hash is the same time
    as the file writes it, so the earlier file was written in the same zone."""
    hashes = set(hashes)
    unknown = unknown_imports(ctx.conn, account_id)
    if not hashes or not unknown:
        return TimesCorrected()
    m = movement_table.c
    docs = set(
        ctx.conn.execute(
            select(m.document_id).where(
                m.account_id == account_id, m.movement_hash.in_(sorted(hashes))
            )
        ).scalars()
    )
    imports = moved = 0
    for imported in unknown:
        if imported.document_id in docs:
            moved += _correct_import(ctx, imported, offset, document_id)
            imports += 1
    return TimesCorrected(imports, moved)


@dataclass(frozen=True)
class RepairResult:
    """`repair_times`' answer: what moved, and what the relinks after it did."""

    corrected: TimesCorrected
    accounts: int
    linked: int
    reopened: int
    ended: int


def repair_times(ctx: CommandContext) -> RepairResult:
    """`hausbuch repair-exchange-times`: correct_by_name over every Binance account of the
    workspace (each account's row locked first), then the relinks an import runs (D162's on
    each account, D184's over the workspace), which find every moved link current and so
    reopen, end and write nothing (their counts are reported). Idempotent: a second run finds
    every import's zone known."""
    from ..chain import transfers as chain_transfers
    from ..transfers import relink

    acc = account_table.c
    accounts = (
        ctx.conn.execute(
            select(acc.id)
            .where(acc.kind == AccountKind.EXCHANGE.value, acc.provider == rules.BINANCE)
            .order_by(acc.id)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    total = TimesCorrected()
    linked = 0
    for account_id in accounts:
        total = total + correct_by_name(ctx, account_id)
    for account_id in accounts:
        linked += relink(ctx, account_id)
    chain = chain_transfers.relink(ctx)
    return RepairResult(
        corrected=total,
        accounts=len(accounts),
        linked=linked + chain.linked,
        reopened=chain.reopened,
        ended=chain.ended,
    )
