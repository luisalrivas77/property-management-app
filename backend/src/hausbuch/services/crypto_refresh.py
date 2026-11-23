"""„Jetzt aktualisieren“: queue the refresh_crypto job and report its state
(docs/phases/phase-10.md §7.7, D291).

One run at a time per household: POST takes a transaction-scoped advisory lock on the
workspace, then reads the household's newest refresh_crypto job. While it waits or runs, or
when it finished less than two minutes ago (by Procrastinate's own event of its end, against
the database clock), nothing is queued and that job's state is returned. Otherwise a new job
is queued in the caller's transaction.

Procrastinate's tables have no row-level security (phase 2 D20), so the job is found by its
`workspace_id` argument, as tax_export does. A succeeded run's counts come from its own
workspace.crypto_refreshed event (jobs.refresh_crypto), found by the job's id; an older run's
event is never shown for a newer job. A running job's `prices_done` compares the newest price
read on demand (stamped by the job's clock, the API container's) with Procrastinate's
`started` event of that job (the database clock); a price is read at least one HTTP round
trip after the start, so a small skew does not decide it, and a container clock running
behind leaves the flag false, the card then saying „läuft …“ as before. The job
itself writes the prices and movements, which move the data version (§7.4); queueing it
writes no workspace table.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Connection, func, select, text

from hausbuch.db.tables import event as event_table
from hausbuch.jobs.enqueue import _DEFER_APP
from hausbuch.jobs.refresh_crypto import REFRESH_CRYPTO_LOCK, REFRESH_CRYPTO_TASK
from hausbuch.schemas.crypto_refresh import CryptoRefreshState
from hausbuch.schemas.events import CryptoRefreshed, EventType
from hausbuch.services.context import CommandContext
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.prices.overview import newest_spot

REFUSE_FOR = "2 minutes"  # a second press within this shows the run that just finished

_NEWEST = text(
    "SELECT j.id, j.status::text AS status,"
    " (SELECT max(e.at) FROM procrastinate_events e WHERE e.job_id = j.id"
    "  AND e.type IN ('succeeded', 'failed', 'cancelled', 'aborted')) AS finished_at,"
    " (SELECT max(e.at) FROM procrastinate_events e WHERE e.job_id = j.id"
    "  AND e.type IN ('succeeded', 'failed', 'cancelled', 'aborted'))"
    f"  > now() - interval '{REFUSE_FOR}' AS recent,"
    " (SELECT min(e.at) FROM procrastinate_events e WHERE e.job_id = j.id"
    "  AND e.type = 'started') AS started_at"
    " FROM procrastinate_jobs j"
    " WHERE j.task_name = :task AND j.args->>'workspace_id' = :workspace"
    " ORDER BY j.id DESC LIMIT 1"
)


def _today(now: datetime) -> date:
    return now.astimezone(LOCAL_TIME_ZONE).date()


def _counts(conn: Connection, job_id: int) -> CryptoRefreshed | None:
    """The run's counts from its workspace.crypto_refreshed event, or None (none written)."""
    e = event_table.c
    payload = conn.execute(
        select(e.payload)
        .where(
            e.type == EventType.WORKSPACE_CRYPTO_REFRESHED.value,
            e.payload["job_id"].astext == str(job_id),
        )
        .order_by(e.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return None if payload is None else CryptoRefreshed.model_validate(payload)


def _state(conn: Connection, workspace_id: UUID, now: datetime) -> tuple[CryptoRefreshState, bool]:
    """The newest run's state, and whether a press now is refused (one runs, or one finished
    within REFUSE_FOR)."""
    spot = newest_spot(conn, _today(now))
    job = conn.execute(
        _NEWEST, {"task": REFRESH_CRYPTO_TASK, "workspace": str(workspace_id)}
    ).one_or_none()
    if job is None:
        return CryptoRefreshState(status="idle", spot_read_at=spot), False
    if job.status in ("todo", "doing"):
        # The job prices the held coins before it reads the wallets (jobs.refresh_crypto): a
        # price read on demand since the job started means that step is done.
        done = spot is not None and job.started_at is not None and spot >= job.started_at
        return (
            CryptoRefreshState(
                job_id=job.id, status="running", spot_read_at=spot, prices_done=done
            ),
            True,
        )
    succeeded = job.status == "succeeded"
    counts = _counts(conn, job.id) if succeeded else None
    state = CryptoRefreshState(
        job_id=job.id,
        status="succeeded" if succeeded else "failed",
        finished_at=job.finished_at,
        spot_read_at=spot,
        stored=None if counts is None else counts.stored,
        unpriced=None if counts is None else counts.unpriced,
        wallets_failed=None if counts is None else counts.wallets_failed,
        wallets_aborted=None if counts is None else counts.wallets_aborted,
    )
    return state, bool(job.recent)


def refresh_state(conn: Connection, workspace_id: UUID, now: datetime) -> CryptoRefreshState:
    """GET /crypto/refresh."""
    return _state(conn, workspace_id, now)[0]


def queue_refresh(ctx: CommandContext) -> CryptoRefreshState:
    """POST /crypto/refresh: queue one run unless one runs or just ran (`queued` says which)."""
    key = func.hashtextextended(f"crypto_refresh:{ctx.workspace_id}", 0)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(key)))
    state, refused = _state(ctx.conn, ctx.workspace_id, ctx.now)
    if refused:
        return state
    job_id = _DEFER_APP.configure_task(
        REFRESH_CRYPTO_TASK,
        allow_unknown=True,
        lock=REFRESH_CRYPTO_LOCK,
        connection=ctx.conn.connection.driver_connection,
    ).defer(workspace_id=str(ctx.workspace_id))
    return CryptoRefreshState(
        job_id=job_id, status="running", queued=True, spot_read_at=state.spot_read_at
    )
