"""The health_daily periodic task (docs/phases/phase-9.md §11.5, D242; phase 9 step 1).

At 08:30 UTC every day, after phase 6's month_end at 08:00 on the 1st, the worker stores a
health_snapshot for every month of every provisioned workspace that has ended, is closed
(D231), has no snapshot yet, follows a closed month (or the household's history starts
after the month before it) and whose month-end read again is complete
(services.health.store_missing_snapshots): the month-end is captured first when no run holds
it or the newest differs from the reading (net_worth.capture_month_end, insert-only), then
the month's key figures as of that month are stored beside that run, with the event
health.snapshot_stored. A month that already has one is left alone, so a second run the
same day stores nothing; a month that closes later (a line confirmed, a statement
imported), whose month before closes later, or whose month-end gap is closed later (a price,
a statement) is stored by the next day's run.

Step 4 adds the second half of §11.5 (one explain_plan job for the default scenario when a
row was stored and ai_private_accounts is on); this step stores the snapshot only.

No retry: a failed workspace is logged by its exception class and the next day's run (or
„Kennzahlen festhalten“) stores the month. lock= keeps one run at a time; each workspace's
writes also take its advisory lock.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.enums import ActorType
from hausbuch.services.context import Actor, new_command
from hausbuch.services.health import store_missing_snapshots

from .document_read import JobDeps

HEALTH_DAILY_TASK = "health_daily"
HEALTH_DAILY_CRON = "30 8 * * *"  # §11.5: 08:30 every day, the worker's clock (UTC)
HEALTH_DAILY_LOCK = "health:daily"
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, HEALTH_DAILY_TASK)

logger = logging.getLogger("hausbuch.jobs.health_daily")


@dataclass
class HealthDailyResult:
    stored: int = 0
    failed_workspaces: int = 0


def run_health_daily(
    deps: JobDeps, *, workspace_ids: Iterable[UUID] | None = None
) -> HealthDailyResult:
    """One run. `workspace_ids` is for tests. Never raises for one workspace's failure."""
    now = deps.clock()
    result = HealthDailyResult()
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    for ws in sorted(set(ids)):
        try:
            with workspace_transaction(deps.engine, ws) as conn:
                ctx = new_command(conn, ws, SYSTEM_ACTOR, now=now)
                stored = store_missing_snapshots(ctx)
        except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
            result.failed_workspaces += 1
            fields = {"event": "health_daily_workspace_failed", "exc_type": type(exc).__name__}
            logger.error("a workspace's key figures were not stored", extra={"fields": fields})
            continue
        result.stored += len(stored)
    fields = {
        "event": "health_snapshots_stored",
        "stored": result.stored,
        "failed_workspaces": result.failed_workspaces,
    }
    level = logging.WARNING if result.failed_workspaces else logging.INFO
    logger.log(level, "health daily run", extra={"fields": fields})
    return result
