"""The morning chain: each morning job starts when the one it depends on has ended
(23 Nov 2026; until then each waited for its own clock time).

The order (UTC, the worker's clock):

    06:30 bank_sync_scheduled ─ queues one bank_sync per account ─┐
    06:30 sync_wallets ─ (sync_wallets_retry, ~10 min, once) ─ fetch_prices ─┤
                                                                     morning_gate
                                     month_end ─ health_daily ─ kept_answers_warm

- The wallets end → fetch_prices is queued (a wallet read can add a held coin). When an
  address failed with wallet.source_failed, the one retry is queued instead, about ten
  minutes later, and it queues fetch_prices (jobs.sync_wallets).
- The month-end needs the day's bank lines and prices (phase 6 D167 (5)). It is queued by
  `morning_gate`, a small task each finisher queues when it ends: the price fetch, the bank
  schedule, and every bank_sync run. The gate queues month_end once all three hold:
  a fetch_prices job of today succeeded, a bank_sync_scheduled job of today succeeded, and no
  bank_sync row with trigger `scheduled` created today is still queued, running or
  importing (needs_approval, done, nothing_new and failed are ends: nobody waits for a
  person). A finisher's own job row is still `doing` while it queues the gate, so the gate
  takes the finisher's job id (`finished`) and counts that job as ended; a bank_sync run's
  row is final before its gate is queued. The gates run one at a time (GATE_LOCK) and queue
  month_end only when no month_end job of today exists, so two finishers ending together
  queue it once.
- month_end runs the capture on the 1st only (UTC; on another day it would capture last
  month's month-end again) and then queues health_daily, which queues the warm step.

Fallbacks: the clock times stay, as deadlines: fetch_prices 07:30 (07:00 until 23 Nov 2026),
month_end 08:00 on the 1st, health_daily 08:30, kept_answers_warm 09:00. None of these tasks
retries, so a broken link (a job that raised, a worker that was down) must never skip a step
silently. Every run of
these four, chained or by the clock, first asks `step_ran_today`: another job of the same
task with today's timestamp has succeeded. If so it does nothing and hands nothing on (the
run that did the work handed on already); prices are never fetched twice a day (issuers get
one request a day per held fund). A run that did its work queues the next step, so a
fallback month-end on the 1st is followed by its key figures at once. A job that raised is
`failed` and does not count, so the clock's run does the step.

The day of a job is its `timestamp` argument (the scheduled time Procrastinate's periodic
deferrer passes, and the time a chained defer was made), as a UTC date; the day compared is
deps.clock()'s. Procrastinate never deletes finished jobs here (no delete_jobs), so the
rows are the record. No migration, no model call; what each job computes is unchanged.

Every defer names the task's lock: the deferring app knows no registered task. A defer or a
gate that fails is logged by its exception class and never fails the job that ended: the
clock's run is the backstop.
"""

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID

import procrastinate
from sqlalchemy import Engine, and_, func, select, text

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.schemas.bank_sync import SyncStatus, SyncTrigger

from .bank_sync import run_bank_sync
from .bank_sync_schedule import SCHEDULE_TASK, run_bank_sync_scheduled
from .document_read import JobDeps, RetryLater
from .fetch_prices import FETCH_PRICES_LOCK, FETCH_PRICES_TASK, run_fetch_prices
from .health_daily import HEALTH_DAILY_LOCK, HEALTH_DAILY_TASK, run_health_daily
from .kept_answers import WARM_LOCK, WARM_TASK, run_warm
from .month_end import MONTH_END_LOCK, MONTH_END_TASK, run_month_end
from .sync_wallets import (
    RETRY_DELAY_S,
    RETRY_PRIORITY,
    SYNC_WALLETS_LOCK,
    SYNC_WALLETS_RETRY_TASK,
    WalletRunResult,
    decode_targets,
    encode_targets,
    run_retry_wallets,
    run_sync_wallets,
)

GATE_TASK = "morning_gate"
GATE_LOCK = "morning:gate"

# The chained steps and their tasks' locks (the deferring app knows no registered task).
STEP_LOCKS = {
    FETCH_PRICES_TASK: FETCH_PRICES_LOCK,
    MONTH_END_TASK: MONTH_END_LOCK,
    HEALTH_DAILY_TASK: HEALTH_DAILY_LOCK,
    WARM_TASK: WARM_LOCK,
}
NEXT_STEP = {MONTH_END_TASK: HEALTH_DAILY_TASK, HEALTH_DAILY_TASK: WARM_TASK}

# A bank run that has not reached an end (needs_approval is one: nobody waits for a person).
OPEN_SYNC_STATUSES = (SyncStatus.QUEUED, SyncStatus.RUNNING, SyncStatus.IMPORTING)

logger = logging.getLogger("hausbuch.jobs.morning")

# Never opened: defer() with `connection=` uses the given connection (jobs.enqueue's pattern).
_DEFER_APP = procrastinate.App(connector=procrastinate.SyncPsycopgConnector())

_TODAY = (
    "(args->>'timestamp') ~ '^[0-9]+$'"
    " AND (args->>'timestamp')::bigint >= :start AND (args->>'timestamp')::bigint < :end"
)


def _day(now: datetime) -> dict[str, int]:
    """The UTC day of `now` as epoch bounds, for a job's `timestamp` argument."""
    start = datetime.combine(now.astimezone(UTC).date(), time(), tzinfo=UTC)
    return {"start": int(start.timestamp()), "end": int((start + timedelta(days=1)).timestamp())}


def chain_timestamp(now: datetime) -> int:
    return int(now.timestamp())


def step_ran_today(engine: Engine, task: str, now: datetime, *, job_id: int | None) -> bool:
    """Whether another job of `task` with today's timestamp has succeeded. A succeeded job
    either did the step or skipped it because an earlier one had, so the first of the day
    did the work; a failed one does not count."""
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT 1 FROM procrastinate_jobs WHERE task_name = :task"
                    " AND status = 'succeeded' AND id <> :me AND " + _TODAY + " LIMIT 1"
                ),
                {"task": task, "me": -1 if job_id is None else job_id, **_day(now)},
            ).first()
            is not None
        )


def log_skipped(task: str, job_id: int | None) -> None:
    fields = {"event": "morning_step_skipped", "task": task, "job_id": job_id}
    logger.info("morning step already done today", extra={"fields": fields})


def _defer(conn: Any, task: str, lock: str, **options: Any) -> None:
    kwargs = options.pop("kwargs")
    _DEFER_APP.configure_task(
        task,
        allow_unknown=True,
        lock=lock,
        connection=conn.connection.driver_connection,
        **options,
    ).defer(**kwargs)


def _safely(what: str, step: Any, *args: Any, **kwargs: Any) -> Any:
    """A link of the chain: a failure is logged by its class name and never fails the job
    that ended (the clock's run is the backstop)."""
    try:
        return step(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - class name only: a driver message can quote data
        fields = {"event": "morning_chain_failed", "step": what, "exc_type": type(exc).__name__}
        logger.error("morning chain link not queued", extra={"fields": fields})
        return None


def defer_step(engine: Engine, task: str, now: datetime) -> None:
    """Queue one chained step (with its lock and a timestamp of now)."""
    with engine.begin() as conn:
        _defer(conn, task, STEP_LOCKS[task], kwargs={"timestamp": chain_timestamp(now)})


def hand_on(engine: Engine, task: str, now: datetime) -> None:
    """After `task` did its work: queue the step that follows it, if any."""
    following = NEXT_STEP.get(task)
    if following is not None:
        _safely(f"defer:{following}", defer_step, engine, following, now)


def after_wallets(engine: Engine, now: datetime, retry: list[tuple[UUID, UUID]]) -> None:
    """The morning wallet run ended (success or not): queue the one retry of the addresses
    that failed with wallet.source_failed, about ten minutes from now and below „Jetzt
    abrufen“'s priority, or else the price fetch."""

    def queue() -> None:
        with engine.begin() as conn:
            if retry:
                _defer(
                    conn,
                    SYNC_WALLETS_RETRY_TASK,
                    SYNC_WALLETS_LOCK,
                    schedule_in={"seconds": RETRY_DELAY_S},
                    priority=RETRY_PRIORITY,
                    kwargs={"timestamp": chain_timestamp(now), "addresses": encode_targets(retry)},
                )
            else:
                _defer(
                    conn,
                    FETCH_PRICES_TASK,
                    FETCH_PRICES_LOCK,
                    kwargs={"timestamp": chain_timestamp(now)},
                )

    _safely("after_wallets", queue)


def after_wallet_retry(engine: Engine, now: datetime) -> None:
    _safely(f"defer:{FETCH_PRICES_TASK}", defer_step, engine, FETCH_PRICES_TASK, now)


def _month_end_queued_today(conn: Any, now: datetime) -> bool:
    """A month_end job of today exists that has run or will (not a failed or cancelled one:
    then the gate may queue it once more)."""
    return (
        conn.execute(
            text(
                "SELECT 1 FROM procrastinate_jobs WHERE task_name = :task"
                " AND status IN ('todo', 'doing', 'succeeded') AND " + _TODAY + " LIMIT 1"
            ),
            {"task": MONTH_END_TASK, **_day(now)},
        ).first()
        is not None
    )


def queue_gate(engine: Engine, now: datetime, *, finished: int | None) -> None:
    """A finisher ended: queue `morning_gate`, unless today's month-end job is already
    queued (then there is nothing left to decide, e.g. a person's bank sync in the
    afternoon). `finished` is the finisher's own job id (its row is still `doing`)."""

    def queue() -> None:
        with engine.begin() as conn:
            if _month_end_queued_today(conn, now):
                return
            _defer(
                conn,
                GATE_TASK,
                GATE_LOCK,
                kwargs={
                    "timestamp": chain_timestamp(now),
                    "finished": "" if finished is None else str(finished),
                },
            )

    _safely(GATE_TASK, queue)


def _ended(conn: Any, task: str, now: datetime, finished: int | None) -> bool:
    """A job of `task` with today's timestamp succeeded, or is the finisher that queued this
    gate (still `doing` when it did)."""
    return (
        conn.execute(
            text(
                "SELECT 1 FROM procrastinate_jobs WHERE task_name = :task AND " + _TODAY + " AND"
                " (status = 'succeeded' OR (id = :finished AND status = 'doing')) LIMIT 1"
            ),
            {"task": task, "finished": -1 if finished is None else finished, **_day(now)},
        ).first()
        is not None
    )


def open_morning_runs(engine: Engine, now: datetime, workspace_ids: Any = None) -> int:
    """Scheduled bank runs created today that have not reached an end, over every
    workspace. A workspace that cannot be read counts as one open run: the gate then waits
    for the next finisher or the clock's run, never fires early."""
    start = datetime.combine(now.astimezone(UTC).date(), time(), tzinfo=UTC)
    query = select(func.count()).where(
        and_(
            sync_table.c.trigger == SyncTrigger.SCHEDULED.value,
            sync_table.c.created_at >= start,
            sync_table.c.status.in_([s.value for s in OPEN_SYNC_STATUSES]),
        )
    )
    ids = provisioned_workspace_ids(engine) if workspace_ids is None else workspace_ids
    open_runs = 0
    for ws in sorted(set(ids)):
        try:
            with workspace_transaction(engine, ws) as conn:
                open_runs += conn.execute(query).scalar_one()
        except Exception as exc:  # noqa: BLE001 - class name only
            open_runs += 1
            fields = {"event": "morning_gate_workspace_failed", "exc_type": type(exc).__name__}
            logger.error("a workspace's bank runs were not read", extra={"fields": fields})
    return open_runs


def run_gate(deps: JobDeps, *, finished: int | None, workspace_ids: Any = None) -> str:
    """One gate: queue today's month_end when the prices and the morning's bank side have
    ended. Its outcome: queued, already (today's month_end exists), waiting_prices,
    waiting_schedule or waiting_bank. The caller runs under GATE_LOCK, so the check and the
    defer are one step for every other gate."""
    now = deps.clock()
    with deps.engine.begin() as conn:
        if _month_end_queued_today(conn, now):
            outcome = "already"
        elif not _ended(conn, FETCH_PRICES_TASK, now, finished):
            outcome = "waiting_prices"
        elif not _ended(conn, SCHEDULE_TASK, now, finished):
            outcome = "waiting_schedule"
        else:
            outcome = ""
    if not outcome:
        if open_morning_runs(deps.engine, now, workspace_ids):
            outcome = "waiting_bank"
        else:
            with deps.engine.begin() as conn:
                _defer(
                    conn,
                    MONTH_END_TASK,
                    MONTH_END_LOCK,
                    kwargs={"timestamp": chain_timestamp(now)},
                )
            outcome = "queued"
    fields = {"event": GATE_TASK, "outcome": outcome, "finished": finished}
    logger.info("morning gate", extra={"fields": fields})
    return outcome


# The steps as the tasks run them (jobs.tasks wraps each in its Procrastinate task). `job_id`
# is the running job's own id, so its own `doing` row never counts as "ran today".


def run_wallets_step(deps: JobDeps, *, workspace_ids: Any = None) -> WalletRunResult | None:
    """06:30: the morning wallet run, then its one retry or the price fetch, queued even when
    the run raised (the price fetch does not need every wallet read)."""
    now = deps.clock()
    result: WalletRunResult | None = None
    try:
        result = run_sync_wallets(deps, workspace_ids=workspace_ids)
    finally:
        after_wallets(deps.engine, now, [] if result is None else result.retry)
    return result


def run_wallet_retry_step(deps: JobDeps, addresses: list[str]) -> WalletRunResult | None:
    """The one retry (sync_wallets_retry), then the price fetch, never another retry."""
    now = deps.clock()
    result: WalletRunResult | None = None
    try:
        result = run_retry_wallets(deps, decode_targets(addresses))
    finally:
        after_wallet_retry(deps.engine, now)
    return result


def run_prices_step(deps: JobDeps, *, job_id: int | None, **fetch_options: Any) -> bool:
    """The price fetch, at most once a day: skipped when a fetch_prices job of today has
    succeeded. One that did its work queues the gate. Whether it did the work."""
    now = deps.clock()
    if step_ran_today(deps.engine, FETCH_PRICES_TASK, now, job_id=job_id):
        log_skipped(FETCH_PRICES_TASK, job_id)
        return False
    run_fetch_prices(deps, **fetch_options)
    queue_gate(deps.engine, now, finished=job_id)
    return True


def run_schedule_step(deps: JobDeps, *, job_id: int | None, workspace_ids: Any = None) -> None:
    """06:30: the bank schedule queues the morning's bank runs, then the gate (it is one of
    the month-end's three conditions, and the gate fires on a day it queued none)."""
    run_bank_sync_scheduled(deps, workspace_ids=workspace_ids)
    queue_gate(deps.engine, deps.clock(), finished=job_id)


def run_bank_sync_step(
    deps: JobDeps, *, workspace_id: UUID, sync_id: UUID, attempt: int, **run_options: Any
) -> str:
    """One bank run, then the gate once its row has reached an end: on every exit but
    RetryLater (bank.unreachable: the row is queued again and its retry will end it).
    `run_options` go to run_bank_sync (the tests' network)."""
    try:
        outcome = run_bank_sync(
            deps, workspace_id=workspace_id, sync_id=sync_id, attempt=attempt, **run_options
        )
    except RetryLater:
        raise
    except BaseException:
        queue_gate(deps.engine, deps.clock(), finished=None)
        raise
    queue_gate(deps.engine, deps.clock(), finished=None)
    return outcome


def _is_first(now: datetime) -> bool:
    return now.astimezone(UTC).day == 1


def run_month_end_step(deps: JobDeps, *, job_id: int | None, workspace_ids: Any = None) -> bool:
    """The month-end, then health_daily. The capture runs on the 1st (UTC) only: on any other
    day it would capture last month's month-end again, so the chained run only hands on."""
    now = deps.clock()
    if step_ran_today(deps.engine, MONTH_END_TASK, now, job_id=job_id):
        log_skipped(MONTH_END_TASK, job_id)
        return False
    if _is_first(now):
        run_month_end(deps, workspace_ids=workspace_ids)
    else:
        fields = {"event": "month_end_not_due", "job_id": job_id}
        logger.info("no month-end today", extra={"fields": fields})
    hand_on(deps.engine, MONTH_END_TASK, now)
    return True


def run_health_step(deps: JobDeps, *, job_id: int | None, workspace_ids: Any = None) -> bool:
    now = deps.clock()
    if step_ran_today(deps.engine, HEALTH_DAILY_TASK, now, job_id=job_id):
        log_skipped(HEALTH_DAILY_TASK, job_id)
        return False
    run_health_daily(deps, workspace_ids=workspace_ids)
    hand_on(deps.engine, HEALTH_DAILY_TASK, now)
    return True


async def run_warm_step(deps: JobDeps, *, job_id: int | None, members: Any = None) -> bool:
    now = deps.clock()
    ran = await asyncio.to_thread(step_ran_today, deps.engine, WARM_TASK, now, job_id=job_id)
    if ran:
        log_skipped(WARM_TASK, job_id)
        return False
    await run_warm(deps, members)
    return True
