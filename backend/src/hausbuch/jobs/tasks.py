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
    EXPLAIN_PLAN_TASK,
    FOLLOW_ACCOUNTS_TASK,
    MATCH_IMPORT_TASK,
    MATCH_TRANSACTION_TASK,
    REMATCH_TASK,
    REMATCH_WAITING_TASK,
    REPAIR_SOLANA_LAMPORTS_TASK,
    TAX_EXPORT_TASK,
)
from .explain_plan import run_explain_plan
from .fetch_prices import (
    FETCH_PRICES_CRON,
    FETCH_PRICES_LOCK,
    FETCH_PRICES_TASK,
    run_fetch_prices,
)
from .follow_accounts import run_follow_accounts
from .health_daily import (
    HEALTH_DAILY_CRON,
    HEALTH_DAILY_LOCK,
    HEALTH_DAILY_TASK,
    run_health_daily,
)
from .inbound_mail import POLL_CRON, POLL_LOCK, POLL_TASK, run_poll_inbox
from .kept_answers import (
    CHECK_CRON,
    CHECK_LOCK,
    CHECK_TASK,
    WARM_CRON,
    WARM_LOCK,
    WARM_TASK,
    run_check,
    run_warm,
)
from .lamports_repair import run_repair_solana_lamports
from .matching import (
    run_match_import,
    run_match_transaction,
    run_rematch_on_evidence,
    run_rematch_waiting,
)
from .month_end import MONTH_END_CRON, MONTH_END_LOCK, MONTH_END_TASK, run_month_end
from .notify import NOTIFY_CRON, NOTIFY_LOCK, NOTIFY_TASK, run_send_reminders
from .refresh_crypto import REFRESH_CRYPTO_LOCK, REFRESH_CRYPTO_TASK, run_refresh_crypto
from .scrub import SCRUB_CRON, SCRUB_TASK, run_scrub_proposals
from .sync_wallets import (
    SYNC_WALLET_TASK,
    SYNC_WALLETS_CRON,
    SYNC_WALLETS_LOCK,
    SYNC_WALLETS_TASK,
    run_sync_wallet,
    run_sync_wallets,
)
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


def follow_accounts(context: JobContext, *, workspace_id: str) -> None:
    """Once after the deploy of 14 Nov 2026 (`hausbuch follow-accounts`); no retry."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_follow_accounts(deps, workspace_id=UUID(workspace_id))


def explain_plan(
    context: JobContext, *, workspace_id: str, scenario_id: str, requested_by: str
) -> None:
    """The household plan's explanation (phase 9 D257); no retry: every outcome is stored."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_explain_plan(
        deps,
        workspace_id=UUID(workspace_id),
        scenario_id=UUID(scenario_id) if scenario_id else None,
        requested_by=UUID(requested_by) if requested_by else None,
    )


def repair_solana_lamports(context: JobContext, *, workspace_id: str, dry_run: str) -> None:
    """Once after the deploy of 18 Nov 2026 (`hausbuch repair-solana-lamports`); no retry."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_repair_solana_lamports(deps, workspace_id=UUID(workspace_id), dry_run=dry_run == "yes")


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


def month_end(context: JobContext, *, timestamp: int) -> None:
    """D167 (5), at 08:00 on the 1st: the previous month-end's net worth stored once per
    workspace (a month-end that has a run already is left alone)."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_month_end(deps)


def health_daily(context: JobContext, *, timestamp: int) -> None:
    """Phase 9 §11.5, daily at 08:30: a health_snapshot for every closed month with a
    month-end run and no snapshot yet, per workspace."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_health_daily(deps)


async def warm_kept_answers(context: JobContext, *, timestamp: int) -> None:
    """Phase 10 §7.4, daily at 09:00: the kept routes of Übersicht and Zu prüfen asked once
    per enrolled member, on the API's event loop."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    await run_warm(deps)


async def check_kept_answers(context: JobContext, *, timestamp: int) -> None:
    """Phase 10 §7.4, daily at 15:00: one kept answer recomputed and compared."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    await run_check(deps)


def sync_wallets(context: JobContext, *, timestamp: int) -> None:
    """Phase 7 D182 (1), daily at 06:45: every active wallet address, oldest sync first."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_sync_wallets(deps)


def sync_wallet(context: JobContext, *, workspace_id: str, account_id: str) -> None:
    """„Jetzt abrufen“ on a wallet's page: that wallet's addresses."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_sync_wallet(deps, workspace_id=UUID(workspace_id), account_id=UUID(account_id))


def refresh_crypto(context: JobContext, *, workspace_id: str) -> None:
    """Phase 10 D291, „Jetzt aktualisieren“: the household's wallets read again, then a
    current price for every held coin."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_refresh_crypto(deps, workspace_id=UUID(workspace_id))


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
    # No retry: `hausbuch follow-accounts` can be run again; what it did no longer waits.
    app.task(name=FOLLOW_ACCOUNTS_TASK, pass_context=True)(follow_accounts)
    # No retry: `hausbuch repair-solana-lamports` can be run again; a corrected group stays.
    app.task(name=REPAIR_SOLANA_LAMPORTS_TASK, pass_context=True)(repair_solana_lamports)
    # No retry: a failed or refused call ends in the template row (D257); „Neu erklären“ asks
    # again. Procrastinate's lock (enqueue.explain_lock) runs one explanation at a time.
    app.task(name=EXPLAIN_PLAN_TASK, pass_context=True)(explain_plan)
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
    # No retry: „Monatsabschluss festhalten“ stores a month-end by hand. lock= keeps one run at
    # a time; each capture takes its workspace's advisory lock and writes only a missing one.
    worth = app.task(name=MONTH_END_TASK, pass_context=True, lock=MONTH_END_LOCK)(month_end)
    app.periodic(cron=MONTH_END_CRON, periodic_id=MONTH_END_TASK)(worth)
    # No retry: a month not stored today is stored by the next day's run. lock= keeps one run
    # at a time; each workspace's writes take its advisory lock and store only a missing month.
    figures = app.task(name=HEALTH_DAILY_TASK, pass_context=True, lock=HEALTH_DAILY_LOCK)(
        health_daily
    )
    app.periodic(cron=HEALTH_DAILY_CRON, periodic_id=HEALTH_DAILY_TASK)(figures)
    warm = app.task(name=WARM_TASK, pass_context=True, lock=WARM_LOCK)(warm_kept_answers)
    app.periodic(cron=WARM_CRON, periodic_id=WARM_TASK)(warm)
    check = app.task(name=CHECK_TASK, pass_context=True, lock=CHECK_LOCK)(check_kept_answers)
    app.periodic(cron=CHECK_CRON, periodic_id=CHECK_TASK)(check)
    # No retry: a failed address is read again the next day from the same cursor. One lock
    # for the daily run and „Jetzt abrufen“, so no address is read twice at once.
    wallets = app.task(name=SYNC_WALLETS_TASK, pass_context=True, lock=SYNC_WALLETS_LOCK)(
        sync_wallets
    )
    app.periodic(cron=SYNC_WALLETS_CRON, periodic_id=SYNC_WALLETS_TASK)(wallets)
    app.task(name=SYNC_WALLET_TASK, pass_context=True, lock=SYNC_WALLETS_LOCK)(sync_wallet)
    # No retry: the household presses again. The wallet syncs' lock (D291).
    app.task(name=REFRESH_CRYPTO_TASK, pass_context=True, lock=REFRESH_CRYPTO_LOCK)(refresh_crypto)
    return app
