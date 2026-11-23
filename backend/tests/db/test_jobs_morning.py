"""The morning chain (jobs.morning) against Procrastinate's job table on the test database:
each step queued when the one it depends on has ended, the gate that queues the month-end
once, the clock's fallback runs that do nothing after a chained run, and the wallets' one
retry.

The work of the price fetch, the month-end, the key figures and the warm step is replaced by
counters here (what each computes is unchanged and tested in its own file); the bank runs
and the wallet reads are the real ones, with the scripted FinTS client and the fake chain
source of tests/db/test_jobs_bank_sync.py and tests/db/test_wallet_sync_core.py. A job a
worker would run is run here by calling its step with the job's own id, the job row moved to
`doing` first and to `succeeded` after, as the worker does."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.test_jobs_bank_sync import Bank, _good
from db.test_services_net_worth import NOW, ON, _account, _import
from db.test_services_net_worth_transit import CURRENT_IBAN, DEPOT_IBAN, _book, _line
from db.test_wallet_sync_core import SETTINGS, SOL_A, wallet, world  # noqa: F401 - fixtures
from fints_fake import factory
from sqlalchemy import select, text

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.jobs import bank_sync as bank_job
from hausbuch.jobs import morning
from hausbuch.jobs.bank_sync_schedule import SCHEDULE_TASK
from hausbuch.jobs.document_read import JobDeps, RetryLater
from hausbuch.jobs.fetch_prices import FETCH_PRICES_LOCK, FETCH_PRICES_TASK
from hausbuch.jobs.health_daily import HEALTH_DAILY_LOCK, HEALTH_DAILY_TASK
from hausbuch.jobs.kept_answers import WARM_LOCK, WARM_TASK
from hausbuch.jobs.month_end import MONTH_END_LOCK, MONTH_END_TASK
from hausbuch.jobs.sync_wallets import (
    RETRY_PRIORITY,
    SYNC_WALLETS_LOCK,
    SYNC_WALLETS_RETRY_TASK,
    SYNC_WALLETS_TASK,
    WalletRunResult,
)
from hausbuch.rules.errors import WALLET_SOURCE_FAILED
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.bank_sync import SyncStatus
from hausbuch.schemas.chain import WalletSyncStatus
from hausbuch.schemas.enums import AccountKind
from hausbuch.services import net_worth
from hausbuch.services.chain.base import ChainSourceError

pytestmark = pytest.mark.db

FIRST = datetime(2026, 12, 1, 6, 30, tzinfo=UTC)  # a month closes: the capture runs
SECOND = datetime(2026, 12, 2, 6, 30, tzinfo=UTC)  # an ordinary day: month_end only hands on


def _delete_jobs(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM procrastinate_jobs"))


@pytest.fixture(autouse=True)
def _no_leftover_jobs(app_engine):
    """The queue is session-wide and the gate reads it whole: each test starts and ends
    empty (tests/db/test_jobs_queue.py)."""
    _delete_jobs(app_engine)
    yield
    _delete_jobs(app_engine)


class Work:
    """Counters in place of the steps' work."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[str] = []

        def record(name):
            def run(deps, *args, **kwargs):
                self.calls.append(name)

            return run

        async def warm(deps, members=None):
            self.calls.append(WARM_TASK)

        monkeypatch.setattr(morning, "run_fetch_prices", record(FETCH_PRICES_TASK))
        monkeypatch.setattr(morning, "run_month_end", record(MONTH_END_TASK))
        monkeypatch.setattr(morning, "run_health_daily", record(HEALTH_DAILY_TASK))
        monkeypatch.setattr(morning, "run_warm", warm)


@pytest.fixture
def work(monkeypatch):
    return Work(monkeypatch)


def _deps(engine, at, settings=SETTINGS, store=None):
    return JobDeps(engine, settings, store, None, clock=lambda: at)


def _jobs(engine, task=None):
    query = "SELECT id, task_name, lock, status, args, priority FROM procrastinate_jobs"
    params = {}
    if task is not None:
        query += " WHERE task_name = :task"
        params["task"] = task
    with engine.begin() as conn:
        return conn.execute(text(query + " ORDER BY id"), params).all()


def _todo(engine, task):
    return [j for j in _jobs(engine, task) if j.status == "todo"]


def _set(engine, job_id, status) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE procrastinate_jobs SET status = :s WHERE id = :id"),
            {"s": status, "id": job_id},
        )


def _add(engine, task, at, status="doing", lock=None, **args) -> int:
    """A job as the clock's periodic deferrer would queue it (`timestamp` = its time)."""
    with engine.begin() as conn:
        job_id = conn.execute(
            text(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, lock, args)"
                " VALUES ('default', :task, :lock, CAST(:args AS jsonb)) RETURNING id"
            ),
            {
                "task": task,
                "lock": lock,
                "args": json.dumps({"timestamp": int(at.timestamp()), **args}),
            },
        ).scalar_one()
    if status != "todo":
        _set(engine, job_id, status)
    return job_id


def _run_gates(engine, deps, ws) -> list[str]:
    """Every waiting gate, one at a time (their lock), as the worker would."""
    outcomes = []
    for gate in _todo(engine, morning.GATE_TASK):
        _set(engine, gate.id, "doing")
        finished = gate.args["finished"]
        outcomes.append(
            morning.run_gate(deps, finished=int(finished) if finished else None, workspace_ids=[ws])
        )
        _set(engine, gate.id, "succeeded")
    return outcomes


def _run_step(engine, task, step, deps, **kwargs):
    (job,) = _todo(engine, task)
    _set(engine, job.id, "doing")
    result = step(deps, job_id=job.id, **kwargs)
    _set(engine, job.id, "succeeded")
    return result


def _network(bank):
    return bank_job.Network(
        client_factory=factory(_good(approval_polls=0), bank.clients),
        sleep=lambda seconds: None,
        monotonic=lambda: 0.0,
    )


@pytest.fixture
def bank(run, read, app_engine, blob_store, tmp_path, fresh_workspaces):
    bank = Bank(run, read, app_engine, blob_store, tmp_path, fresh_workspaces[0])
    bank.auto_sync()
    return bank


def _schedule(bank, at) -> None:
    """The 06:30 bank schedule as its job: queues the morning's runs, then a gate."""
    deps = _deps(bank.engine, at, bank.settings, bank.deps.blob_store)
    job_id = _add(bank.engine, SCHEDULE_TASK, at)
    morning.run_schedule_step(deps, job_id=job_id, workspace_ids=[bank.ws])
    _set(bank.engine, job_id, "succeeded")


def _morning_run(bank):
    (row,) = bank.rows(sync_table, sync_table.c.trigger == "scheduled")
    return row


# --- The order ----------------------------------------------------------------------------


def test_the_morning_runs_in_order_and_the_month_end_waits_for_the_last_bank_run(
    bank, work, monkeypatch
):
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, FIRST, bank.settings, bank.deps.blob_store)

    _schedule(bank, FIRST)
    assert _morning_run(bank).status == SyncStatus.QUEUED.value
    assert _run_gates(engine, deps, ws) == ["waiting_prices"]

    # The wallets end: the price fetch is queued under its own lock, nothing else.
    monkeypatch.setattr(morning, "run_sync_wallets", lambda deps, **kw: WalletRunResult())
    wallets_id = _add(engine, SYNC_WALLETS_TASK, FIRST, lock=SYNC_WALLETS_LOCK)
    morning.run_wallets_step(deps, workspace_ids=[ws])
    _set(engine, wallets_id, "succeeded")
    (prices,) = _todo(engine, FETCH_PRICES_TASK)
    assert prices.lock == FETCH_PRICES_LOCK and prices.args["timestamp"] == int(FIRST.timestamp())

    # The prices end while the morning's bank run is still queued: the gate waits. It runs
    # while the price job is still `doing` (the finisher's id in `finished` counts it).
    (prices,) = _todo(engine, FETCH_PRICES_TASK)
    _set(engine, prices.id, "doing")
    assert morning.run_prices_step(deps, job_id=prices.id) is True
    assert _run_gates(engine, deps, ws) == ["waiting_bank"]
    _set(engine, prices.id, "succeeded")
    assert _jobs(engine, MONTH_END_TASK) == []

    # The bank run ends waiting for a pushTAN approval (an end: nobody waits for a person).
    outcome = morning.run_bank_sync_step(
        deps,
        workspace_id=ws,
        sync_id=_morning_run(bank).id,
        attempt=0,
        network=_network(bank),
    )
    assert outcome == "needs_approval"
    assert _run_gates(engine, deps, ws) == ["queued"]
    (month_end,) = _todo(engine, MONTH_END_TASK)
    assert month_end.lock == MONTH_END_LOCK

    # month_end, then health_daily, then the warm step, each queued by the one before.
    assert _run_step(engine, MONTH_END_TASK, morning.run_month_end_step, deps) is True
    (health,) = _todo(engine, HEALTH_DAILY_TASK)
    assert health.lock == HEALTH_DAILY_LOCK
    assert _run_step(engine, HEALTH_DAILY_TASK, morning.run_health_step, deps) is True
    (warm,) = _todo(engine, WARM_TASK)
    assert warm.lock == WARM_LOCK
    _set(engine, warm.id, "doing")
    assert asyncio.run(morning.run_warm_step(deps, job_id=warm.id)) is True
    assert work.calls == [FETCH_PRICES_TASK, MONTH_END_TASK, HEALTH_DAILY_TASK, WARM_TASK]
    # a person's sync later that day queues no gate: today's month_end exists
    morning.queue_gate(engine, FIRST + timedelta(hours=6), finished=None)
    assert _todo(engine, morning.GATE_TASK) == []


def test_the_gate_waits_for_the_schedule_and_fires_when_it_queued_no_run(bank, work):
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, FIRST, bank.settings, bank.deps.blob_store)
    bank.auto_sync(on=False)  # nothing to queue this morning
    prices_id = _add(engine, FETCH_PRICES_TASK, FIRST, status="succeeded")
    morning.queue_gate(engine, FIRST, finished=prices_id)
    assert _run_gates(engine, deps, ws) == ["waiting_schedule"]
    _schedule(bank, FIRST)
    assert bank.rows(sync_table) == []
    assert _run_gates(engine, deps, ws) == ["queued"]
    assert len(_jobs(engine, MONTH_END_TASK)) == 1


def test_a_bank_run_that_will_be_retried_queues_no_gate_and_one_that_raised_does(bank, monkeypatch):
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, FIRST, bank.settings)

    def unreachable(deps, **kwargs):
        raise RetryLater(60)

    monkeypatch.setattr(morning, "run_bank_sync", unreachable)
    with pytest.raises(RetryLater):
        morning.run_bank_sync_step(deps, workspace_id=ws, sync_id=ws, attempt=0)
    assert _jobs(engine, morning.GATE_TASK) == []

    def broken(deps, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(morning, "run_bank_sync", broken)
    with pytest.raises(RuntimeError):
        morning.run_bank_sync_step(deps, workspace_id=ws, sync_id=ws, attempt=0)
    assert len(_todo(engine, morning.GATE_TASK)) == 1


def test_two_finishers_at_once_queue_the_month_end_once(bank, work):
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, FIRST, bank.settings)
    _add(engine, SCHEDULE_TASK, FIRST, status="succeeded")
    prices_id = _add(engine, FETCH_PRICES_TASK, FIRST)  # still `doing`
    # The price job and a bank run end together: each queues a gate.
    morning.queue_gate(engine, FIRST, finished=prices_id)
    morning.queue_gate(engine, FIRST, finished=None)
    assert len(_todo(engine, morning.GATE_TASK)) == 2
    # The gates run one at a time (one lock): the first queues month_end, the second sees it.
    assert _run_gates(engine, deps, ws) == ["queued", "already"]
    assert [(j.status, j.lock) for j in _jobs(engine, MONTH_END_TASK)] == [("todo", MONTH_END_LOCK)]


def test_a_gate_without_the_finishers_id_does_not_count_a_running_price_job(bank, work):
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, FIRST, bank.settings)
    _add(engine, SCHEDULE_TASK, FIRST, status="succeeded")
    _add(engine, FETCH_PRICES_TASK, FIRST)  # `doing`, not the gate's finisher
    morning.queue_gate(engine, FIRST, finished=None)
    assert _run_gates(engine, deps, ws) == ["waiting_prices"]


def test_a_running_schedule_counts_only_as_the_schedule(bank, work):
    """No connection queues a run (FinTS off): the schedule's gate, picked up while the
    schedule job is still `doing`, must not count it as the prices too."""
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, FIRST, bank.settings)
    schedule_id = _add(engine, SCHEDULE_TASK, FIRST)  # `doing`
    morning.queue_gate(engine, FIRST, finished=schedule_id)
    assert _run_gates(engine, deps, ws) == ["waiting_prices"]
    assert _jobs(engine, MONTH_END_TASK) == []


def test_a_running_price_job_counts_only_as_the_prices(bank, work):
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, FIRST, bank.settings)
    prices_id = _add(engine, FETCH_PRICES_TASK, FIRST)  # `doing`, no schedule today
    morning.queue_gate(engine, FIRST, finished=prices_id)
    assert _run_gates(engine, deps, ws) == ["waiting_schedule"]
    assert _jobs(engine, MONTH_END_TASK) == []


def test_yesterdays_runs_do_not_count_today(bank, work):
    engine, ws = bank.engine, bank.ws
    deps = _deps(engine, SECOND, bank.settings)
    _add(engine, SCHEDULE_TASK, FIRST, status="succeeded")
    _add(engine, FETCH_PRICES_TASK, FIRST, status="succeeded")
    _add(engine, MONTH_END_TASK, FIRST, status="succeeded")
    morning.queue_gate(engine, SECOND, finished=None)
    assert _run_gates(engine, deps, ws) == ["waiting_prices"]


# --- The clock's fallbacks ------------------------------------------------------------------

STEPS = [
    (FETCH_PRICES_TASK, morning.run_prices_step, morning.GATE_TASK),
    (MONTH_END_TASK, morning.run_month_end_step, HEALTH_DAILY_TASK),
    (HEALTH_DAILY_TASK, morning.run_health_step, WARM_TASK),
]


# What the clock's run does after a chained run of the same task succeeded today: the prices
# nothing (never fetched twice a day); the month-end and the key figures their idempotent work
# again (a last-day payment booked after the chained capture), handing nothing on.
AGAIN = {
    FETCH_PRICES_TASK: [],
    MONTH_END_TASK: [MONTH_END_TASK],
    HEALTH_DAILY_TASK: [HEALTH_DAILY_TASK],
}


@pytest.mark.parametrize(("task", "step", "following"), STEPS)
def test_the_fallback_hands_nothing_on_after_the_chained_run(
    app_engine, work, task, step, following
):
    deps = _deps(app_engine, FIRST + timedelta(hours=2))
    _add(app_engine, task, FIRST, status="succeeded")  # the chained run, done
    fallback = _add(app_engine, task, FIRST + timedelta(hours=1))
    assert step(deps, job_id=fallback) is False
    assert work.calls == AGAIN[task] and _jobs(app_engine, following) == []


@pytest.mark.parametrize(("task", "step", "following"), STEPS)
def test_the_fallback_does_the_work_when_the_chain_broke(app_engine, work, task, step, following):
    deps = _deps(app_engine, FIRST + timedelta(hours=2))
    _add(app_engine, task, FIRST, status="failed")  # the chained run raised: no success
    fallback = _add(app_engine, task, FIRST + timedelta(hours=1))
    assert step(deps, job_id=fallback) is True
    assert work.calls == [task] and len(_todo(app_engine, following)) == 1


def test_the_warm_fallback_does_nothing_after_the_chained_warm(app_engine, work):
    deps = _deps(app_engine, FIRST + timedelta(hours=3))
    _add(app_engine, WARM_TASK, FIRST, status="succeeded")
    fallback = _add(app_engine, WARM_TASK, FIRST + timedelta(hours=2))
    assert asyncio.run(morning.run_warm_step(deps, job_id=fallback)) is False
    assert work.calls == []
    assert asyncio.run(morning.run_warm_step(deps, job_id=None)) is False


def test_prices_are_fetched_once_a_day_even_when_chained_after_the_fallback(app_engine, work):
    deps = _deps(app_engine, FIRST + timedelta(hours=1))
    _add(app_engine, FETCH_PRICES_TASK, FIRST + timedelta(hours=1), status="succeeded")
    chained = _add(app_engine, FETCH_PRICES_TASK, FIRST + timedelta(minutes=70))  # late wallets
    assert morning.run_prices_step(deps, job_id=chained) is False
    assert work.calls == [] and _jobs(app_engine, morning.GATE_TASK) == []


def test_on_a_day_that_closes_no_month_the_month_end_only_hands_on(app_engine, work):
    deps = _deps(app_engine, SECOND)
    chained = _add(app_engine, MONTH_END_TASK, SECOND)
    assert morning.run_month_end_step(deps, job_id=chained) is True
    assert work.calls == []
    (health,) = _todo(app_engine, HEALTH_DAILY_TASK)
    assert health.lock == HEALTH_DAILY_LOCK


def test_the_clocks_month_end_recaptures_a_last_day_payment_booked_after_the_chain(
    app_engine, run, read, fresh_workspaces
):
    """The gate fires once the last bank run has ended, while its matching jobs may still be
    queued: a transfer debited on the month's last day is booked after the chained capture.
    The 08:00 run captures again (a new run, the money in transit to the depot counted) and
    hands nothing on: health_daily was queued once, by the chained run."""
    ws, _ = fresh_workspaces
    current = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=CURRENT_IBAN, opening="1000.00"
    )
    depot = _account(run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN)

    def stored(ctx):
        statement = _import(ctx, current, date(2026, 7, 1), ON)
        debit = _line(ctx, current, statement, ON, "-500.00", iban=DEPOT_IBAN)
        cash = _import(ctx, depot, date(2026, 7, 1), date(2026, 10, 20))
        _line(ctx, depot, cash, date(2026, 10, 1), "200.00")
        return debit

    debit = run(ws, stored, now=NOW)  # the bank run imported it; its match job still waits
    morning_at = datetime(2026, 11, 1, 6, 50, tzinfo=UTC)

    chained = _add(app_engine, MONTH_END_TASK, morning_at)
    deps = _deps(app_engine, morning_at)
    assert morning.run_month_end_step(deps, job_id=chained, workspace_ids=[ws]) is True
    _set(app_engine, chained, "succeeded")
    early = read(ws, net_worth.latest_month_end)
    assert early.total == Decimal("700.00")  # the 500 € sent on 31 Oct are in no line yet
    assert len(_todo(app_engine, HEALTH_DAILY_TASK)) == 1

    _book(run, ws, debit, "-500.00", AllocationCategory.INTERNAL_TRANSFER_OUT)  # the match

    clock_at = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    fallback = _add(app_engine, MONTH_END_TASK, clock_at)
    deps = _deps(app_engine, clock_at)
    assert morning.run_month_end_step(deps, job_id=fallback, workspace_ids=[ws]) is False
    late = read(ws, net_worth.latest_month_end)
    assert late.total == Decimal("1200.00")  # recaptured: the money on its way to the depot
    assert late.run.net_worth_run_id != early.run.net_worth_run_id
    assert len(_jobs(app_engine, HEALTH_DAILY_TASK)) == 1  # no second hand-on


# --- The prices' 07:00 floor ----------------------------------------------------------------


def _scheduled_at(engine, job_id):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT scheduled_at FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
        ).scalar_one()


def test_the_chained_price_fetch_waits_for_seven_and_not_after(app_engine, monkeypatch):
    monkeypatch.setattr(morning, "run_sync_wallets", lambda deps, **kw: WalletRunResult())
    morning.run_wallets_step(_deps(app_engine, FIRST), workspace_ids=[])  # 06:30
    (early,) = _jobs(app_engine, FETCH_PRICES_TASK)
    assert _scheduled_at(app_engine, early.id) == datetime(2026, 12, 1, 7, 0, tzinfo=UTC)
    assert early.args["timestamp"] == int(FIRST.timestamp())

    _delete_jobs(app_engine)
    late_at = datetime(2026, 12, 1, 7, 10, tzinfo=UTC)
    morning.run_wallets_step(_deps(app_engine, late_at), workspace_ids=[])
    (late,) = _jobs(app_engine, FETCH_PRICES_TASK)
    assert _scheduled_at(app_engine, late.id) is None  # at once


def test_the_retry_queues_the_price_fetch_with_the_same_floor(app_engine, monkeypatch):
    monkeypatch.setattr(morning, "run_retry_wallets", lambda deps, targets: WalletRunResult())
    at = datetime(2026, 12, 1, 6, 41, tzinfo=UTC)
    morning.run_wallet_retry_step(_deps(app_engine, at), [])
    (prices,) = _jobs(app_engine, FETCH_PRICES_TASK)
    assert _scheduled_at(app_engine, prices.id) == datetime(2026, 12, 1, 7, 0, tzinfo=UTC)


# --- The wallets' one retry -----------------------------------------------------------------


def test_a_failed_address_is_read_once_more_and_never_a_third_time(
    app_engine,
    blob_store,
    world,  # noqa: F811 - a fixture imported above
    wallet,  # noqa: F811 - a fixture imported above
):
    ws, _, a, _ = wallet
    deps = _deps(app_engine, FIRST, store=blob_store)
    world.error[SOL_A] = ChainSourceError(WALLET_SOURCE_FAILED, 500)

    result = morning.run_wallets_step(deps, workspace_ids=[ws])
    assert result.retry == [(ws, a)]
    assert _jobs(app_engine, FETCH_PRICES_TASK) == []  # the prices wait for the retry
    (retry,) = _jobs(app_engine, SYNC_WALLETS_RETRY_TASK)
    assert (retry.status, retry.lock, retry.priority) == ("todo", SYNC_WALLETS_LOCK, RETRY_PRIORITY)
    assert retry.args["addresses"] == [f"{ws}:{a}"]
    with app_engine.begin() as conn:  # about ten minutes from now
        waits = conn.execute(
            text(
                "SELECT scheduled_at > now() + interval '9 minutes' FROM procrastinate_jobs"
                " WHERE id = :id"
            ),
            {"id": retry.id},
        ).scalar_one()
    assert waits is True

    # The retry fails again: the prices are queued, and no second retry.
    again = morning.run_wallet_retry_step(deps, retry.args["addresses"])
    assert again.statuses == {WALLET_SOURCE_FAILED: 1} and again.retry == []
    assert len(_jobs(app_engine, SYNC_WALLETS_RETRY_TASK)) == 1
    (prices,) = _jobs(app_engine, FETCH_PRICES_TASK)
    assert prices.lock == FETCH_PRICES_LOCK


def test_the_retry_reads_only_the_failed_address_and_succeeds(
    app_engine,
    blob_store,
    world,  # noqa: F811 - a fixture imported above
    wallet,  # noqa: F811 - a fixture imported above
):
    ws, _, a, b = wallet
    deps = _deps(app_engine, FIRST, store=blob_store)
    world.error[SOL_A] = ChainSourceError(WALLET_SOURCE_FAILED, 500)
    morning.run_wallets_step(deps, workspace_ids=[ws])
    (retry,) = _jobs(app_engine, SYNC_WALLETS_RETRY_TASK)

    world.error.clear()  # the provider answers again
    morning.run_wallet_retry_step(deps, retry.args["addresses"])
    with workspace_transaction(app_engine, ws) as conn:
        rows = conn.execute(
            text("SELECT address_id, status FROM wallet_sync ORDER BY started_at, address_id")
        ).all()
    by_address = {}
    for address_id, status in rows:
        by_address.setdefault(address_id, []).append(status)
    assert by_address[a] == [WalletSyncStatus.FAILED.value, WalletSyncStatus.SUCCEEDED.value]
    assert by_address[b] == [WalletSyncStatus.SUCCEEDED.value]  # read once, in the morning run
    assert len(_jobs(app_engine, FETCH_PRICES_TASK)) == 1


def test_a_wallet_run_that_raised_still_queues_the_prices(app_engine, monkeypatch):
    def broken(deps, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(morning, "run_sync_wallets", broken)
    with pytest.raises(RuntimeError):
        morning.run_wallets_step(_deps(app_engine, FIRST), workspace_ids=[])
    assert len(_todo(app_engine, FETCH_PRICES_TASK)) == 1


def test_the_bank_rows_read_are_only_todays_scheduled_runs(bank):
    engine, ws = bank.engine, bank.ws
    _schedule(bank, FIRST)
    assert morning.open_morning_runs(engine, FIRST, [ws]) == 1
    assert morning.open_morning_runs(engine, SECOND, [ws]) == 0  # yesterday's run
    with workspace_transaction(engine, ws) as conn:
        conn.execute(sync_table.update().values(trigger="manual"))  # a person restarted it
    assert morning.open_morning_runs(engine, FIRST, [ws]) == 0
    with workspace_transaction(engine, ws) as conn:
        assert conn.execute(select(sync_table.c.status)).scalar_one() == "queued"
