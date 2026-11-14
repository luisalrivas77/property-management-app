"""Procrastinate task registration (phase-2.md §5.1).

Tasks are registered on an App by register_tasks, so the worker's app (async connector, opened)
knows them; enqueueing goes through configure_task by name (jobs/enqueue.py) and needs no
registration. Task arguments are JSON: ids travel as strings.

The task gets the process's JobDeps from the worker's additional_context, and the attempt
number from its job (0 on the first run). Retrying is decided by run_document_read, which
raises RetryLater; the strategy below only turns that into Procrastinate's retry.
"""

from uuid import UUID

import procrastinate
from procrastinate.job_context import JobContext
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy, RetryDecision

from hausbuch.schemas.tax import TaxExportKind

from .bank_sync import run_bank_sync
from .bank_sync_schedule import (
    SCHEDULE_CRON,
    SCHEDULE_LOCK,
    SCHEDULE_TASK,
    run_bank_sync_scheduled,
)
from .document_read import TASK_NAME, JobDeps, RetryLater, run_document_read
from .enqueue import (
    BANK_SYNC_TASK,
    MATCH_IMPORT_TASK,
    MATCH_TRANSACTION_TASK,
    REMATCH_TASK,
    REMATCH_WAITING_TASK,
    TAX_EXPORT_TASK,
)
from .fetch_prices import (
    FETCH_PRICES_CRON,
    FETCH_PRICES_LOCK,
    FETCH_PRICES_TASK,
    run_fetch_prices,
)
from .inbound_mail import POLL_CRON, POLL_LOCK, POLL_TASK, run_poll_inbox
from .matching import (
    run_match_import,
    run_match_transaction,
    run_rematch_on_evidence,
    run_rematch_waiting,
)
from .notify import NOTIFY_CRON, NOTIFY_LOCK, NOTIFY_TASK, run_send_reminders
from .scrub import SCRUB_CRON, SCRUB_TASK, run_scrub_proposals
from .tax_export import run_tax_export

DEPS_KEY = "hausbuch_deps"


class RetryWhenAsked(BaseRetryStrategy):
    """Retry exactly when the task raised RetryLater, after its wait."""

    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        if isinstance(exception, RetryLater):
            return RetryDecision(retry_in={"seconds": exception.wait_s})
        return None


def document_read(
    context: JobContext,
    *,
    workspace_id: str,
    document_id: str,
    proposal_id: str,
) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_document_read(
        deps,
        workspace_id=UUID(workspace_id),
        document_id=UUID(document_id),
        proposal_id=UUID(proposal_id),
        attempt=context.job.attempts,
    )


def match_import(context: JobContext, *, workspace_id: str, import_id: str) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_match_import(
        deps,
        workspace_id=UUID(workspace_id),
        import_id=UUID(import_id),
        attempt=context.job.attempts,
    )


def match_transaction(
    context: JobContext,
    *,
    workspace_id: str,
    transaction_id: str,
    reask: str = "",
    resort: str = "",
) -> None:
    """`reask` ("1"): the one-off re-ask of a proposed private line (reask_household); a job
    queued before it existed carries none. `resort` ("1"): the one-off re-sort of a line in a
    group's fallback into the subcategories (services.household_resort)."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_match_transaction(
        deps,
        workspace_id=UUID(workspace_id),
        transaction_id=UUID(transaction_id),
        attempt=context.job.attempts,
        reask=reask == "1",
        resort=resort == "1",
    )


def rematch_on_evidence(context: JobContext, *, workspace_id: str, document_id: str) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_rematch_on_evidence(
        deps,
        workspace_id=UUID(workspace_id),
        document_id=UUID(document_id),
        attempt=context.job.attempts,
    )


def rematch_waiting(context: JobContext, *, workspace_id: str, trigger: str) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_rematch_waiting(
        deps,
        workspace_id=UUID(workspace_id),
        trigger=trigger,
        attempt=context.job.attempts,
    )


def tax_export(
    context: JobContext,
    *,
    workspace_id: str,
    export_id: str,
    property_id: str,
    tax_year: str,
    kind: str,
    created_by: str,
    owner_id: str = "",
) -> None:
    """`owner_id` defaults to "" for a job queued before phase 4b, which named a property."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_tax_export(
        deps,
        workspace_id=UUID(workspace_id),
        export_id=UUID(export_id),
        property_id=UUID(property_id) if property_id else None,
        owner_id=UUID(owner_id) if owner_id else None,
        tax_year=int(tax_year),
        kind=TaxExportKind(kind),
        created_by=UUID(created_by) if created_by else None,
        attempt=context.job.attempts,
    )


def bank_sync(context: JobContext, *, workspace_id: str, sync_id: str) -> None:
    """Phase 5 §4.3: one FinTS run. Retried (RetryLater) only for bank.unreachable."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_bank_sync(
        deps,
        workspace_id=UUID(workspace_id),
        sync_id=UUID(sync_id),
        attempt=context.job.attempts,
    )


def scrub_proposals(context: JobContext, *, timestamp: int) -> None:
    """D19/D41, daily. `timestamp` is the scheduled time Procrastinate's periodic deferrer
    passes; the cutoff uses the process clock (deps.clock) instead."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_scrub_proposals(deps)


def poll_inbox(context: JobContext, *, timestamp: int) -> None:
    """D121, every ten minutes: email-in from the household's Gmail label. Off (returns at
    once) until the four HAUSBUCH_INBOX_* values are set."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_poll_inbox(deps)


def send_reminders(context: JobContext, *, timestamp: int) -> None:
    """D123, daily at 06:00: the reminder digest to each member of a workspace whose
    reminder_emails switch is on. Off (one log line) until the HAUSBUCH_TEM_* values are set."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_send_reminders(deps)


def bank_sync_scheduled(context: JobContext, *, timestamp: int) -> None:
    """D120, daily at 06:30: one scheduled bank_sync per account whose connection has
    auto_sync on. Off (one log line) until the FinTS PIN and product id are set."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_bank_sync_scheduled(deps)


def fetch_prices(context: JobContext, *, timestamp: int) -> None:
    """D167 (3), daily at 07:00: the ECB rates, then the held coins' and ETFs' prices for
    every workspace. CoinGecko only with HAUSBUCH_COINGECKO_KEY; Kraken and the rest without."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_fetch_prices(deps)


def register_tasks(app: procrastinate.App) -> procrastinate.App:
    """The queued tasks, plus scrub_proposals as a periodic task: the worker's periodic
    deferrer queues it at SCRUB_CRON. Procrastinate skips a schedule missed by more than
    10 minutes (a worker that was down at 03:15), so that day's scrub moves to the next day;
    the API container runs at min-scale 1, so that is rare and only delays a scrub."""
    retry = RetryWhenAsked()
    app.task(name=TASK_NAME, pass_context=True, retry=retry)(document_read)
    app.task(name=MATCH_IMPORT_TASK, pass_context=True, retry=retry)(match_import)
    app.task(name=MATCH_TRANSACTION_TASK, pass_context=True, retry=retry)(match_transaction)
    app.task(name=REMATCH_TASK, pass_context=True, retry=retry)(rematch_on_evidence)
    app.task(name=REMATCH_WAITING_TASK, pass_context=True, retry=retry)(rematch_waiting)
    app.task(name=TAX_EXPORT_TASK, pass_context=True, retry=retry)(tax_export)
    app.task(name=BANK_SYNC_TASK, pass_context=True, retry=retry)(bank_sync)
    # No retry: a failed scrub runs again the next day. No queueing lock (jobs/enqueue.py
    # says why); Procrastinate's periodic_defers table already defers one job per schedule
    # however many instances run a deferrer, and a duplicate run would scrub nothing.
    scrub = app.task(name=SCRUB_TASK, pass_context=True)(scrub_proposals)
    app.periodic(cron=SCRUB_CRON, periodic_id=SCRUB_TASK)(scrub)
    # No retry: the next poll is ten minutes away. The lock keeps one poll at a time when a
    # slow one is still running at the next tick.
    poll = app.task(name=POLL_TASK, pass_context=True, lock=POLL_LOCK)(poll_inbox)
    app.periodic(cron=POLL_CRON, periodic_id=POLL_TASK)(poll)
    # No retry: a failed mail is retried by the next day's run (D123, up to three attempts).
    # lock= is Procrastinate's lock (one running send_reminders job at a time, a second waits),
    # not a queueing lock; each workspace is also sent under an advisory lock (jobs/notify.py),
    # so not even a run outside the worker can send a digest twice.
    notify = app.task(name=NOTIFY_TASK, pass_context=True, lock=NOTIFY_LOCK)(send_reminders)
    app.periodic(cron=NOTIFY_CRON, periodic_id=NOTIFY_TASK)(notify)
    # No retry: the runs it queues are bank_sync jobs with their own retry, and a missed day
    # is the next day's. lock= keeps one running job at a time; each account is queued under
    # its account row's lock, and a live run is never queued twice (services.bank_sync).
    schedule = app.task(name=SCHEDULE_TASK, pass_context=True, lock=SCHEDULE_LOCK)(
        bank_sync_scheduled
    )
    app.periodic(cron=SCHEDULE_CRON, periodic_id=SCHEDULE_TASK)(schedule)
    # No retry: a failed source asks again the next day (D174's card after seven days).
    # lock= keeps one run at a time; a price row is unique per asset, day and source.
    prices = app.task(name=FETCH_PRICES_TASK, pass_context=True, lock=FETCH_PRICES_LOCK)(
        fetch_prices
    )
    app.periodic(cron=FETCH_PRICES_CRON, periodic_id=FETCH_PRICES_TASK)(prices)
    return app
