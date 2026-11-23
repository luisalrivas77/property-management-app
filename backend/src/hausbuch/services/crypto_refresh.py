"""„Jetzt aktualisieren“: queue the refresh_crypto job and report its state
(docs/phases/phase-10.md §7.7, D291).

One run at a time per household: POST takes a transaction-scoped advisory lock on the
workspace, then reads the household's newest refresh_crypto job. While it waits or runs, or
when it finished less than two minutes ago (by Procrastinate's own event of its end, against
the database clock), nothing is queued and that job's state is returned. Otherwise a new job
is queued in the caller's transaction.

Procrastinate's tables have no row-level security (phase 2 D20), so the job is found by its
`workspace_id` argument, as tax_export does. The job itself writes the prices and movements,
which move the data version (§7.4); queueing it writes no workspace table.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Connection, func, select, text

from hausbuch.jobs.enqueue import _DEFER_APP
from hausbuch.jobs.refresh_crypto import REFRESH_CRYPTO_LOCK, REFRESH_CRYPTO_TASK
from hausbuch.schemas.crypto_refresh import CryptoRefreshState
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
    f"  > now() - interval '{REFUSE_FOR}' AS recent"
    " FROM procrastinate_jobs j"
    " WHERE j.task_name = :task AND j.args->>'workspace_id' = :workspace"
    " ORDER BY j.id DESC LIMIT 1"
)


def _today(now: datetime) -> date:
    return now.astimezone(LOCAL_TIME_ZONE).date()


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
        return CryptoRefreshState(job_id=job.id, status="running", spot_read_at=spot), True
    state = CryptoRefreshState(
        job_id=job.id,
        status="succeeded" if job.status == "succeeded" else "failed",
        finished_at=job.finished_at,
        spot_read_at=spot,
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
