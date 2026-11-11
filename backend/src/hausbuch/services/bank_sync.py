"""The FinTS connection and its sync runs (docs/phases/phase-5.md §4, D115-D120, D127).

Commands for the routes (create, update and read the connection; start a run; list runs) and
the steps jobs/bank_sync.py runs in its own short transactions around the network: begin,
approval needed, approval confirmed, finish (the preview, the import, the balance), fail and
retry. Nothing here talks to the bank.

Who owns a run (step 1's decision, §12.1 left it open):

- A run's job carries only (workspace_id, sync_id). The row's `started_at` is the run's
  token: every start and restart sets it, and every write of the job locks the row and checks
  the token it read at begin. A job that finds another token was superseded by a person's
  restart and stops without writing (and ends its dialog).
- `bank.sync_running` refuses a start while a run is `queued`, `running` or `importing` and
  not stale (rules.bank_sync.is_stale: untouched for the approval wait plus five minutes). The
  spec names `running` and `importing`; a `queued` row's job is about to run, so a second
  start would only queue a second dialog. A stale `queued` or `running` row (its worker is
  gone) is restarted: WORKER_LOST → queued.
- A `needs_approval` row is restarted by a manual start, whoever started it (§12.1: the card
  restarts a scheduled run's; a manual run's after a redeploy, D118 "restartable"). The row
  keeps its status until the new job's STARTED moves it to running (tan_flow.yaml); a job
  still polling the old approval sees the new token and stops.
- The import and `done` commit in one transaction, so a row is never left `importing` and a
  failed run never holds an import (§12.1 expected a failure after the import to keep its
  import_id: with one transaction there is no such failure; reported as a deviation).

Secrets (D119): the PIN and the product id are read from Settings by the job only, never
stored. The client state is stored with a MAC (HMAC-SHA256 under a key derived from the
session key) over the connection id, the account id, the BLZ, the URL, the login and the
blob: a state written for another login, URL or row is rejected (bank.state_rejected) and
dropped. The login is returned and logged only masked; no event carries a secret, an amount
or a name.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import event as event_table
from hausbuch.jobs import enqueue
from hausbuch.rules import bank_sync as rules
from hausbuch.rules import errors as E
from hausbuch.rules import statements as statement_rules
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.bank_sync import (
    BankConnection,
    BankConnectionCreate,
    BankConnectionPatch,
    BankSync,
    SyncEvent,
    SyncStatus,
    SyncTrigger,
    SyncWindowFacts,
    mask_login,
)
from hausbuch.schemas.enums import (
    AccountSource,
    BalanceSource,
    DocumentSource,
    EntityType,
    ImportStatus,
)
from hausbuch.schemas.events import (
    BankConnectionRecorded,
    BankSyncApprovalNeeded,
    BankSyncCompleted,
    BankSyncFailed,
    BankSyncStarted,
    EventType,
)
from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

from .allocations import user_id
from .context import CommandContext
from .documents import LOCAL_TIME_ZONE, upload_document
from .errors import ConflictError, NotFoundError, violated_constraint
from .events import append_event
from .lookup import require_row
from .statements import (
    account_has_lines,
    declare_bank_balance,
    import_statement,
    last_reconciled_closing,
    preview_import,
)

_STATE_KEY_LABEL = b"hausbuch-fints-state-v1"
_CONNECTION_TAKEN = "bank_connection_account_id_key"
_SECRET_COLUMNS = {"client_data", "client_data_mac", "login_name"}
_LIST_LIMIT = 20
_S = SyncStatus
_HELD = frozenset({_S.QUEUED, _S.RUNNING, _S.IMPORTING})


# --- The client state's MAC (D119) ---------------------------------------------------------


def state_key(session_key: bytes) -> bytes:
    """The MAC key of the stored client state, derived from the session key so no new secret
    is needed and the two uses never share a key."""
    return hmac.new(session_key, _STATE_KEY_LABEL, hashlib.sha256).digest()


def state_mac(key: bytes, connection: Row | Any, blob: bytes) -> bytes:
    """HMAC-SHA256 over the connection's identity and the blob, each part length-prefixed."""
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for part in (
        str(connection.id).encode(),
        str(connection.account_id).encode(),
        connection.bank_code.encode(),
        connection.fints_url.encode(),
        connection.login_name.encode(),
        blob,
    ):
        mac.update(len(part).to_bytes(8, "big"))
        mac.update(part)
    return mac.digest()


def verified_state(key: bytes, connection: Row) -> bytes | None:
    """The stored client state if its MAC verifies, else None (also when none is stored)."""
    blob, mac = connection.client_data, connection.client_data_mac
    if blob is None or mac is None:
        return None
    expected = state_mac(key, connection, bytes(blob))
    return bytes(blob) if hmac.compare_digest(expected, bytes(mac)) else None


# --- The connection --------------------------------------------------------------------------


def create_connection(ctx: CommandContext, data: BankConnectionCreate) -> BankConnection:
    """One connection per account; the account's source becomes fints (D125). An owner
    account holds no statements, so it cannot have one."""
    account = require_row(
        ctx.conn, account_table, EntityType.ACCOUNT, data.account_id, for_update=True
    )
    if account.is_owner_account:
        raise RuleError(statement_rules.OWNER_ACCOUNT, "owner accounts hold no statements")
    try:
        row = ctx.conn.execute(
            insert(connection_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=data.account_id,
                bank_code=data.bank_code,
                fints_url=data.fints_url,
                login_name=data.login_name,
                created_at=ctx.now,
                updated_at=ctx.now,
            )
            .returning(connection_table)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _CONNECTION_TAKEN:
            raise ConflictError(
                "the account already has a bank connection", constraint=_CONNECTION_TAKEN
            ) from None
        raise
    ctx.conn.execute(
        update(account_table)
        .where(account_table.c.id == data.account_id)
        .values(source=AccountSource.FINTS.value, updated_at=ctx.now)
    )
    append_event(
        ctx,
        EventType.ACCOUNT_CONNECTION_CREATED,
        data.account_id,
        _recorded(row, changed=[]),
    )
    return _to_connection(row)


def update_connection(
    ctx: CommandContext, account_id: UUID, patch: BankConnectionPatch
) -> BankConnection:
    """Change the fields sent. A new URL or login invalidates the stored client state (its MAC
    covers both), so the state, the TAN mechanism and the last approval are dropped with it:
    the next run starts like a first one."""
    current = _connection_row(ctx.conn, account_id, for_update=True)
    requested = patch.changes()
    values: dict[str, Any] = {
        name: value for name, value in requested.items() if getattr(current, name) != value
    }
    if not values:
        return _to_connection(current)
    changed = sorted(values)
    if {"fints_url", "login_name"} & set(values):
        values |= {"client_data": None, "client_data_mac": None, "sca_confirmed_at": None}
        if current.tan_mechanism is not None:
            values["tan_mechanism"] = None
            changed.append("tan_mechanism")
    row = ctx.conn.execute(
        update(connection_table)
        .where(connection_table.c.id == current.id)
        .values(**values, updated_at=ctx.now)
        .returning(connection_table)
    ).one()
    append_event(
        ctx, EventType.ACCOUNT_CONNECTION_UPDATED, account_id, _recorded(row, changed=changed)
    )
    return _to_connection(row)


def get_connection(conn: Connection, account_id: UUID) -> BankConnection | None:
    row = conn.execute(
        select(connection_table).where(connection_table.c.account_id == account_id)
    ).one_or_none()
    return None if row is None else _to_connection(row)


def _connection_row(conn: Connection, account_id: UUID, *, for_update: bool = False) -> Row:
    query = select(connection_table).where(connection_table.c.account_id == account_id)
    if for_update:
        query = query.with_for_update()
    row = conn.execute(query).one_or_none()
    if row is None:
        raise NotFoundError("bank_connection", account_id)
    return row


def _recorded(row: Row, *, changed: list[str]) -> BankConnectionRecorded:
    return BankConnectionRecorded(
        connection_id=row.id,
        account_id=row.account_id,
        bank_code=row.bank_code,
        login_masked=mask_login(row.login_name),
        auto_sync=row.auto_sync,
        changed=changed,
    )


def _to_connection(row: Row) -> BankConnection:
    values = {k: v for k, v in row._mapping.items() if k not in _SECRET_COLUMNS}
    return BankConnection.model_validate(
        values
        | {
            "login_masked": mask_login(row.login_name),
            "state_present": row.client_data is not None,
        }
    )


# --- Starting a run (POST /accounts/{id}/syncs) ---------------------------------------------


def configured(settings: Settings) -> bool:
    """A PIN and a usable product id are set (§4.4 bank.not_configured)."""
    return bool(settings.fints_pin) and rules.product_id_usable(settings.fints_product_id)


def start_sync(ctx: CommandContext, account_id: UUID, *, settings: Settings) -> BankSync:
    """Queue a manual run (or restart one, see the module docstring) and its job. Refuses
    bank.not_configured, bank.rate_limited (a fifth manual start within the hour) and
    bank.sync_running; bank.window_invalid when the anchor is today or later."""
    account = require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)
    connection = _connection_row(ctx.conn, account_id)
    if not configured(settings):
        raise RuleError(E.BANK_NOT_CONFIGURED, "the FinTS PIN or product id is not configured")
    if _manual_starts_last_hour(ctx, account_id) >= rules.MANUAL_STARTS_PER_HOUR:
        raise RuleError(E.BANK_RATE_LIMITED, "at most four manual syncs per hour")
    window = rules.request_window(
        SyncWindowFacts(
            opening_balance_on=account.opening_balance_on,
            last_booking_on=connection.last_booking_on,
        ),
        ctx.now.astimezone(LOCAL_TIME_ZONE).date(),
    )
    start = {
        "trigger": SyncTrigger.MANUAL.value,
        "requested_from": window.start,
        "requested_to": window.end,
        "started_at": ctx.now,
        "created_by": user_id(ctx),
        "updated_at": ctx.now,
    }
    live = _live_row(ctx.conn, account_id)
    if live is None:
        row = ctx.conn.execute(
            insert(sync_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=account_id,
                connection_id=connection.id,
                status=_S.QUEUED.value,
                created_at=ctx.now,
                **start,
            )
            .returning(sync_table)
        ).one()
    else:
        status = _S(live.status)
        stale = rules.is_stale(live.updated_at, ctx.now, settings.sync_approval_timeout_s)
        if status in _HELD and not (stale and status is not _S.IMPORTING):
            raise RuleError(E.BANK_SYNC_RUNNING, "a sync of this account is running")
        if status is not _S.NEEDS_APPROVAL:  # a stale queued or running row: its job is gone
            lost = rules.next_state(status, SyncEvent.WORKER_LOST, _trigger(live))
            start["status"] = lost.status.value
        row = ctx.conn.execute(
            update(sync_table)
            .where(sync_table.c.id == live.id)
            .values(**start, connection_id=connection.id)
            .returning(sync_table)
        ).one()
    append_event(
        ctx,
        EventType.ACCOUNT_SYNC_STARTED,
        account_id,
        BankSyncStarted(
            sync_id=row.id,
            connection_id=connection.id,
            trigger=SyncTrigger.MANUAL,
            requested_from=window.start,
            requested_to=window.end,
        ),
    )
    enqueue.enqueue_bank_sync(
        ctx.conn, workspace_id=ctx.workspace_id, account_id=account_id, sync_id=row.id
    )
    return _to_sync(row)


def _manual_starts_last_hour(ctx: CommandContext, account_id: UUID) -> int:
    """Every manual start of the account's runs in the last hour, restarts included (§4.7:
    the bank's SCA counters count each dialog)."""
    return ctx.conn.execute(
        select(func.count())
        .select_from(event_table)
        .where(
            event_table.c.type == EventType.ACCOUNT_SYNC_STARTED.value,
            event_table.c.entity_id == account_id,
            event_table.c.recorded_at > ctx.now - timedelta(hours=1),
            event_table.c.payload["trigger"].astext == SyncTrigger.MANUAL.value,
        )
    ).scalar_one()


def _live_row(conn: Connection, account_id: UUID) -> Row | None:
    return conn.execute(
        select(sync_table)
        .where(
            sync_table.c.account_id == account_id,
            sync_table.c.status.in_([s.value for s in rules.LIVE_SYNC_STATUSES]),
        )
        .order_by(sync_table.c.created_at.desc(), sync_table.c.id.desc())
        .limit(1)
        .with_for_update()
    ).one_or_none()


def _trigger(row: Row) -> SyncTrigger:
    return SyncTrigger(row.trigger)


# --- Reads -----------------------------------------------------------------------------------


def list_syncs(conn: Connection, account_id: UUID, limit: int = _LIST_LIMIT) -> list[BankSync]:
    """The account's latest runs, newest first."""
    rows = conn.execute(
        select(sync_table)
        .where(sync_table.c.account_id == account_id)
        .order_by(sync_table.c.created_at.desc(), sync_table.c.id.desc())
        .limit(limit)
    )
    return [_to_sync(r) for r in rows]


def get_sync(conn: Connection, sync_id: UUID) -> BankSync | None:
    row = conn.execute(select(sync_table).where(sync_table.c.id == sync_id)).one_or_none()
    return None if row is None else _to_sync(row)


def _to_sync(row: Row) -> BankSync:
    return BankSync.model_validate(dict(row._mapping))


# What a bank_sync_failed card says per code: what happened, and what to do (principle 7).
_FAILED_CARD = {
    E.BANK_NOT_CONFIGURED: (
        "Der Bankabruf ist nicht eingerichtet: PIN oder Produkt-ID fehlen in der"
        " Serverkonfiguration.",
        "Beide als Secret hinterlegen (Runbook Schritt 14), dann erneut abrufen.",
    ),
    E.BANK_TAN_NOT_DECOUPLED: (
        "Die Bank bietet für diesen Zugang keine Freigabe per App (pushTAN) an.",
        "Im Online-Banking das Verfahren S-pushTAN einrichten, dann erneut abrufen.",
    ),
    E.BANK_APPROVAL_TIMEOUT: (
        "Die Freigabe in der S-pushTAN-App kam nicht innerhalb von fünf Minuten.",
        "Erneut abrufen und die Anfrage in der App freigeben.",
    ),
    E.BANK_ACCOUNT_NOT_FOUND: (
        "Unter diesem Zugang führt die Bank kein Konto mit der IBAN dieses Kontos.",
        "Anmeldename und BLZ im Bankabruf prüfen.",
    ),
    E.BANK_STATE_REJECTED: (
        "Die gespeicherte Verbindung zur Bank passte nicht mehr und wurde verworfen.",
        "Erneut abrufen; die Bank fragt dann wieder nach einer Freigabe.",
    ),
    E.BANK_UNREACHABLE: (
        "Die Bank war nicht erreichbar.",
        "Später erneut abrufen; hält es an, die FinTS-Adresse prüfen.",
    ),
    E.BANK_REFUSED: (
        "Die Bank hat den Abruf abgelehnt.",
        "Anmeldename und PIN prüfen; nach drei falschen PINs sperrt die Bank den Zugang.",
    ),
    E.BANK_UNSUPPORTED_FORMAT: (
        "Die Bank liefert Umsätze in keinem Format, das Hausbuch lesen kann.",
        "Kontoauszüge weiter als Datei hochladen.",
    ),
    E.BANK_WINDOW_INVALID: (
        "Der Abruf hätte nach heute begonnen: der Anfangssaldo des Kontos liegt heute oder später.",
        "Den Stichtag des Anfangssaldos im Konto prüfen.",
    ),
}
_FAILED_DEFAULT = (
    "Der letzte Bankabruf ist fehlgeschlagen.",
    "Erneut abrufen; hält es an, die Datei als Kontoauszug hochladen.",
)


def bank_sync_items(conn: Connection) -> list[InboxItem]:
    """The Zu prüfen cards of §4.6 and D117, computed on read from each account's latest
    finished run: bank_sync_failed while it failed, bank_balance_drift while it brought
    nothing new and HKSAL differed from the last reconciled closing. A later good run ends
    either card. (bank_approval_needed, for a scheduled run, comes with step 2's schedule.)"""
    latest = (
        select(sync_table)
        .where(sync_table.c.finished_at.is_not(None))
        .order_by(
            sync_table.c.account_id,
            sync_table.c.finished_at.desc(),
            sync_table.c.id.desc(),
        )
        .distinct(sync_table.c.account_id)
    )
    items: list[InboxItem] = []
    for row in conn.execute(latest):
        if row.status == _S.FAILED.value:
            reasoning, question = _FAILED_CARD.get(row.error_code, _FAILED_DEFAULT)
            items.append(
                InboxItem(
                    kind=InboxItemKind.BANK_SYNC_FAILED,
                    created_at=row.finished_at,
                    reasoning=f"{reasoning} ({row.error_code})",
                    question=question,
                    account_id=row.account_id,
                    sync_id=row.id,
                )
            )
        elif row.status == _S.NOTHING_NEW.value and row.error_code == E.BANK_BALANCE_DRIFT:
            items.append(
                InboxItem(
                    kind=InboxItemKind.BANK_BALANCE_DRIFT,
                    created_at=row.finished_at,
                    reasoning=(
                        "Der Kontostand der Bank weicht vom letzten abgeglichenen Saldo ab,"
                        " obwohl keine neuen Umsätze kamen"
                        f" ({E.BANK_BALANCE_DRIFT})."
                    ),
                    question=(
                        "Fehlt ein Kontoauszug? Den Zeitraum als Datei hochladen oder den"
                        " Anfangssaldo des Kontos prüfen."
                    ),
                    account_id=row.account_id,
                    sync_id=row.id,
                )
            )
    return items


# --- The job's steps (jobs/bank_sync.py) ----------------------------------------------------


@dataclass(frozen=True)
class RunPlan:
    """What the job needs for the network part, read at begin. The PIN and product id are
    not in it: the job reads them from Settings."""

    sync_id: UUID
    account_id: UUID
    connection_id: UUID
    trigger: SyncTrigger
    token: datetime  # bank_sync.started_at at begin
    requested_from: date
    requested_to: date
    bank_code: str
    fints_url: str
    login_name: str
    client_data: bytes | None  # verified against its MAC
    tan_mechanism: str | None
    iban_hmac: bytes
    created_by: UUID | None  # the person of a manual run


class Superseded(Exception):
    """The row was restarted (or finished) by someone else: the job stops without writing."""


def begin_run(
    ctx: CommandContext, sync_id: UUID, *, attempt: int, settings: Settings
) -> RunPlan | None:
    """Take the row for this job. None when there is nothing to run: the row is gone or
    final; a retried job (attempt > 0) of a `needs_approval` row leaves it restartable
    (WORKER_LOST, D118); not configured or a state whose MAC fails records `failed`."""
    row = _sync_row(ctx.conn, sync_id)
    if row is None:
        return None
    status = _S(row.status)
    if status not in rules.LIVE_SYNC_STATUSES:
        return None
    trigger = _trigger(row)
    if attempt > 0 and status is _S.NEEDS_APPROVAL:
        rules.next_state(status, SyncEvent.WORKER_LOST, trigger)  # stays needs_approval
        return None
    if attempt > 0 and status is _S.RUNNING:  # the worker was lost mid-run
        status = rules.next_state(status, SyncEvent.WORKER_LOST, trigger).status
    if status is _S.IMPORTING:  # never committed (module docstring); nothing to resume
        return None
    connection = require_row(ctx.conn, connection_table, "bank_connection", row.connection_id)
    if not configured(settings):
        _fail(ctx, row, E.BANK_NOT_CONFIGURED)
        return None
    client_data: bytes | None = None
    if connection.client_data is not None:
        client_data = verified_state(state_key(settings.session_key), connection)
        if client_data is None:
            ctx.conn.execute(
                update(connection_table)
                .where(connection_table.c.id == connection.id)
                .values(client_data=None, client_data_mac=None, updated_at=ctx.now)
            )
            _fail(ctx, row, E.BANK_STATE_REJECTED)
            return None
    _set(ctx, row.id, rules.next_state(status, SyncEvent.STARTED, trigger).status)
    account = require_row(ctx.conn, account_table, EntityType.ACCOUNT, row.account_id)
    return RunPlan(
        sync_id=row.id,
        account_id=row.account_id,
        connection_id=connection.id,
        trigger=trigger,
        token=row.started_at,
        requested_from=row.requested_from,
        requested_to=row.requested_to,
        bank_code=connection.bank_code,
        fints_url=connection.fints_url,
        login_name=connection.login_name,
        client_data=client_data,
        tan_mechanism=connection.tan_mechanism,
        iban_hmac=bytes(account.iban_hmac),
        created_by=row.created_by,
    )


def approval_needed(ctx: CommandContext, plan: RunPlan) -> bool:
    """running → needs_approval, with its event. True when the job ends the dialog now (a
    scheduled run: nobody is watching the phone, D120)."""
    row = _owned(ctx, plan)
    move = rules.next_state(_S(row.status), SyncEvent.TAN_DEMANDED, plan.trigger)
    _set(ctx, row.id, move.status)
    append_event(
        ctx,
        EventType.ACCOUNT_SYNC_APPROVAL_NEEDED,
        plan.account_id,
        BankSyncApprovalNeeded(sync_id=row.id, trigger=plan.trigger),
    )
    return move.end_dialog


def still_owned(conn: Connection, plan: RunPlan) -> bool:
    """The row still carries this job's token and is live (a read, between two polls)."""
    row = conn.execute(
        select(sync_table.c.status, sync_table.c.started_at).where(sync_table.c.id == plan.sync_id)
    ).one_or_none()
    return (
        row is not None
        and row.started_at == plan.token
        and _S(row.status) in rules.LIVE_SYNC_STATUSES
    )


def approval_confirmed(ctx: CommandContext, plan: RunPlan) -> None:
    row = _owned(ctx, plan)
    _set(
        ctx, row.id, rules.next_state(_S(row.status), SyncEvent.TAN_CONFIRMED, plan.trigger).status
    )


def approval_timed_out(ctx: CommandContext, plan: RunPlan) -> None:
    row = _owned(ctx, plan)
    move = rules.next_state(_S(row.status), SyncEvent.TAN_TIMEOUT, plan.trigger)
    _fail(ctx, row, move.error_code or E.BANK_APPROVAL_TIMEOUT)


def fail_run(
    ctx: CommandContext, plan: RunPlan, code: str, response_code: str | None = None
) -> None:
    """Any live row → failed with the code (and a 9xxx answer's four digits on the event)."""
    _fail(ctx, _owned(ctx, plan), code, response_code)


def retry_run(ctx: CommandContext, plan: RunPlan) -> None:
    """running → queued before the job asks Procrastinate to run it again (bank.unreachable)."""
    row = _owned(ctx, plan)
    _set(ctx, row.id, rules.next_state(_S(row.status), SyncEvent.RETRY, plan.trigger).status)


@dataclass(frozen=True)
class Fetched:
    """What the bank sent: the booked camt streams, HKSAL's balance, the client state."""

    streams: list[bytes]
    balance: Decimal | None
    state: bytes | None
    tan_mechanism: str | None
    approved: bool  # an approval was confirmed in this run


def finish_run(
    ctx: CommandContext,
    store: BlobStore,
    plan: RunPlan,
    fetched: Fetched,
    *,
    settings: Settings,
) -> SyncStatus:
    """Store the client state, then D127: no new line → nothing_new (no document, no import;
    D117's drift check); a new line → the ZIP stored and imported through import_statement
    unchanged, HKSAL declared when the file states no closing → done. A statement.* refusal
    of the archive records failed with that code, the client state kept."""
    row = _owned(ctx, plan)
    status = _S(row.status)
    connection = require_row(
        ctx.conn, connection_table, "bank_connection", plan.connection_id, for_update=True
    )
    _store_state(ctx, plan, connection, fetched, settings)
    if not fetched.streams:
        move = rules.next_state(status, SyncEvent.STREAMS_EMPTY, plan.trigger)
        _nothing_new(ctx, row, plan, move.status, lines_total=0, balance=fetched.balance)
        return move.status
    try:
        with ctx.conn.begin_nested():
            data = rules.build_archive(fetched.streams)
            preview = preview_import(
                ctx.conn, plan.account_id, data, iban_key=settings.iban_hmac_key
            )
            if preview.lines_new == 0:
                move = rules.next_state(status, SyncEvent.NOTHING_NEW, plan.trigger)
                _nothing_new(
                    ctx,
                    row,
                    plan,
                    move.status,
                    lines_total=preview.lines_total,
                    balance=fetched.balance,
                )
                return move.status
            importing = rules.next_state(status, SyncEvent.NEW_LINES, plan.trigger).status
            _set(ctx, row.id, importing)
            imported = _import(ctx, store, plan, data, fetched.balance, settings)
            done = rules.next_state(importing, SyncEvent.IMPORTED, plan.trigger).status
    except RuleError as exc:
        _fail(ctx, row, exc.code if _storable_code(exc.code) else E.BANK_FAILED)
        return _S.FAILED
    _finish(
        ctx,
        row.id,
        done,
        import_id=imported.id,
        lines_total=imported.lines_total,
        lines_new=imported.lines_new,
        balance_source=imported.balance_source.value,
        error_code=None,
    )
    ctx.conn.execute(
        update(connection_table)
        .where(connection_table.c.id == plan.connection_id)
        .values(
            last_sync_at=ctx.now,
            last_booking_on=_later(connection.last_booking_on, imported.last_booking_on),
            updated_at=ctx.now,
        )
    )
    append_event(
        ctx,
        EventType.ACCOUNT_SYNC_COMPLETED,
        plan.account_id,
        BankSyncCompleted(
            sync_id=row.id,
            status=_S.DONE,
            import_id=imported.id,
            lines_total=imported.lines_total,
            lines_new=imported.lines_new,
            balance_source=imported.balance_source,
            balance_checked=False,
            reconciled=imported.status is ImportStatus.RECONCILED,
        ),
    )
    return done


def _import(
    ctx: CommandContext,
    store: BlobStore,
    plan: RunPlan,
    data: bytes,
    balance: Decimal | None,
    settings: Settings,
) -> Any:
    """§4.3: the ZIP stored as a bank_sync document, then the unchanged import (which finds
    the same document by its bytes), then HKSAL when the file states no closing (D117)."""
    filename = f"fints-{plan.requested_from:%Y%m%d}-{plan.requested_to:%Y%m%d}.zip"
    upload_document(
        ctx,
        store,
        data,
        media_type="application/zip",
        source=DocumentSource.BANK_SYNC,
        original_filename=filename,
    )
    imported = import_statement(
        ctx,
        store,
        plan.account_id,
        data,
        media_type="application/zip",
        iban_key=settings.iban_hmac_key,
        original_filename=filename,
    )
    declared = rules.declared_closing(
        imported.declared_closing if imported.balance_source is BalanceSource.FILE else None,
        balance,
    )
    if declared is not None and declared[1] is BalanceSource.BANK:
        imported = declare_bank_balance(ctx, imported.id, declared[0])
    return imported


def _nothing_new(
    ctx: CommandContext,
    row: Row,
    plan: RunPlan,
    status: SyncStatus,
    *,
    lines_total: int,
    balance: Decimal | None,
) -> None:
    """D127 and D117: no document, no import; HKSAL compared with the last reconciled closing
    (the anchor when the account has no line yet; skipped while the latest import is not
    reconciled, which has its own card)."""
    reference = last_reconciled_closing(ctx.conn, plan.account_id)
    if reference is None and not account_has_lines(ctx.conn, plan.account_id):
        reference = ctx.conn.execute(
            select(account_table.c.opening_balance).where(account_table.c.id == plan.account_id)
        ).scalar_one()
    checked = balance is not None and reference is not None
    drift = checked and rules.balance_drift(balance, reference) is not None  # type: ignore[arg-type]
    _finish(
        ctx,
        row.id,
        status,
        import_id=None,
        lines_total=lines_total,
        lines_new=0,
        balance_source=BalanceSource.BANK.value if balance is not None else None,
        balance_checked=checked,
        error_code=E.BANK_BALANCE_DRIFT if drift else E.BANK_NOTHING_NEW,
    )
    ctx.conn.execute(
        update(connection_table)
        .where(connection_table.c.id == plan.connection_id)
        .values(last_sync_at=ctx.now, updated_at=ctx.now)
    )
    append_event(
        ctx,
        EventType.ACCOUNT_SYNC_COMPLETED,
        plan.account_id,
        BankSyncCompleted(
            sync_id=row.id,
            status=_S.NOTHING_NEW,
            lines_total=lines_total,
            lines_new=0,
            balance_source=BalanceSource.BANK if balance is not None else None,
            balance_checked=checked,
        ),
    )


def _store_state(
    ctx: CommandContext, plan: RunPlan, connection: Row, fetched: Fetched, settings: Settings
) -> None:
    """The client state with its MAC (D119), only while the connection is the one the run
    started with (a login or URL changed meanwhile would make it another login's state)."""
    if fetched.state is None:
        return
    same = (connection.bank_code, connection.fints_url, connection.login_name) == (
        plan.bank_code,
        plan.fints_url,
        plan.login_name,
    )
    if not same:
        return
    values: dict[str, Any] = {
        "client_data": fetched.state,
        "client_data_mac": state_mac(state_key(settings.session_key), connection, fetched.state),
        "tan_mechanism": fetched.tan_mechanism,
        "updated_at": ctx.now,
    }
    if fetched.approved:
        values["sca_confirmed_at"] = ctx.now
    ctx.conn.execute(
        update(connection_table).where(connection_table.c.id == connection.id).values(**values)
    )


def _storable_code(code: str) -> bool:
    return code.startswith(("bank.", "statement.")) and len(code) <= 64


def _later(a: date | None, b: date | None) -> date | None:
    return max((d for d in (a, b) if d is not None), default=None)


def _sync_row(conn: Connection, sync_id: UUID) -> Row | None:
    return conn.execute(
        select(sync_table).where(sync_table.c.id == sync_id).with_for_update()
    ).one_or_none()


def _owned(ctx: CommandContext, plan: RunPlan) -> Row:
    row = _sync_row(ctx.conn, plan.sync_id)
    if (
        row is None
        or row.started_at != plan.token
        or _S(row.status) not in rules.LIVE_SYNC_STATUSES
    ):
        raise Superseded
    return row


def _set(ctx: CommandContext, sync_id: UUID, status: SyncStatus) -> None:
    ctx.conn.execute(
        update(sync_table)
        .where(sync_table.c.id == sync_id)
        .values(status=status.value, updated_at=ctx.now)
    )


def _finish(ctx: CommandContext, sync_id: UUID, status: SyncStatus, **values: Any) -> None:
    ctx.conn.execute(
        update(sync_table)
        .where(sync_table.c.id == sync_id)
        .values(status=status.value, finished_at=ctx.now, updated_at=ctx.now, **values)
    )


def _fail(ctx: CommandContext, row: Row, code: str, response_code: str | None = None) -> None:
    rules.next_state(_S(row.status), SyncEvent.ERROR, _trigger(row))  # every live row may fail
    started = row.started_at
    _finish(
        ctx,
        row.id,
        _S.FAILED,
        error_code=code,
        # A row that never started (not configured) still needs started_at <= finished_at.
        **({} if started is not None else {"started_at": ctx.now}),
    )
    append_event(
        ctx,
        EventType.ACCOUNT_SYNC_FAILED,
        row.account_id,
        BankSyncFailed(sync_id=row.id, error_code=code, response_code=response_code),
    )
