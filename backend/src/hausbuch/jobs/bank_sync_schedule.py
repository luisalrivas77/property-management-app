"""The bank_sync_scheduled periodic task: the daily FinTS sync (docs/phases/phase-5.md D120,
§4.6, step 2).

At 06:30 UTC (07:30 in Berlin in winter, 08:30 in summer) the worker visits every provisioned
workspace and, for each account whose connection has `auto_sync` on, queues one `bank_sync`
run with trigger `scheduled` through `services.bank_sync.queue_scheduled_sync`: the same
window rule (`rules.bank_sync.request_window`), the same row, event and job as a manual
start, so the run itself is jobs/bank_sync.py unchanged, import path included.

What keeps the phone quiet (D120): a scheduled run that meets a pushTAN demand ends the dialog
at once and waits at `needs_approval` with the Zu prüfen card `bank_approval_needed`; the
next mornings leave that account alone (`rules.bank_sync.schedule_skip`), so the bank sends
one request and then none until a person presses „Jetzt abrufen“, which restarts the row as
a manual run with the approval polled. A failed latest run also waits for a person (except
bank.unreachable), so a wrong PIN is never tried again on the next morning.

Inert while the FinTS PIN or product id is not configured: one log line, nothing read.

Each account is queued in a transaction of its own, so one account's error (e.g.
bank.window_invalid) is logged by its code and the others still run. Nothing personal is
logged: one line per run with counts and codes; an unexpected failure by its class name.
"""

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.enums import ActorType
from hausbuch.services import bank_sync as service
from hausbuch.services.context import Actor, new_command

from .document_read import JobDeps

SCHEDULE_TASK = "bank_sync_scheduled"
SCHEDULE_CRON = "30 6 * * *"  # D120: 06:30, the worker's clock (UTC in the container)
SCHEDULE_LOCK = "bank_sync:schedule"  # Procrastinate lock=: one running job at a time
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, SCHEDULE_TASK)

logger = logging.getLogger("hausbuch.jobs.bank_sync_schedule")


@dataclass
class ScheduleResult:
    """What one run did: `status` is done or not_configured; `outcomes` counts the accounts
    by what happened to them (queued, or rules.schedule_skip's reason, or off); `codes`
    counts the rule codes of accounts that could not be queued."""

    status: str
    workspaces: int = 0
    failed_workspaces: int = 0
    outcomes: Counter = field(default_factory=Counter)
    codes: Counter = field(default_factory=Counter)


def _log(result: ScheduleResult) -> None:
    fields: dict[str, Any] = {
        "event": SCHEDULE_TASK,
        "status": result.status,
        "workspaces": result.workspaces,
        "failed_workspaces": result.failed_workspaces,
        "outcomes": dict(sorted(result.outcomes.items())),
        "codes": dict(sorted(result.codes.items())),
    }
    level = logging.WARNING if result.failed_workspaces or result.codes else logging.INFO
    logger.log(level, "bank sync schedule", extra={"fields": fields})


def run_bank_sync_scheduled(
    deps: JobDeps, *, workspace_ids: Iterable[UUID] | None = None
) -> ScheduleResult:
    """One daily run. `workspace_ids` is for tests; the task passes none, so every provisioned
    workspace is visited. Never raises for one workspace's or one account's failure."""
    if not service.configured(deps.settings):
        result = ScheduleResult("not_configured")
        _log(result)
        return result
    result = ScheduleResult("done")
    now = deps.clock()
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    for workspace_id in sorted(ids):
        try:
            with workspace_transaction(deps.engine, workspace_id) as conn:
                accounts = service.auto_sync_accounts(conn)
        except Exception as exc:  # class name only: a driver message can quote parameters
            result.failed_workspaces += 1
            result.codes[type(exc).__name__] += 1
            continue
        result.workspaces += 1
        for account_id in accounts:
            try:
                with workspace_transaction(deps.engine, workspace_id) as conn:
                    ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=now)
                    started = service.queue_scheduled_sync(ctx, account_id)
                result.outcomes[started.outcome] += 1
            except RuleError as exc:
                result.outcomes["refused"] += 1
                result.codes[exc.code] += 1
            except Exception as exc:  # class name only
                result.outcomes["error"] += 1
                result.codes[type(exc).__name__] += 1
    _log(result)
    return result
