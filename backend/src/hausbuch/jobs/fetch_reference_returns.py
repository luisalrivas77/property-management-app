"""The funds series' downloads (docs/phases/phase-10.md §7.11 (c) 3, point 8, D310 amended).

- fetch_reference_returns, daily at 06:15 UTC: for every provisioned workspace whose newest
  download is due (services.reference_returns.due: none stored, or the newest lacks last year
  and is more than 30 days old), the file is asked once for the run and stored per
  workspace. Nothing is asked when no workspace is due.
- refresh_reference_returns, one workspace: POST /household-goal/reference-returns/refresh
  and `hausbuch fetch-reference-returns`; asks and stores whether due or not.

A failed or refused file stores nothing (the newest good download keeps counting; with none
the goal says `series_missing`) and is logged by its exception class and HTTP status only. A
workspace whose store fails is logged by its exception class and never stops the others. The
refresh raises on failure, so the job ends `failed`. No retry: the next day's run asks again.
One lock for both, so they never write at once.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.services import reference_returns as R
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.prices.http import Fetch, SourceError, urllib_fetch

from .document_read import JobDeps

FETCH_REFERENCE_TASK = R.FETCH_TASK
REFRESH_REFERENCE_TASK = R.REFRESH_TASK
REFERENCE_LOCK = R.LOCK
# Before the wallets and the bank (06:30) and after the reminders (06:00), the worker's clock.
FETCH_REFERENCE_CRON = "15 6 * * *"

logger = logging.getLogger("hausbuch.jobs.fetch_reference_returns")


@dataclass
class ReferenceResult:
    due: int = 0
    stored: int = 0
    failed_workspaces: int = 0
    error: str | None = None  # the download's exception class, when it failed
    status: int | None = None


def _log_failure(exc: SourceError, what: str) -> None:
    logger.warning("%s: download failed (%s, status %s)", what, exc.kind, exc.status)


def run_fetch_reference_returns(
    deps: JobDeps, *, fetch: Fetch = urllib_fetch, workspace_ids: Iterable[UUID] | None = None
) -> ReferenceResult:
    """One daily run; never raises for one workspace. `fetch`, `workspace_ids` for tests."""
    now = deps.clock()
    today = now.astimezone(LOCAL_TIME_ZONE).date()
    result = ReferenceResult()
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    due: list[UUID] = []
    for ws in sorted(set(ids)):
        try:
            with workspace_transaction(deps.engine, ws) as conn:
                if R.due(conn, today, now):
                    due.append(ws)
        except Exception as exc:  # noqa: BLE001 - one workspace never stops the others
            result.failed_workspaces += 1
            logger.warning("%s: a workspace failed (%s)", R.FETCH_TASK, type(exc).__name__)
    result.due = len(due)
    if due:  # asked once for the run, outside any transaction
        try:
            rows = R.download(fetch)
        except SourceError as exc:
            result.error, result.status = exc.kind, exc.status
            _log_failure(exc, R.FETCH_TASK)
            due = []
    for ws in due:
        try:
            with workspace_transaction(deps.engine, ws) as conn:
                R.store(conn, ws, rows, today)
            result.stored += 1
        except Exception as exc:  # noqa: BLE001 - one workspace never stops the others
            result.failed_workspaces += 1
            logger.warning("%s: a workspace failed (%s)", R.FETCH_TASK, type(exc).__name__)
    logger.info(
        "%s: %d due, %d stored, %d failed",
        R.FETCH_TASK,
        result.due,
        result.stored,
        result.failed_workspaces,
    )
    return result


def run_refresh_reference_returns(
    deps: JobDeps, *, workspace_id: UUID, fetch: Fetch = urllib_fetch
) -> UUID:
    """One workspace, due or not: the stored download's id, or SourceError."""
    now = deps.clock()
    try:
        rows = R.download(fetch)
    except SourceError as exc:
        _log_failure(exc, R.REFRESH_TASK)
        raise
    with workspace_transaction(deps.engine, workspace_id) as conn:
        stored = R.store(conn, workspace_id, rows, now.astimezone(LOCAL_TIME_ZONE).date())
    logger.info("%s: stored %d years", R.REFRESH_TASK, len(rows))
    return stored
