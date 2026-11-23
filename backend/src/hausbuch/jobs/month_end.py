"""The month_end periodic task (docs/phases/phase-6.md D167 (5); phase 6 step 6).

At 08:00 UTC on the 1st the worker stores the previous month-end's net worth for every
provisioned workspace (services.net_worth.capture_month_end): one valuation_snapshot row per
account balance, valued position, property and loan under one net_worth_run_id, and the event
workspace.valuation_snapshot_written. A month-end whose newest run holds the same figures is
left alone, so a second run of the task (a worker restart, a run by hand) writes nothing; one
whose figures changed since (a press of „Monatsabschluss festhalten“ before the morning's
fetches) gets a new run, `recaptured` (review of 15 Nov 2026, finding 3: until then the task
left any stored month-end alone, so an early press froze an early figure). The month-end is
the last day of the month before the run's day in UTC; at 08:00 UTC that is the same day in
Berlin.

It runs once the morning's bank runs and the price fetch have ended (jobs.morning queues it;
08:00 on the 1st the clock's fallback), so the month's last lines and
the last day's prices fetched that morning are in the stored run; a statement or a price that
arrives later still is stored by „Monatsabschluss neu speichern“ on the Übersicht, which
offers it when today's reading of the month-end differs from the stored one.

No retry: a failed workspace is logged by its exception class and „Monatsabschluss
festhalten“ stores the month-end by hand. lock= keeps one run at a time; each capture also
takes the workspace's advisory lock.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.net_worth import SnapshotOutcome
from hausbuch.services.context import Actor, new_command
from hausbuch.services.net_worth import capture_month_end, last_month_end

from .document_read import JobDeps

MONTH_END_TASK = "month_end"
MONTH_END_CRON = "0 8 1 * *"  # D167 (5): 08:00 on the 1st, the worker's clock (UTC)
MONTH_END_LOCK = "net_worth:month_end"
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, MONTH_END_TASK)

logger = logging.getLogger("hausbuch.jobs.month_end")


@dataclass
class MonthEndResult:
    as_of: str = ""
    outcomes: dict[str, int] = field(default_factory=dict)
    failed_workspaces: int = 0


def run_month_end(deps: JobDeps, *, workspace_ids: Iterable[UUID] | None = None) -> MonthEndResult:
    """One run. `workspace_ids` is for tests. Never raises for one workspace's failure."""
    now = deps.clock()
    as_of = last_month_end(now.date())
    result = MonthEndResult(as_of=as_of.isoformat())
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    for ws in sorted(set(ids)):
        try:
            with workspace_transaction(deps.engine, ws) as conn:
                ctx = new_command(conn, ws, SYSTEM_ACTOR, now=now)
                outcome = capture_month_end(ctx, as_of).outcome
        except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
            result.failed_workspaces += 1
            fields = {"event": "month_end_workspace_failed", "exc_type": type(exc).__name__}
            logger.error("a workspace's month-end was not stored", extra={"fields": fields})
            continue
        result.outcomes[outcome.value] = result.outcomes.get(outcome.value, 0) + 1
    fields = {
        "event": "month_end_stored",
        "as_of": result.as_of,
        "written": result.outcomes.get(SnapshotOutcome.WRITTEN.value, 0),
        "recaptured": result.outcomes.get(SnapshotOutcome.RECAPTURED.value, 0),
        "unchanged": result.outcomes.get(SnapshotOutcome.UNCHANGED.value, 0),
        "empty": result.outcomes.get(SnapshotOutcome.EMPTY.value, 0),
        "failed_workspaces": result.failed_workspaces,
    }
    level = logging.WARNING if result.failed_workspaces else logging.INFO
    logger.log(level, "month-end run", extra={"fields": fields})
    return result
