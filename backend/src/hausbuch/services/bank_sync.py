"""The FinTS connection and its sync runs (docs/phases/phase-5.md §4, D115-D120, D127).

Commands for the routes (create, update and read the connection; start a run; list runs) and
the steps jobs/bank_sync.py runs in its own short transactions around the network: begin,
approval needed, approval confirmed, finish (the preview, the import, the balance), fail and
retry; and the bank's PDF statements (D154, step 2b): what is stored, one statement stored
(store_statement), the list. Nothing here talks to the bank.

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

Phase 6 step 3 (D163, D173): each connection names a credential slot whose PIN is the
container secret HAUSBUCH_FINTS_PIN_<SLOT> (`primary`: HAUSBUCH_FINTS_PIN); a start or a run
whose slot has no PIN is refused with bank.pin_missing before any dialog, naming the variable.
A private account gets a connection only once its statement export is imported and
reconciled (bank.history_missing), and a first run starts after that history (imports_through,
D173 (4)); a later run's 14-day overlap never reaches into a history from CSV or PDF files
(unreferenced_through). The bank's parameters a run reads are recorded on the account as
account.bank_parameters_read when they change (D173 (7)); their bank name picks the card texts
(DKB's app, D173 (1)) and the daily-fetch guard (two manual syncs without an approval first,
D173 (5)).

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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_statement as statement_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs import enqueue
from hausbuch.rules import bank_statements as statement_fetch
from hausbuch.rules import bank_sync as rules
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.bank import StatementFormat
from hausbuch.schemas.bank_sync import (
    PRIMARY_SLOT,
    BankConnection,
    BankConnectionCreate,
    BankConnectionPatch,
    BankParameters,
    BankStatement,
    BankSync,
    StatementKey,
    SyncEvent,
    SyncStatus,
    SyncTrigger,
    SyncWindow,
    SyncWindowFacts,
    mask_login,
)
from hausbuch.schemas.common import TAX_YEAR_MIN
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import (
    AccountPurpose,
    AccountSource,
    BalanceSource,
    DocumentSource,
    DocumentType,
    EntityType,
    ImportStatus,
)
from hausbuch.schemas.events import (
    BankConnectionRecorded,
    BankParametersRead,
    BankStatementFetched,
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
from .documents import LOCAL_TIME_ZONE, type_document, upload_document
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
    """One connection per account; the account's source becomes fints (D125). Phase 6
    step 3 lifts phase 5's guard on private accounts: a private account gets one once its
    statement export is imported and reconciled (bank.history_missing otherwise), so the
    first window starts after that history (D173 (4), §15 step 3). The rental account is
    unchanged."""
    account = require_row(
        ctx.conn, account_table, EntityType.ACCOUNT, data.account_id, for_update=True
    )
    if account.purpose == AccountPurpose.PRIVATE.value and (
        imports_through(ctx.conn, data.account_id) is None
    ):
        raise RuleError(
            E.BANK_HISTORY_MISSING, "import and reconcile the account's statement export first"
        )
    try:
        row = ctx.conn.execute(
            insert(connection_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=data.account_id,
                bank_code=data.bank_code,
                fints_url=data.fints_url,
                login_name=data.login_name,
                credential_slot=data.credential_slot,
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
    return _to_connection(ctx.conn, row)


def update_connection(
    ctx: CommandContext, account_id: UUID, patch: BankConnectionPatch
) -> BankConnection:
    """Change the fields sent. A new URL or login invalidates the stored client state (its MAC
    covers both), so the state, the TAN mechanism and the last approval are dropped with it:
    the next run starts like a first one. Switching the daily fetch on is refused with
    bank.auto_sync_waits while the bank asks for an approval too often (D173 (5)). A new
    credential slot keeps the state (its MAC covers the login and the URL, not the PIN)."""
    current = _connection_row(ctx.conn, account_id, for_update=True)
    requested = patch.changes()
    values: dict[str, Any] = {
        name: value for name, value in requested.items() if getattr(current, name) != value
    }
    if not values:
        return _to_connection(ctx.conn, current)
    if values.get("auto_sync") is True and not _auto_sync_allowed(ctx.conn, current):
        raise RuleError(
            E.BANK_AUTO_SYNC_WAITS,
            "the daily fetch waits for two manual syncs in a row without an approval",
        )
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
    return _to_connection(ctx.conn, row)


def get_connection(conn: Connection, account_id: UUID) -> BankConnection | None:
    row = conn.execute(
        select(connection_table).where(connection_table.c.account_id == account_id)
    ).one_or_none()
    return None if row is None else _to_connection(conn, row)


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
        credential_slot=row.credential_slot,
        auto_sync=row.auto_sync,
        fetch_statements=row.fetch_statements,
        changed=changed,
    )


def _to_connection(conn: Connection, row: Row) -> BankConnection:
    values = {k: v for k, v in row._mapping.items() if k not in _SECRET_COLUMNS}
    bank_name = recorded_bank_name(conn, row.id)
    return BankConnection.model_validate(
        values
        | {
            "login_masked": mask_login(row.login_name),
            "state_present": row.client_data is not None,
            "credential_variable": rules.pin_variable(row.credential_slot),
            "bank_name": bank_name,
            "approval_text": rules.approval_text(bank_name) or PHASE5_APPROVAL_TEXT,
            "auto_sync_allowed": _auto_sync_allowed(conn, row, bank_name=bank_name),
        }
    )


# The Bankabruf card's text while it waits for an approval, for a bank whose name is not in
# rules.bank_sync's table (the Sparkasse's S-pushTAN, phase 5).
PHASE5_APPROVAL_TEXT = "In der S-pushTAN-App freigeben"


def imports_through(conn: Connection, account_id: UUID) -> date | None:
    """D173 (4): the last booking day of the account's reconciled imports (an upload, an
    email or an earlier sync), None without any. For a camt export it is the last day the
    file booked; for a CSV it is the day whose closing the user confirmed ("Saldo nach der
    letzten Buchung vom …", D173 (3))."""
    return conn.execute(
        select(func.max(import_table.c.last_booking_on)).where(
            import_table.c.account_id == account_id,
            import_table.c.status == ImportStatus.RECONCILED.value,
        )
    ).scalar_one()


def unreferenced_through(conn: Connection, account_id: UUID) -> date | None:
    """D173 (4), amended after the step 3 review: the last booking day of the account's
    reconciled imports whose lines carry no bank reference, every format but camt (a CSV, a
    DKB CSV, a PDF), None without any. A later sync's 14-day overlap never reaches back into
    it, since dedup cannot tell those lines from the camt's."""
    return conn.execute(
        select(func.max(import_table.c.last_booking_on)).where(
            import_table.c.account_id == account_id,
            import_table.c.status == ImportStatus.RECONCILED.value,
            import_table.c.format != StatementFormat.CAMT.value,
        )
    ).scalar_one()


# --- The bank's parameters (phase 6 D173 (7)) -------------------------------------------------


def _last_parameters(conn: Connection, connection_id: UUID) -> BankParameters | None:
    payload = conn.execute(
        select(event_table.c.payload)
        .where(
            event_table.c.type == EventType.ACCOUNT_BANK_PARAMETERS_READ.value,
            event_table.c.payload["connection_id"].astext == str(connection_id),
        )
        .order_by(event_table.c.recorded_at.desc(), event_table.c.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if payload is None:
        return None
    read = BankParametersRead.model_validate(payload)
    return BankParameters.model_validate(read.model_dump(exclude={"connection_id", "sync_id"}))


def recorded_bank_name(conn: Connection, connection_id: UUID) -> str | None:
    """The bank's name from the connection's last recorded parameters (HIBPA)."""
    last = _last_parameters(conn, connection_id)
    return None if last is None else last.bank_name


def record_bank_parameters(
    ctx: CommandContext, plan: "RunPlan", parameters: BankParameters
) -> bool:
    """account.bank_parameters_read when the bank's parameters differ from the connection's
    last record (the first run always records them). A connection fact, not the run's: it
    is written whether or not the run still owns its row. True when written."""
    if _last_parameters(ctx.conn, plan.connection_id) == parameters:
        return False
    append_event(
        ctx,
        EventType.ACCOUNT_BANK_PARAMETERS_READ,
        plan.account_id,
        BankParametersRead(
            **parameters.model_dump(), connection_id=plan.connection_id, sync_id=plan.sync_id
        ),
    )
    return True


def manual_sync_approvals(conn: Connection, connection_id: UUID) -> list[bool]:
    """D173 (5)'s input: the connection's finished manual syncs, oldest first, each as
    whether the bank asked for an approval in it (an account.sync_approval_needed event of
    that run). A run that failed before the bank could ask (unreachable, refused at login)
    says nothing about the cadence and is left out; a failed run that did ask counts."""
    rows = conn.execute(
        select(sync_table.c.id, sync_table.c.status)
        .where(
            sync_table.c.connection_id == connection_id,
            sync_table.c.trigger == SyncTrigger.MANUAL.value,
            sync_table.c.finished_at.is_not(None),
        )
        .order_by(sync_table.c.finished_at, sync_table.c.id)
    ).all()
    if not rows:
        return []
    asked = set(
        conn.execute(
            select(event_table.c.payload["sync_id"].astext).where(
                event_table.c.type == EventType.ACCOUNT_SYNC_APPROVAL_NEEDED.value,
                event_table.c.payload["sync_id"].astext.in_([str(r.id) for r in rows]),
            )
        ).scalars()
    )
    succeeded = {_S.DONE.value, _S.NOTHING_NEW.value}
    return [str(r.id) in asked for r in rows if r.status in succeeded or str(r.id) in asked]


def _auto_sync_allowed(conn: Connection, row: Row, *, bank_name: str | None = None) -> bool:
    """D173 (5) for a connection row: any bank but DKB always; DKB after two manual syncs in
    a row without an approval. A connection whose parameters were never read (no run yet)
    has no bank name and is let through (the fixture keys the rule by the BPD's name)."""
    name = bank_name if bank_name is not None else recorded_bank_name(conn, row.id)
    if not rules.is_dkb(name):
        return True
    return rules.auto_sync_allowed(name, manual_sync_approvals(conn, row.id))


# --- Starting a run (POST /accounts/{id}/syncs) ---------------------------------------------


def configured(settings: Settings) -> bool:
    """A usable product id and at least one slot's PIN are set (§4.4 bank.not_configured).
    Whether the connection's own slot has its PIN is check_slot's (bank.pin_missing)."""
    return bool(settings.fints_pins) and rules.product_id_usable(settings.fints_product_id)


def configured_slots(settings: Settings) -> list[str]:
    """The slots whose container secret is set, names only (phase 6 D163)."""
    return sorted(settings.fints_pins)


def start_sync(ctx: CommandContext, account_id: UUID, *, settings: Settings) -> BankSync:
    """Queue a manual run (or restart one, see the module docstring) and its job. Refuses
    bank.not_configured, bank.pin_missing (the connection's slot has no PIN; phase 6 D163),
    bank.rate_limited (a fifth manual start within the hour) and bank.sync_running;
    bank.window_invalid when the anchor or the imported history reaches today."""
    account = require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)
    connection = _connection_row(ctx.conn, account_id)
    if not configured(settings):
        raise RuleError(E.BANK_NOT_CONFIGURED, "the FinTS PIN or product id is not configured")
    rules.check_slot(set(settings.fints_pins), connection.credential_slot)
    if _manual_starts_last_hour(ctx, account_id) >= rules.MANUAL_STARTS_PER_HOUR:
        raise RuleError(E.BANK_RATE_LIMITED, "at most four manual syncs per hour")
    window = _window(ctx, account, connection)
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
    _announce(ctx, row, SyncTrigger.MANUAL)
    return _to_sync(row)


def _window(ctx: CommandContext, account: Row, connection: Row) -> SyncWindow:
    """D120's window on today's day in Europe/Berlin (bank.window_invalid when the anchor is
    today or later); a first run starts after the imported history, and a later run's
    overlap stops at the end of the history from files without the bank's reference (D173
    (4))."""
    return rules.request_window(
        SyncWindowFacts(
            opening_balance_on=account.opening_balance_on,
            last_booking_on=connection.last_booking_on,
            imports_through=imports_through(ctx.conn, account.id),
            unreferenced_through=unreferenced_through(ctx.conn, account.id),
        ),
        ctx.now.astimezone(LOCAL_TIME_ZONE).date(),
    )


def _announce(ctx: CommandContext, row: Row, trigger: SyncTrigger) -> None:
    """The run's bank.sync_started event and its job, in the caller's transaction."""
    append_event(
        ctx,
        EventType.ACCOUNT_SYNC_STARTED,
        row.account_id,
        BankSyncStarted(
            sync_id=row.id,
            connection_id=row.connection_id,
            trigger=trigger,
            requested_from=row.requested_from,
            requested_to=row.requested_to,
        ),
    )
    enqueue.enqueue_bank_sync(
        ctx.conn, workspace_id=ctx.workspace_id, account_id=row.account_id, sync_id=row.id
    )


# --- The daily run (D120, jobs/bank_sync_schedule.py) ----------------------------------------


@dataclass(frozen=True)
class ScheduledStart:
    """What the daily run did with one connection: `queued` with its run, or why not
    (rules.schedule_skip's reasons, `off` when auto_sync was switched off meanwhile, or
    `auto_sync_waits` for a DKB connection whose approvals do not allow it yet)."""

    outcome: str
    sync: BankSync | None = None


SCHEDULE_QUEUED = "queued"
SCHEDULE_OFF = "off"
SCHEDULE_AUTO_SYNC_WAITS = "auto_sync_waits"  # D173 (5): a DKB connection's guard


def auto_sync_accounts(conn: Connection) -> list[UUID]:
    """The accounts whose connection has auto_sync on (the workspace's, under RLS)."""
    return list(
        conn.execute(
            select(connection_table.c.account_id)
            .where(connection_table.c.auto_sync.is_(True))
            .order_by(connection_table.c.created_at, connection_table.c.account_id)
        ).scalars()
    )


def queue_scheduled_sync(ctx: CommandContext, account_id: UUID) -> ScheduledStart:
    """D120: queue one scheduled run of the account, through the same window, row, event and
    job as a manual start, unless rules.schedule_skip says to leave it alone (a live run, an
    approval the bank asked for, a failure waiting for a person). Never restarts a row: only a
    person's start does that. No rate limit (it counts manual starts) and no created_by. The
    caller checks that the PIN and product id are configured. Raises bank.window_invalid
    when the anchor is today or later."""
    account = require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)
    connection = _connection_row(ctx.conn, account_id)
    if not connection.auto_sync:
        return ScheduledStart(SCHEDULE_OFF)
    if not _auto_sync_allowed(ctx.conn, connection):  # D173 (5): switched on before the guard
        return ScheduledStart(SCHEDULE_AUTO_SYNC_WAITS)
    live = _live_row(ctx.conn, account_id)
    last = _latest_finished(ctx.conn, account_id)
    skip = rules.schedule_skip(
        None if live is None else _S(live.status),
        None if last is None else _S(last.status),
        None if last is None else last.error_code,
    )
    if skip is not None:
        return ScheduledStart(skip)
    window = _window(ctx, account, connection)
    row = ctx.conn.execute(
        insert(sync_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            connection_id=connection.id,
            trigger=SyncTrigger.SCHEDULED.value,
            status=_S.QUEUED.value,
            requested_from=window.start,
            requested_to=window.end,
            started_at=ctx.now,
            created_by=None,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(sync_table)
    ).one()
    _announce(ctx, row, SyncTrigger.SCHEDULED)
    return ScheduledStart(SCHEDULE_QUEUED, _to_sync(row))


def _latest_finished(conn: Connection, account_id: UUID) -> Row | None:
    """The run the schedule decides on: the latest finished one that succeeded or failed for
    a reason a person must see. A bank.unreachable failure is passed over, so an outage after
    a refusal (a wrong PIN, pressed again while the bank was down) never hides the refusal
    and lets the next morning try the PIN unattended (PR #200 review, F1). The failed card
    still shows the latest run, unreachable included (bank_sync_items)."""
    return conn.execute(
        select(sync_table)
        .where(
            sync_table.c.account_id == account_id,
            sync_table.c.finished_at.is_not(None),
            ~(
                (sync_table.c.status == _S.FAILED.value)
                & (sync_table.c.error_code == E.BANK_UNREACHABLE)
            ),
        )
        .order_by(sync_table.c.finished_at.desc(), sync_table.c.id.desc())
        .limit(1)
    ).one_or_none()


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
        "Die Bank hat den Abruf abgelehnt: wegen der Anmeldung (Anmeldename oder PIN), einer"
        " in der S-pushTAN-App abgelehnten Freigabe oder weil sie die erste Einrichtung"
        " nicht zulässt. Der vierstellige Code der Bank sagt, was davon.",
        "Nicht einfach erneut abrufen: nach drei falschen PINs sperrt die Bank den Zugang."
        " Erst den Code im Runbook (Schritt 14) nachsehen und die Ursache beheben.",
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

# What a bank.refused card says for the bank's code (D151): the code's meaning from the DK's
# list of answer codes (FinTS Rückmeldungscodes), in plain words, and what to do
# (principle 7). A code not listed keeps the general bank.refused text with its number.
_PIN_WARNING = "Nicht einfach erneut abrufen: nach drei falschen PINs sperrt die Bank den Zugang."
_CHECK_PIN = (
    "Die PIN im Container (HAUSBUCH_FINTS_PIN) mit der PIN des Online-Bankings vergleichen"
    " und korrigieren (Runbook Schritt 14), erst dann erneut abrufen. " + _PIN_WARNING
)
# Hausbuch drops a stored connection (system id, chosen approval method) only when the FinTS
# address or the login changes (update_connection), so that is the way to drop it by hand.
_DROP_CONNECTION = (
    "Die gespeicherte Verbindung verwerfen: im Bankabruf „Zugang ändern“ den Anmeldenamen"
    " einmal ändern und dann wieder auf den richtigen setzen (nur eine neue Adresse oder ein"
    " neuer Anmeldename verwirft sie). Der nächste Abruf meldet Hausbuch neu an und fragt"
    " dafür in der S-pushTAN-App nach einer Freigabe."
)
_METHOD_NOT_ALLOWED = (
    "Die Bank lässt das Freigabeverfahren nicht (mehr) zu, mit dem Hausbuch sich anmeldet.",
    "Im Online-Banking prüfen, dass S-pushTAN für diesen Zugang eingerichtet und aktiv ist."
    " Danach: " + _DROP_CONNECTION,
)
_OUTDATED = (
    "Die Bank lehnt eine Version des Bankprotokolls ab, die Hausbuch verwendet.",
    "Das liegt nicht an den Zugangsdaten: Hausbuch braucht eine Aktualisierung (python-fints)."
    " Bis dahin Kontoauszüge als Datei hochladen.",
)
_REFUSED_CARD: dict[str, tuple[str, str]] = {
    # the product registration
    "9078": (
        "Die Bank lässt Hausbuch nicht an: sie kennt die Produkt-ID nicht, mit der Hausbuch"
        " sich anmeldet (die Registrierungsnummer der Deutschen Kreditwirtschaft). Entweder"
        " steht im Container eine andere Nummer, oder die Registrierung ist bei dieser Bank"
        " noch nicht angekommen. Der Code betrifft die Produkt-ID, nicht die PIN.",
        "Die Produkt-ID im Container (HAUSBUCH_FINTS_PRODUCT_ID) Zeichen für Zeichen mit der"
        " Nummer aus der E-Mail der Deutschen Kreditwirtschaft vergleichen und bei einer"
        " Abweichung korrigieren (Runbook Schritt 14). Stimmt sie, einige Tage warten und"
        " dann erneut abrufen: eine neue Registrierung erreicht die Banken erst nach einiger"
        " Zeit.",
    ),
    # the PIN
    "9942": ("Die Bank hat die PIN abgelehnt.", _CHECK_PIN),
    "9340": (
        "Die Bank hat die Anmeldung abgelehnt, weil ihre Signatur nicht stimmt; beim Verfahren"
        " mit PIN und Freigabe-App heißt das: die PIN ist falsch.",
        _CHECK_PIN,
    ),
    "9910": (
        "Die Bank meldet die PIN als falsch oder den Zugang als gesperrt.",
        "Einmal selbst im Online-Banking der Bank anmelden. Geht das nicht, ist der Zugang"
        " gesperrt: die Bank entsperrt ihn (oft mit einem neuen PIN-Brief), danach die neue"
        " PIN in den Container. Geht es, die PIN im Container korrigieren. " + _PIN_WARNING,
    ),
    # a locked access
    "9930": (
        "Die Bank hat die PIN gesperrt.",
        "Die Sperre im Online-Banking oder bei der Bank aufheben lassen und danach die PIN im"
        " Container prüfen. Bis dahin nicht erneut abrufen.",
    ),
    "9931": (
        "Die Bank hat den Zugang nach zu vielen Fehlversuchen gesperrt.",
        "Die Bank anrufen und den Zugang entsperren lassen; danach die PIN im Container"
        " prüfen. Bis dahin nicht erneut abrufen.",
    ),
    "3938": (
        "Die Bank hat den Zugang vorläufig gesperrt, meist nach falschen PINs.",
        "Einmal selbst im Online-Banking der Bank anmelden und die Sperre dort aufheben;"
        " danach die PIN im Container prüfen. Bis dahin nicht erneut abrufen.",
    ),
    "9333": (
        "Die Bank hat diesen Zugang für Online-Banking mit PIN noch nicht freigeschaltet.",
        "Einmal mit den Daten aus dem PIN-Brief im Online-Banking der Bank anmelden (dabei"
        " wird meist die Start-PIN geändert) oder die Bank bitten, den Zugang freizuschalten;"
        " dann die gültige PIN in den Container und erneut abrufen.",
    ),
    # the approval in the S-pushTAN app
    "9951": (
        "Die Freigabe ist bei der Bank abgelaufen, bevor sie in der S-pushTAN-App bestätigt war.",
        "Erneut abrufen und die Anfrage gleich in der App freigeben.",
    ),
    "9941": (
        "Die Bank hat die Freigabe nicht angenommen, etwa weil sie in der S-pushTAN-App"
        " abgelehnt wurde; der Code kann auch eine ungültige Signatur, also die PIN, meinen.",
        "Kam in der App keine Anfrage an, zuerst die PIN im Container prüfen. "
        + _PIN_WARNING
        + " Kam eine Anfrage an: erneut abrufen und sie in der App freigeben, nicht ablehnen.",
    ),
    "9980": (
        "Die Bank hat den Freigabe-Vorgang abgebrochen.",
        "Erneut abrufen und die Anfrage in der App freigeben. Kommt der Code wieder, im"
        " Online-Banking nachsehen, ob die Bank eine Änderung des Freigabeverfahrens verlangt.",
    ),
    "9075": (
        "Die Bank verlangt für diesen Abruf eine Freigabe in der S-pushTAN-App.",
        "Erneut abrufen und die Anfrage in der App freigeben. Kommt keine Anfrage, im"
        " Online-Banking prüfen, dass S-pushTAN eingerichtet ist.",
    ),
    # the approval method
    "9955": _METHOD_NOT_ALLOWED,
    "9361": _METHOD_NOT_ALLOWED,
    "9958": _METHOD_NOT_ALLOWED,
    "9380": (
        "Die Bank lässt das Freigabeverfahren, mit dem Hausbuch sich anmeldet, oder diesen"
        " Abruf für den Zugang nicht zu.",
        "Im Online-Banking prüfen, dass S-pushTAN aktiv ist und der Zugang Umsätze abrufen"
        " darf. Danach: " + _DROP_CONNECTION,
    ),
    # the customer system id (the stored connection)
    "9391": (
        "Die Bank verlangt eine neue Kundensystem-ID, die Kennung, unter der sie Hausbuch"
        " wiedererkennt.",
        _DROP_CONNECTION,
    ),
    "9210": (
        "Die Bank hat eine Angabe der Anfrage als ungültig abgelehnt. Der Code hat viele"
        " Bedeutungen; nach einem früheren erfolgreichen Abruf ist es meist die gespeicherte"
        " Kundensystem-ID, die Kennung, unter der die Bank Hausbuch wiedererkennt.",
        _DROP_CONNECTION + " Welche Angabe gemeint war, steht im Container-Log"
        " (bank_answers, Runbook Schritt 14).",
    ),
    # the bank's side
    "9997": ("Die Bank führt gerade Wartungsarbeiten durch.", "Später erneut abrufen."),
    "9311": ("Das PIN/TAN-System der Bank ist gerade nicht verfügbar.", "Später erneut abrufen."),
    "9185": _OUTDATED,
    "9180": _OUTDATED,
}


# Phase 6 D163: the slot's PIN is missing; the card names the variable to set (never a value).
def _pin_missing_card(credential_slot: str) -> tuple[str, str]:
    variable = rules.pin_variable(credential_slot)
    return (
        f"Für den Zugang „{credential_slot}“ ist keine PIN hinterlegt: die Variable {variable}"
        " fehlt im API-Container.",
        f"In der Scaleway-Konsole (Serverless › Containers › API-Container › Einstellungen)"
        f" die geheime Umgebungsvariable (Secret) {variable} mit der PIN dieses Zugangs anlegen"
        " und den Container neu bereitstellen (Runbook Schritt 17), dann erneut abrufen.",
    )


# D173 (1), (2): DKB's approval is in the DKB app, and without camt its CSV export is the way.
_DKB_WORDING = (
    ("das Verfahren S-pushTAN einrichten", "die DKB-App als Freigabeverfahren einrichten"),
    (
        "dass S-pushTAN für diesen Zugang eingerichtet und aktiv ist",
        "dass die DKB-App für diesen Zugang als Freigabeverfahren eingerichtet und aktiv ist",
    ),
    ("dass S-pushTAN eingerichtet ist", "dass die DKB-App als Freigabeverfahren eingerichtet ist"),
    ("dass S-pushTAN aktiv ist", "dass die DKB-App als Freigabeverfahren aktiv ist"),
    ("(pushTAN)", "(DKB-App)"),
    ("S-pushTAN-App", "DKB-App"),
)
_DKB_UNSUPPORTED_FORMAT = (
    "Die DKB liefert über FinTS keine Umsätze in einem Format, das Hausbuch lesen kann (kein"
    " camt).",
    "Die Umsätze weiter als CSV-Export aus dem DKB-Banking („Umsätze exportieren“) hochladen.",
)


def for_bank(text: str, bank_name: str | None) -> str:
    """A card text for the connection's bank: DKB's app instead of the Sparkasse's S-pushTAN
    app (D173 (1)); any other bank's text unchanged."""
    if not rules.is_dkb(bank_name):
        return text
    for sparkasse, dkb in _DKB_WORDING:
        text = text.replace(sparkasse, dkb)
    return text


def failed_card_text(
    error_code: str | None,
    response_code: str | None,
    *,
    credential_slot: str = PRIMARY_SLOT,
    bank_name: str | None = None,
) -> tuple[str, str]:
    """A bank_sync_failed card's reasoning and question: for bank.refused with a known bank
    code the code's own text (D151), for bank.pin_missing the slot's variable (D163), else
    the error code's; worded for the connection's bank (D173 (1)). The reasoning ends with
    the error code and the bank's code, e.g. "(bank.refused, Code der Bank 9078)"."""
    reasoning, question = _FAILED_CARD.get(error_code or "", _FAILED_DEFAULT)
    if error_code == E.BANK_REFUSED and response_code in _REFUSED_CARD:
        reason, question = _REFUSED_CARD[response_code]
        reasoning = f"Die Bank hat den Abruf abgelehnt. {reason}"
    elif error_code == E.BANK_PIN_MISSING and rules.valid_slot(credential_slot):
        reasoning, question = _pin_missing_card(credential_slot)
    elif error_code == E.BANK_UNSUPPORTED_FORMAT and rules.is_dkb(bank_name):
        reasoning, question = _DKB_UNSUPPORTED_FORMAT
    shown = error_code
    if response_code is not None:
        shown = f"{error_code}, Code der Bank {response_code}"
    reasoning, question = for_bank(reasoning, bank_name), for_bank(question, bank_name)
    if credential_slot != PRIMARY_SLOT and rules.valid_slot(credential_slot):
        # the PIN to check is the slot's own container secret
        question = question.replace(
            "(HAUSBUCH_FINTS_PIN)", f"({rules.pin_variable(credential_slot)})"
        )
    return f"{reasoning} ({shown})", question


def _response_code(conn: Connection, row: Row) -> str | None:
    """The bank's deciding code (9xxx, or 3938), kept on the run's sync_failed event only."""
    return conn.execute(
        select(event_table.c.payload["response_code"].astext)
        .where(
            event_table.c.type == EventType.ACCOUNT_SYNC_FAILED.value,
            event_table.c.entity_id == row.account_id,
            event_table.c.payload["sync_id"].astext == str(row.id),
        )
        .order_by(event_table.c.recorded_at.desc())
        .limit(1)
    ).scalar_one_or_none()


# The bank_approval_needed card (D120): its first sentence is the spec's.
APPROVAL_NEEDED_REASONING = (
    "Der automatische Abruf braucht eine Freigabe: jetzt abrufen und in der S-pushTAN-App"
    " bestätigen."
)
APPROVAL_NEEDED_QUESTION = (
    "„Jetzt abrufen“ drücken und die Anfrage gleich in der S-pushTAN-App freigeben. Bis dahin"
    " ruft Hausbuch dieses Konto nicht automatisch ab, damit keine weiteren Anfragen aufs"
    " Handy kommen."
)


def bank_sync_items(conn: Connection) -> list[InboxItem]:
    """The Zu prüfen cards of §4.6, D117 and D120, computed on read:

    - bank_approval_needed while a scheduled run waits at `needs_approval` (the bank asked
      for an approval and the job ended the dialog). A person's start restarts that row as a
      manual run, which ends the card;
    - from each account's latest finished run: bank_sync_failed while it failed,
      bank_balance_drift while it brought nothing new and HKSAL differed from the last
      reconciled closing. A later good run ends either card.

    The texts name the connection's bank's approval app and a missing slot's variable
    (phase 6 D163, D173 (1))."""
    connections = {
        c.id: (c.credential_slot, recorded_bank_name(conn, c.id))
        for c in conn.execute(select(connection_table.c.id, connection_table.c.credential_slot))
    }

    def bank_name(row: Row) -> str | None:
        return connections.get(row.connection_id, (PRIMARY_SLOT, None))[1]

    items = [
        InboxItem(
            kind=InboxItemKind.BANK_APPROVAL_NEEDED,
            created_at=row.updated_at,
            reasoning=for_bank(APPROVAL_NEEDED_REASONING, bank_name(row)),
            question=for_bank(APPROVAL_NEEDED_QUESTION, bank_name(row)),
            account_id=row.account_id,
            sync_id=row.id,
        )
        for row in conn.execute(
            select(sync_table)
            .where(
                sync_table.c.status == _S.NEEDS_APPROVAL.value,
                sync_table.c.trigger == SyncTrigger.SCHEDULED.value,
            )
            .order_by(
                sync_table.c.account_id,
                sync_table.c.updated_at.desc(),
                sync_table.c.id.desc(),
            )
            .distinct(sync_table.c.account_id)
        )
    ]
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
    for row in conn.execute(latest):
        if row.status == _S.FAILED.value:
            slot, name = connections.get(row.connection_id, (PRIMARY_SLOT, None))
            reasoning, question = failed_card_text(
                row.error_code, _response_code(conn, row), credential_slot=slot, bank_name=name
            )
            items.append(
                InboxItem(
                    kind=InboxItemKind.BANK_SYNC_FAILED,
                    created_at=row.finished_at,
                    reasoning=reasoning,
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
    not in it: the job reads them from Settings, the PIN by `credential_slot` (D163)."""

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
    fetch_statements: bool = False  # D154 (7): the PDF statements after the camt part
    credential_slot: str = PRIMARY_SLOT  # phase 6 D163: names the PIN's container secret


class Superseded(Exception):
    """The row was restarted (or finished) by someone else: the job stops without writing."""


def begin_run(
    ctx: CommandContext, sync_id: UUID, *, attempt: int, settings: Settings
) -> RunPlan | None:
    """Take the row for this job. None when there is nothing to run: the row is gone or
    final; a retried job (attempt > 0) of a `needs_approval` row leaves it restartable
    (WORKER_LOST, D118); not configured, a slot without its PIN (bank.pin_missing, D163)
    or a state whose MAC fails records `failed`."""
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
    if connection.credential_slot not in settings.fints_pins:  # before any dialog (D163)
        _fail(ctx, row, E.BANK_PIN_MISSING)
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
        fetch_statements=connection.fetch_statements,
        credential_slot=connection.credential_slot,
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
        _nothing_new(
            ctx,
            row,
            plan,
            move.status,
            lines_total=0,
            balance=fetched.balance,
            last_booking_on=connection.last_booking_on,
        )
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
                    last_booking_on=_later(connection.last_booking_on, preview.last_booking_on),
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
    last_booking_on: date | None,
) -> None:
    """D127 and D117: no document, no import; HKSAL compared with the last reconciled closing
    (the anchor when the account has no line yet; skipped while the latest import is not
    reconciled, which has its own card). D141: the connection's last_booking_on moves to the
    latest booking day the bank returned (every line already stored, e.g. from an upload), so
    the next window starts from there, not from the anchor."""
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
        .values(last_sync_at=ctx.now, last_booking_on=last_booking_on, updated_at=ctx.now)
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


# --- The bank's PDF statements (D154, step 2b) ----------------------------------------------

STATEMENT_MEDIA_TYPE = "application/pdf"
_STATEMENT_LIST_LIMIT = 500


def stored_statement_keys(conn: Connection, account_id: UUID) -> list[StatementKey]:
    """The (year, number) of every statement stored for the account (plan_probes' input)."""
    t = statement_table.c
    rows = conn.execute(select(t.year, t.number).where(t.account_id == account_id)).all()
    return [StatementKey(year=r.year, number=r.number) for r in rows]


def statements_first_year(conn: Connection, account_id: UUID, today: date) -> int:
    """D154 (3): the year of the account's earliest booked line, or today's year when it has
    none (rules.bank_statements never asks before 2026 either way)."""
    t = transaction_table.c
    first = conn.execute(
        select(func.min(t.booking_date)).where(t.account_id == account_id, t.is_pending.is_(False))
    ).scalar_one_or_none()
    return today.year if first is None else first.year


def store_statement(
    ctx: CommandContext, store: BlobStore, plan: RunPlan, key: StatementKey, pdf: bytes
) -> UUID | None:
    """D154 (5): one delivered statement, in its own transaction. The PDF is stored as every
    original (deduplicated by its SHA-256, source bank_sync) and typed by code as a statement
    of its year, titled „Kontoauszug <Nummer>/<Jahr>“, when untyped (a person's own typing of
    the same file is left alone). It is never imported, classified or read by the model:
    nothing is queued. One bank_statement row and one account.statement_fetched event; None
    (and nothing written beyond the idempotent blob) when the account already has that
    number of that year."""
    t = statement_table.c
    known = ctx.conn.execute(
        select(t.id).where(
            t.account_id == plan.account_id, t.year == key.year, t.number == key.number
        )
    ).first()
    if known is not None:
        return None
    document = upload_document(
        ctx,
        store,
        pdf,
        media_type=STATEMENT_MEDIA_TYPE,
        source=DocumentSource.BANK_SYNC,
        original_filename=statement_fetch.statement_filename(key),
    )
    if document.typing is None:
        type_document(
            ctx,
            document.id,
            DocumentTyping(
                type=DocumentType.STATEMENT,
                title=statement_fetch.statement_title(key),
                tax_year=key.year if key.year >= TAX_YEAR_MIN else None,
            ),
        )
    row = ctx.conn.execute(
        pg_insert(statement_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=plan.account_id,
            year=key.year,
            number=key.number,
            document_id=document.id,
            sync_id=plan.sync_id,
            fetched_at=ctx.now,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "account_id", "year", "number"])
        .returning(t.id)
    ).first()
    if row is None:  # a concurrent run stored the same number first
        return None
    append_event(
        ctx,
        EventType.ACCOUNT_STATEMENT_FETCHED,
        plan.account_id,
        BankStatementFetched(
            sync_id=plan.sync_id, document_id=document.id, year=key.year, number=key.number
        ),
    )
    return document.id


def list_statements(
    conn: Connection, account_id: UUID, limit: int = _STATEMENT_LIST_LIMIT
) -> list[BankStatement]:
    """The account's stored PDF statements, newest year and number first."""
    t = statement_table.c
    rows = conn.execute(
        select(t.id, t.account_id, t.year, t.number, t.document_id, t.sync_id, t.fetched_at)
        .where(t.account_id == account_id)
        .order_by(t.year.desc(), t.number.desc())
        .limit(limit)
    ).all()
    return [BankStatement.model_validate(dict(r._mapping)) for r in rows]
