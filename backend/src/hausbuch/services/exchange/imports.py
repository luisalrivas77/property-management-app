"""The exchange account's imports (docs/phases/phase-6.md D166 as amended 13 Nov 2026, D175,
D178; step 5a). The parsers and the pure rules are `bison_csv`, `binance_csv` and
`rules/exchange`; this module stores what they read.

- `import_export`: one upload on an exchange account. A transaction export (BISON's, or
  Binance's transaction history) is stored as the immutable original (typed by code as a
  statement of subtype exchange_export, never classified or sent to the model, D166), gets a
  `movement_import` row, and adds every movement whose hash is not stored yet (a longer
  export re-uploaded adds only its new rows, D178 (5)); an EUR deposit or withdrawal is then
  linked to its bank leg by D162. Binance's withdrawal history adds no import: it completes
  each open withdrawal with its network, fee and transaction hash by a new row that
  supersedes the old one (D178 (4)).
- `declare_holdings`: the balances the exchange shows, typed once per import; Σ movements
  per asset to the import's last row against them (D166, D178 (6)), never bridged.
- `confirm_withdrawals`, `confirm_movement`, `answer_movement`: a person's answers.
- `exchange_overview`, `exchange_movements`, `exchange_inbox_items`: the reads.

Never stored (D178 (2)): Binance's User ID and the withdrawal Address; the parsers never
read them into a value, so no movement, event, message or log can carry them. The original
file is kept as uploaded (principle 6).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, Row, func, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import movement_import as import_table
from hausbuch.rules import errors as E
from hausbuch.rules import exchange as rules
from hausbuch.rules.errors import RuleError
from hausbuch.rules.household.assets import registry
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.common import TAX_YEAR_MIN
from hausbuch.schemas.document import Document, DocumentTyping
from hausbuch.schemas.enums import (
    AccountKind,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    EntityType,
    ImportStatus,
)
from hausbuch.schemas.events import (
    AssetRecorded,
    EventType,
    MovementImportChecked,
    MovementImportRecorded,
    MovementRecorded,
    MovementSuperseded,
)
from hausbuch.schemas.household import (
    AnswerMovementBody,
    AssetKind,
    DeclareHoldingsBody,
    ExchangeImport,
    ExchangeMovement,
    ExchangeMovementRow,
    ExchangeOverview,
    ExchangePosition,
    ExchangeUpload,
    HoldingSource,
    ImportBalance,
    MovementImport,
    MovementImportFormat,
    MovementKind,
    MovementSource,
    MovementStatus,
    WithdrawalsConfirmed,
)
from hausbuch.storage import BlobStore

from ..context import CommandContext
from ..documents import type_document, upload_document
from ..errors import RuleViolationError
from ..events import append_event
from ..lookup import require_row
from . import ExportKind, binance_csv, bison_csv, read_export

MOVEMENT_IMPORT = "movement_import"  # require_row's name for a movement_import row

_PROVIDER = {
    ExportKind.BISON: rules.BISON,
    ExportKind.BINANCE_TRANSACTIONS: rules.BINANCE,
    ExportKind.BINANCE_WITHDRAWALS: rules.BINANCE,
}
# The movement_import.format each transaction export is stored as. Binance's needs its own
# value in the format CHECK of migration 0025, which allows bison_csv only: until the
# migration that adds it, a Binance transaction history is refused with
# exchange.format_not_available before anything is written.
_FORMAT_VALUE = {ExportKind.BISON: "bison_csv", ExportKind.BINANCE_TRANSACTIONS: "binance_csv"}
_INWARD_ANSWERS = (
    MovementKind.CASHBACK_REWARD,
    MovementKind.STAKING_REWARD,
    MovementKind.INTERNAL_TRANSFER_IN,
)
_OUTWARD_ANSWERS = (MovementKind.INTERNAL_TRANSFER_OUT, MovementKind.DISPOSAL, MovementKind.FEE)


def _format(kind: ExportKind) -> MovementImportFormat:
    value = _FORMAT_VALUE[kind]
    try:
        return MovementImportFormat(value)
    except ValueError:
        raise RuleError(
            E.EXCHANGE_FORMAT_NOT_AVAILABLE,
            "this export can be imported once its database migration is applied",
        ) from None


# --- Reading the account's movements ---------------------------------------------------------


@dataclass(frozen=True)
class _Stored:
    """A current movement of the account as rules.exchange reads it (the Moved protocol)."""

    id: UUID
    asset_id: UUID
    asset: str
    kind: MovementKind
    status: MovementStatus
    quantity: Decimal
    eur_amount: Decimal | None
    occurred_at: datetime
    tx_hash: str | None
    document_id: UUID | None


def _current(conn: Connection, account_id: UUID, *where) -> list[_Stored]:  # noqa: ANN002
    m, a = movement_table.c, asset_table.c
    rows = conn.execute(
        select(
            m.id,
            m.asset_id,
            a.symbol,
            m.kind,
            m.status,
            m.quantity,
            m.eur_amount,
            m.occurred_at,
            m.tx_hash,
            m.document_id,
        )
        .join(asset_table, a.id == m.asset_id)
        .where(m.account_id == account_id, m.superseded_by.is_(None), *where)
        .order_by(m.occurred_at, m.id)
    ).all()
    return [
        _Stored(
            id=r.id,
            asset_id=r.asset_id,
            asset=r.symbol or "",
            kind=MovementKind(r.kind),
            status=MovementStatus(r.status),
            quantity=r.quantity,
            eur_amount=r.eur_amount,
            occurred_at=r.occurred_at,
            tx_hash=r.tx_hash,
            document_id=r.document_id,
        )
        for r in rows
    ]


def _open_withdrawals(stored: Iterable[_Stored]) -> list[_Stored]:
    """Crypto withdrawals without a transaction hash (D178 (4))."""
    return [
        s
        for s in stored
        if s.kind is MovementKind.INTERNAL_TRANSFER_OUT and s.asset != rules.EUR and not s.tx_hash
    ]


def _require_exchange(conn: Connection, account_id: UUID, *, for_update: bool = False) -> Row:
    account = require_row(
        conn, account_table, EntityType.ACCOUNT, account_id, for_update=for_update
    )
    if account.kind != AccountKind.EXCHANGE.value:
        raise RuleError(E.EXCHANGE_NOT_AN_EXCHANGE, "the account is no exchange account")
    return account


# --- Assets -----------------------------------------------------------------------------------


def asset_for_symbol(ctx: CommandContext, symbol: str) -> UUID:
    """The workspace's asset of a symbol, created on first use: EUR as cash, any other as a
    coin named (and given its price ids) from the asset registry when it lists it."""
    found = ctx.conn.execute(
        select(asset_table.c.id).where(asset_table.c.symbol == symbol)
    ).one_or_none()
    if found is not None:
        return found.id
    if symbol == rules.EUR:
        kind, name, ids = AssetKind.CASH, "Euro", {}
    else:
        coin = registry().coin(symbol)
        kind, name = AssetKind.CRYPTO, coin.name if coin else symbol
        ids = {"coingecko_id": coin.coingecko_id, "kraken_pair": coin.kraken_pair} if coin else {}
    asset_id = ctx.conn.execute(
        insert(asset_table)
        .values(
            workspace_id=ctx.workspace_id,
            kind=kind.value,
            symbol=symbol,
            name=name,
            **ids,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(asset_table.c.id)
    ).scalar_one()
    append_event(
        ctx, EventType.ASSET_CREATED, asset_id, AssetRecorded(kind=kind, symbol=symbol, name=name)
    )
    return asset_id


# --- Import -----------------------------------------------------------------------------------


_IMPORT_COLUMNS = tuple(import_table.c)


def _to_import(row: Row) -> MovementImport:
    return MovementImport.model_validate(row, from_attributes=True)


def _store_original(
    ctx: CommandContext,
    store: BlobStore,
    data: bytes,
    media_type: str,
    original_filename: str | None,
    last: datetime,
) -> Document:
    document = upload_document(
        ctx,
        store,
        data,
        media_type=media_type,
        source=DocumentSource.UPLOAD,
        original_filename=original_filename,
    )
    if document.typing is None:
        # Typed by code only when untyped: a person's own typing of the same file stays.
        day = last.date()
        type_document(
            ctx,
            document.id,
            DocumentTyping(
                type=DocumentType.STATEMENT,
                subtype=DocumentSubtype.EXCHANGE_EXPORT,
                document_date=day,
                tax_year=day.year if day.year >= TAX_YEAR_MIN else None,
            ),
        )
    return document


def _insert_movement(
    ctx: CommandContext, account_id: UUID, movement: ExchangeMovement, document_id: UUID
) -> UUID:
    asset_id = asset_for_symbol(ctx, movement.asset)
    movement_id = ctx.conn.execute(
        insert(movement_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            asset_id=asset_id,
            kind=movement.kind.value,
            quantity=movement.quantity,
            eur_amount=movement.eur_amount,
            fee_eur=movement.fee_eur,
            fee_quantity=movement.fee_quantity,
            occurred_at=movement.occurred_at,
            external_ref=movement.external_ref,
            network=movement.network,
            tx_hash=movement.tx_hash,
            movement_hash=movement.movement_hash,
            source=MovementSource.CSV.value,
            document_id=document_id,
            status=movement.status.value,
            created_at=ctx.now,
        )
        .returning(movement_table.c.id)
    ).scalar_one()
    event = (
        EventType.MOVEMENT_CONFIRMED
        if movement.status is MovementStatus.CONFIRMED
        else EventType.MOVEMENT_PROPOSED
    )
    append_event(
        ctx,
        event,
        movement_id,
        MovementRecorded(
            account_id=account_id,
            asset_id=asset_id,
            kind=movement.kind,
            source=MovementSource.CSV,
            document_id=document_id,
        ),
    )
    return movement_id


def import_export(
    ctx: CommandContext,
    store: BlobStore,
    account_id: UUID,
    data: bytes,
    *,
    media_type: str,
    original_filename: str | None = None,
) -> ExchangeUpload:
    """One file uploaded on an exchange account (see the module docstring). Refused before
    anything is written (RuleError): an account that is no exchange, a file that is no export
    of BISON or Binance (exchange.unreadable, exchange.binance_spot_history), the other
    exchange's file (exchange.account_mismatch), an export without rows, and a withdrawal
    history before any transaction history (exchange.transactions_first). The same file
    again returns its stored import and writes nothing."""
    account = _require_exchange(ctx.conn, account_id, for_update=True)
    export = read_export(data)
    if account.provider != _PROVIDER[export.kind]:
        raise RuleError(
            E.EXCHANGE_ACCOUNT_MISMATCH,
            f"this is an export of {_PROVIDER[export.kind]}, the account is {account.provider}",
        )
    if export.kind is ExportKind.BINANCE_WITHDRAWALS:
        return _complete_withdrawals(
            ctx, store, account_id, data, export.text, media_type, original_filename
        )
    fmt = _format(export.kind)
    if export.kind is ExportKind.BISON:
        movements = rules.bison_movements(bison_csv.parse_text(export.text))
    else:
        movements = rules.binance_movements(binance_csv.parse_transactions_text(export.text))
    if not movements:
        raise RuleError(E.EXCHANGE_UNREADABLE, "the export holds no row")
    first = min(m.occurred_at for m in movements)
    last = max(m.occurred_at for m in movements)
    document = _store_original(ctx, store, data, media_type, original_filename, last)
    existing = ctx.conn.execute(
        select(*_IMPORT_COLUMNS).where(
            import_table.c.account_id == account_id, import_table.c.document_id == document.id
        )
    ).one_or_none()
    if existing is not None:
        return ExchangeUpload(movement_import=_to_import(existing))

    m = movement_table.c
    stored = set(
        ctx.conn.execute(
            select(m.movement_hash).where(
                m.account_id == account_id,
                m.movement_hash.in_([mv.movement_hash for mv in movements]),
            )
        ).scalars()
    )
    new = [mv for mv in movements if mv.movement_hash not in stored]
    imported = ctx.conn.execute(
        insert(import_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            document_id=document.id,
            format=fmt.value,
            rows_total=len(movements),
            rows_new=len(new),
            rows_skipped=len(movements) - len(new),
            first_at=first,
            last_at=last,
            status=ImportStatus.IMPORTED.value,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*_IMPORT_COLUMNS)
    ).one()
    asset_for_symbol(ctx, rules.EUR)  # the EUR balance is typed even with no EUR row
    for movement in new:
        _insert_movement(ctx, account_id, movement, document.id)
    append_event(
        ctx,
        EventType.ACCOUNT_MOVEMENTS_IMPORTED,
        account_id,
        MovementImportRecorded(
            import_id=imported.id,
            document_id=document.id,
            format=fmt,
            rows_total=imported.rows_total,
            rows_new=imported.rows_new,
            rows_skipped=imported.rows_skipped,
        ),
        effective_date=last.date(),
    )
    from ..transfers import relink  # transfers imports the statement services

    relink(ctx, account_id)  # an EUR deposit or withdrawal finds its bank leg (D162)
    return ExchangeUpload(
        movement_import=_to_import(imported),
        withdrawals_open=len(_open_withdrawals(_current(ctx.conn, account_id))),
    )


def _supersede(
    ctx: CommandContext,
    old_id: UUID,
    changes: dict,
    *,
    document_id: UUID | None = None,
) -> UUID:
    """A new row with `changes` replaces the movement (the old one kept, superseded_by); the
    new row carries no movement_hash, so a re-import still finds the original's."""
    m = movement_table.c
    old = ctx.conn.execute(select(movement_table).where(m.id == old_id)).one()
    values = {
        c: getattr(old, c)
        for c in (
            "account_id",
            "asset_id",
            "kind",
            "quantity",
            "eur_amount",
            "fee_eur",
            "fee_quantity",
            "tax_withheld_eur",
            "occurred_at",
            "external_ref",
            "network",
            "tx_hash",
            "source",
            "document_id",
            "transaction_id",
            "status",
        )
    }
    values |= changes
    new_id = ctx.conn.execute(
        insert(movement_table)
        .values(workspace_id=ctx.workspace_id, movement_hash=None, created_at=ctx.now, **values)
        .returning(m.id)
    ).scalar_one()
    ctx.conn.execute(update(movement_table).where(m.id == old_id).values(superseded_by=new_id))
    append_event(
        ctx,
        EventType.MOVEMENT_SUPERSEDED,
        old_id,
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


def _complete_withdrawals(
    ctx: CommandContext,
    store: BlobStore,
    account_id: UUID,
    data: bytes,
    text: str,
    media_type: str,
    original_filename: str | None,
) -> ExchangeUpload:
    """D178 (4): Binance's withdrawal history completes each open withdrawal of the account
    whose history row fits (rules.exchange.match_withdrawals) by a new row with the network,
    the fee in the coin and the transaction hash. A history row whose hash a movement of the
    account carries already completes nothing again. Run on every upload of the file, so
    the same history uploaded after a longer transaction export completes the new rows."""
    history = binance_csv.parse_withdrawals_text(text)
    current = _current(ctx.conn, account_id)
    if not current:
        raise RuleError(
            E.EXCHANGE_TRANSACTIONS_FIRST,
            "upload the transaction history first, then the withdrawal history",
        )
    if not history:
        raise RuleError(E.EXCHANGE_UNREADABLE, "the withdrawal history holds no completed row")
    known = {s.tx_hash for s in current if s.tx_hash}
    unused = [h for h in history if h.tx_hash not in known]
    open_rows = _open_withdrawals(current)
    found = rules.match_withdrawals(open_rows, unused)
    document = _store_original(
        ctx, store, data, media_type, original_filename, max(h.occurred_at for h in history)
    )
    for index, record in sorted(found.items()):
        _supersede(
            ctx,
            open_rows[index].id,
            {
                "fee_quantity": record.fee,
                "network": record.network,
                "tx_hash": record.tx_hash,
                "external_ref": record.tx_hash,
            },
            document_id=document.id,
        )
    return ExchangeUpload(
        withdrawals_completed=len(found), withdrawals_open=len(open_rows) - len(found)
    )


# --- Typed balances (D166, D178 (6)) ---------------------------------------------------------


def _held_by_asset(stored: Sequence[_Stored], conn: Connection) -> dict[UUID, Decimal]:
    """Σ per asset id, EUR with every trade's eur_amount (rules.exchange.positions)."""
    by_symbol = rules.positions(stored)
    ids = {s.asset: s.asset_id for s in stored}
    if rules.EUR in by_symbol and rules.EUR not in ids:
        eur = conn.execute(
            select(asset_table.c.id).where(asset_table.c.symbol == rules.EUR)
        ).scalar_one_or_none()
        if eur is not None:
            ids[rules.EUR] = eur
    return {ids[symbol]: units for symbol, units in by_symbol.items() if symbol in ids}


def _import_held(conn: Connection, imported: Row) -> dict[UUID, Decimal]:
    m = movement_table.c
    stored = _current(conn, imported.account_id, m.occurred_at <= imported.last_at)
    return _held_by_asset(stored, conn)


def declare_holdings(
    ctx: CommandContext, import_id: UUID, body: DeclareHoldingsBody
) -> ExchangeImport:
    """POST /movement-imports/{id}/holdings: the balance per asset the exchange shows at the
    import's last row. Every asset the movements leave non-zero must be typed
    (exchange.balance_missing); an asset the account never moved is refused
    (exchange.balance_unknown_asset). Each typed balance becomes a holding_statement (source
    user, as of the last row's day), replacing the import's earlier typing of that asset; the
    import is reconciled when every typed balance equals Σ movements, else unreconciled with
    the difference named per asset, never bridged. Typing again re-checks."""
    imported = require_row(ctx.conn, import_table, MOVEMENT_IMPORT, import_id, for_update=True)
    held = _import_held(ctx.conn, imported)
    typed = {b.asset_id: b.units for b in body.balances}
    unknown = [a for a in typed if a not in held]
    if unknown:
        raise RuleViolationError(
            [E.EXCHANGE_BALANCE_UNKNOWN_ASSET], "a balance was typed for an asset not on it"
        )
    missing = [a for a, units in held.items() if units != 0 and a not in typed]
    if missing:
        raise RuleViolationError([E.EXCHANGE_BALANCE_MISSING], "type every asset it holds")
    as_of = imported.last_at.date()
    h = holding_table.c
    earlier = {
        r.asset_id: r.id
        for r in ctx.conn.execute(
            select(h.id, h.asset_id).where(
                h.import_id == import_id,
                h.source == HoldingSource.USER.value,
                h.superseded_by.is_(None),
            )
        )
    }
    mismatched = 0
    for asset_id, units in typed.items():
        difference = units - held[asset_id]
        reconciled = difference == 0
        mismatched += not reconciled
        new_id = ctx.conn.execute(
            insert(holding_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=imported.account_id,
                asset_id=asset_id,
                as_of=as_of,
                declared_units=units,
                source=HoldingSource.USER.value,
                import_id=import_id,
                reconciled=reconciled,
                difference_units=difference,
                created_at=ctx.now,
            )
            .returning(h.id)
        ).scalar_one()
        if asset_id in earlier:
            ctx.conn.execute(
                update(holding_table).where(h.id == earlier[asset_id]).values(superseded_by=new_id)
            )
    status = ImportStatus.UNRECONCILED if mismatched else ImportStatus.RECONCILED
    ctx.conn.execute(
        update(import_table)
        .where(import_table.c.id == import_id)
        .values(
            status=status.value,
            error_code=E.EXCHANGE_UNRECONCILED if mismatched else None,
            updated_at=ctx.now,
        )
    )
    append_event(
        ctx,
        EventType.ACCOUNT_MOVEMENTS_UNRECONCILED
        if mismatched
        else EventType.ACCOUNT_MOVEMENTS_RECONCILED,
        imported.account_id,
        MovementImportChecked(
            import_id=import_id, assets_checked=len(typed), assets_mismatched=mismatched
        ),
    )
    return _exchange_import(ctx.conn, _refetch(ctx.conn, import_id))


def _refetch(conn: Connection, import_id: UUID) -> Row:
    return conn.execute(select(*_IMPORT_COLUMNS).where(import_table.c.id == import_id)).one()


# --- Answers ----------------------------------------------------------------------------------


def _require_proposed(conn: Connection, movement_id: UUID) -> Row:
    row = require_row(conn, movement_table, EntityType.MOVEMENT, movement_id, for_update=True)
    _require_exchange(conn, row.account_id)
    if row.superseded_by is not None or row.status != MovementStatus.PROPOSED.value:
        raise RuleViolationError(
            [E.EXCHANGE_MOVEMENT_DONE], "this movement was answered or corrected already"
        )
    return row


def _confirm(ctx: CommandContext, row: Row) -> None:
    m = movement_table.c
    ctx.conn.execute(
        update(movement_table).where(m.id == row.id).values(status=MovementStatus.CONFIRMED.value)
    )
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED,
        row.id,
        MovementRecorded(
            account_id=row.account_id,
            asset_id=row.asset_id,
            kind=MovementKind(row.kind),
            source=MovementSource(row.source),
            document_id=row.document_id,
        ),
    )


def confirm_movement(ctx: CommandContext, movement_id: UUID) -> None:
    """POST /movements/{id}/confirm: the proposed movement is right as it stands (a coin
    withdrawal went to an own wallet; a deposit was a bonus)."""
    _confirm(ctx, _require_proposed(ctx.conn, movement_id))


def answers_for(quantity: Decimal) -> list[MovementKind]:
    """The kinds a person may answer for a coin movement, by its direction."""
    return list(_INWARD_ANSWERS if quantity > 0 else _OUTWARD_ANSWERS)


def answer_movement(ctx: CommandContext, movement_id: UUID, body: AnswerMovementBody) -> UUID:
    """POST /movements/{id}/answer: what the proposed movement really was; a kind of the
    other direction is refused (exchange.answer_invalid). The same kind confirms it; another
    kind replaces it by a confirmed row of that kind. Returns the current movement's id."""
    row = _require_proposed(ctx.conn, movement_id)
    if body.kind not in answers_for(row.quantity):
        raise RuleViolationError(
            [E.EXCHANGE_ANSWER_INVALID], "this kind does not fit the movement's direction"
        )
    if body.kind.value == row.kind:
        _confirm(ctx, row)
        return row.id
    return _supersede(
        ctx, row.id, {"kind": body.kind.value, "status": MovementStatus.CONFIRMED.value}
    )


def confirm_withdrawals(ctx: CommandContext, import_id: UUID) -> WithdrawalsConfirmed:
    """POST /movement-imports/{id}/withdrawals/confirm: „Alle Auszahlungen gingen an eigene
    Wallets“ (D178 (4)): every proposed coin withdrawal read from the import's file is
    confirmed as it stands."""
    imported = require_row(ctx.conn, import_table, MOVEMENT_IMPORT, import_id, for_update=True)
    m = movement_table.c
    rows = ctx.conn.execute(
        select(movement_table)
        .where(
            m.account_id == imported.account_id,
            m.document_id == imported.document_id,
            m.kind == MovementKind.INTERNAL_TRANSFER_OUT.value,
            m.status == MovementStatus.PROPOSED.value,
            m.superseded_by.is_(None),
        )
        .order_by(m.occurred_at, m.id)
        .with_for_update()
    ).all()
    for row in rows:
        _confirm(ctx, row)
    return WithdrawalsConfirmed(confirmed=len(rows))


# --- Reads ------------------------------------------------------------------------------------


def _symbols(conn: Connection, ids: Iterable[UUID]) -> dict[UUID, Row]:
    a = asset_table.c
    wanted = list(set(ids))
    if not wanted:
        return {}
    return {r.id: r for r in conn.execute(select(a.id, a.symbol, a.name).where(a.id.in_(wanted)))}


def _exchange_import(conn: Connection, imported: Row) -> ExchangeImport:
    held = _import_held(conn, imported)
    h = holding_table.c
    typed = {
        r.asset_id: r
        for r in conn.execute(
            select(h.asset_id, h.declared_units, h.reconciled, h.difference_units).where(
                h.import_id == imported.id, h.superseded_by.is_(None)
            )
        )
    }
    names = _symbols(conn, held)
    balances = [
        ImportBalance(
            asset_id=asset_id,
            symbol=names[asset_id].symbol or names[asset_id].name,
            held=units,
            typed=typed[asset_id].declared_units if asset_id in typed else None,
            reconciled=typed[asset_id].reconciled if asset_id in typed else None,
            difference_units=typed[asset_id].difference_units if asset_id in typed else None,
        )
        for asset_id, units in held.items()
    ]
    balances.sort(key=lambda b: (b.symbol != rules.EUR, b.symbol))
    return ExchangeImport(**_to_import(imported).model_dump(), balances=balances)


def exchange_overview(conn: Connection, account_id: UUID) -> ExchangeOverview:
    """GET /accounts/{id}/exchange (see ExchangeOverview)."""
    account = _require_exchange(conn, account_id)
    stored = _current(conn, account_id)
    held = _held_by_asset(stored, conn)
    names = _symbols(conn, held)
    h = holding_table.c
    last_typed = {}
    for r in conn.execute(
        select(h.asset_id, h.as_of, h.declared_units, h.reconciled)
        .where(
            h.account_id == account_id,
            h.source == HoldingSource.USER.value,
            h.superseded_by.is_(None),
        )
        .order_by(h.as_of, h.created_at)
    ):
        last_typed[r.asset_id] = r
    positions = [
        ExchangePosition(
            asset_id=asset_id,
            symbol=names[asset_id].symbol or names[asset_id].name,
            name=names[asset_id].name,
            units=units,
            typed_units=last_typed[asset_id].declared_units if asset_id in last_typed else None,
            typed_as_of=last_typed[asset_id].as_of if asset_id in last_typed else None,
            reconciled=last_typed[asset_id].reconciled if asset_id in last_typed else None,
        )
        for asset_id, units in held.items()
    ]
    positions.sort(key=lambda p: (p.symbol != rules.EUR, p.symbol))
    imports = conn.execute(
        select(*_IMPORT_COLUMNS)
        .where(import_table.c.account_id == account_id)
        .order_by(import_table.c.last_at.desc(), import_table.c.created_at.desc())
    ).all()
    open_count = len(_open_withdrawals(stored))
    todos = []
    if account.provider == rules.BINANCE and open_count:
        todos.append(E.EXCHANGE_WITHDRAWAL_HISTORY_MISSING)
    return ExchangeOverview(
        account_id=account_id,
        provider=account.provider or "",
        positions=positions,
        imports=[_exchange_import(conn, r) for r in imports],
        withdrawals_open=open_count,
        movements_to_answer=sum(1 for s in stored if s.status is MovementStatus.PROPOSED),
        todos=todos,
    )


def _question(kind: MovementKind, status: MovementStatus) -> str | None:
    if status is MovementStatus.PROPOSED and kind is MovementKind.CASHBACK_REWARD:
        return E.EXCHANGE_DEPOSIT_QUESTION
    return None


def exchange_movements(conn: Connection, account_id: UUID) -> list[ExchangeMovementRow]:
    """GET /accounts/{id}/movements: the account's current movements, newest first, each
    with its symbol and, while proposed, its question and the kinds it may be answered as."""
    _require_exchange(conn, account_id)
    m, a = movement_table.c, asset_table.c
    rows = conn.execute(
        select(movement_table, a.symbol.label("symbol"), a.name.label("asset_name"))
        .join(asset_table, a.id == m.asset_id)
        .where(m.account_id == account_id, m.superseded_by.is_(None))
        .order_by(m.occurred_at.desc(), m.id)
    ).all()
    out = []
    for r in rows:
        kind, status = MovementKind(r.kind), MovementStatus(r.status)
        values = {c.name: getattr(r, c.name) for c in movement_table.c}
        out.append(
            ExchangeMovementRow(
                **values,
                symbol=r.symbol or r.asset_name,
                question=_question(kind, status),
                answers=answers_for(r.quantity) if status is MovementStatus.PROPOSED else [],
            )
        )
    return out


def _german_units(units: Decimal) -> str:
    text = format(units.normalize(), "f") if units != 0 else "0"
    return text.replace(".", ",")


def exchange_inbox_items(conn: Connection) -> list[InboxItem]:
    """The Zu prüfen cards of the exchange accounts (principle 7): the latest import of each
    account while it waits for its balances or disagrees with them; per import, its proposed
    coin withdrawals with the one answer for all (D178 (4)); and each other proposed coin
    movement (a crypto deposit: a bonus? an unknown row). Texts name coins and counts, never
    an amount in euro, an address or a transaction hash."""
    acc, i, m = account_table.c, import_table.c, movement_table.c
    items: list[InboxItem] = []
    latest = (
        select(i.account_id, func.max(i.last_at).label("last_at")).group_by(i.account_id)
    ).subquery()
    imports = conn.execute(
        select(*_IMPORT_COLUMNS, acc.label.label("account_label"))
        .join(account_table, acc.id == i.account_id)
        .join(latest, (latest.c.account_id == i.account_id) & (latest.c.last_at == i.last_at))
        .where(acc.kind == AccountKind.EXCHANGE.value)
        .order_by(i.created_at.desc())
    ).all()
    seen: set[UUID] = set()
    for r in imports:
        if r.account_id in seen:
            continue
        seen.add(r.account_id)
        day = _german_day(r.last_at.date())
        if r.status == ImportStatus.IMPORTED.value:
            items.append(
                InboxItem(
                    kind=InboxItemKind.EXCHANGE_PENDING_BALANCE,
                    created_at=r.created_at,
                    reasoning=(
                        f"Der Export von {r.account_label} bis {day} ist eingelesen. Zum "
                        "Abgleich fehlt der Bestand, den die Börse je Coin zeigt."
                    ),
                    question="Wie viel zeigt die Börse je Coin? Bitte unter „Bestand laut "
                    "Börse eingeben“ eintragen.",
                    import_id=r.id,
                    account_id=r.account_id,
                )
            )
        elif r.status == ImportStatus.UNRECONCILED.value:
            exchange_import = _exchange_import(conn, r)
            wrong = "; ".join(
                f"{b.symbol}: laut Börse {_german_units(b.typed or Decimal(0))}, gebucht "
                f"{_german_units(b.held)}"
                for b in exchange_import.balances
                if b.reconciled is False
            )
            items.append(
                InboxItem(
                    kind=InboxItemKind.EXCHANGE_UNRECONCILED,
                    created_at=r.updated_at,
                    reasoning=f"Der Bestand laut Börse zum {day} weicht ab ({wrong}).",
                    question=(
                        "Fehlt ein Teil des Exports, oder wurde ein Bestand falsch abgelesen? "
                        "Bitte einen vollständigen Export hochladen oder den Bestand neu "
                        "eingeben; Hausbuch gleicht nie selbst aus."
                    ),
                    import_id=r.id,
                    account_id=r.account_id,
                    error_code=E.EXCHANGE_UNRECONCILED,
                )
            )
    # Proposed coin withdrawals, one card per import (by the document they were read from).
    withdrawals = conn.execute(
        select(i.id, i.account_id, i.created_at, func.count(m.id).label("n"))
        .join(movement_table, (m.document_id == i.document_id) & (m.account_id == i.account_id))
        .join(account_table, acc.id == i.account_id)
        .where(
            acc.kind == AccountKind.EXCHANGE.value,
            m.kind == MovementKind.INTERNAL_TRANSFER_OUT.value,
            m.status == MovementStatus.PROPOSED.value,
            m.superseded_by.is_(None),
        )
        .group_by(i.id, i.account_id, i.created_at)
    ).all()
    for r in withdrawals:
        items.append(
            InboxItem(
                kind=InboxItemKind.EXCHANGE_WITHDRAWALS,
                created_at=r.created_at,
                reasoning=(
                    f"{r.n} Auszahlung{'en' if r.n != 1 else ''} von Coins aus dem Export: "
                    "Hausbuch weiß nicht, wohin sie gingen."
                ),
                question=(
                    "Gingen alle an eigene Wallets? Sonst einzeln auf der Kontoseite beantworten."
                ),
                import_id=r.id,
                account_id=r.account_id,
            )
        )
    a = asset_table.c
    singles = conn.execute(
        select(m.id, m.account_id, m.kind, m.occurred_at, m.created_at, a.symbol)
        .join(asset_table, a.id == m.asset_id)
        .join(account_table, acc.id == m.account_id)
        .where(
            acc.kind == AccountKind.EXCHANGE.value,
            m.kind != MovementKind.INTERNAL_TRANSFER_OUT.value,
            m.status == MovementStatus.PROPOSED.value,
            m.superseded_by.is_(None),
        )
    ).all()
    for r in singles:
        day = _german_day(r.occurred_at.date())
        if r.kind == MovementKind.CASHBACK_REWARD.value:
            reasoning = f"Eingang von {r.symbol} am {day} ohne Kauf."
            question = "Ist dieser Eingang ein Bonus oder kam er von einer eigenen Wallet?"
        else:
            reasoning = f"Eine Bewegung von {r.symbol} am {day}, die Hausbuch nicht kennt."
            question = "Was war diese Bewegung?"
        items.append(
            InboxItem(
                kind=InboxItemKind.EXCHANGE_MOVEMENT,
                created_at=r.created_at,
                reasoning=reasoning,
                question=question,
                movement_id=r.id,
                account_id=r.account_id,
                error_code=_question(MovementKind(r.kind), MovementStatus.PROPOSED),
            )
        )
    return items


def _german_day(day: date) -> str:
    return day.strftime("%d.%m.%Y")


__all__ = [
    "answer_movement",
    "asset_for_symbol",
    "confirm_movement",
    "confirm_withdrawals",
    "declare_holdings",
    "exchange_inbox_items",
    "exchange_movements",
    "exchange_overview",
    "import_export",
]
