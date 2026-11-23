"""The morning chain's registration and its pure helpers (jobs.morning). The order, the gate
and the fallbacks against the job table are tests/db/test_jobs_morning.py."""

import inspect
import uuid
from datetime import UTC, datetime

import procrastinate
from procrastinate import testing

from hausbuch.jobs import morning, tasks
from hausbuch.jobs import sync_wallets as wallets_job
from hausbuch.jobs.fetch_prices import FETCH_PRICES_TASK
from hausbuch.jobs.health_daily import HEALTH_DAILY_CRON, HEALTH_DAILY_TASK
from hausbuch.jobs.kept_answers import WARM_CRON, WARM_TASK
from hausbuch.jobs.month_end import MONTH_END_CRON, MONTH_END_TASK
from hausbuch.rules.errors import WALLET_SOURCE_FAILED, WALLET_SOURCE_THROTTLED
from hausbuch.schemas.chain import WalletSyncStatus
from hausbuch.services.chain.sync import SyncOutcome


def _app():
    return tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))


def test_the_gate_is_a_task_with_its_own_lock_and_no_clock():
    app = _app()
    gate = app.tasks[morning.GATE_TASK]
    assert gate.lock == morning.GATE_LOCK
    assert not [p for p in app.periodic_registry.periodic_tasks.values() if p.task is gate]


def test_every_chained_step_is_deferred_under_its_tasks_lock():
    app = _app()
    for task, lock in morning.STEP_LOCKS.items():
        assert app.tasks[task].lock == lock
    assert morning.NEXT_STEP == {MONTH_END_TASK: HEALTH_DAILY_TASK, HEALTH_DAILY_TASK: WARM_TASK}
    assert FETCH_PRICES_TASK in morning.STEP_LOCKS


def test_the_fallbacks_keep_their_clock_times():
    crons = (MONTH_END_CRON, HEALTH_DAILY_CRON, WARM_CRON)
    assert crons == ("0 8 1 * *", "30 8 * * *", "0 9 * * *")


def test_the_day_is_the_utc_day_of_the_clock():
    bounds = morning._day(datetime(2026, 12, 1, 23, 59, tzinfo=UTC))
    assert bounds["end"] - bounds["start"] == 86400
    assert bounds["start"] == int(datetime(2026, 12, 1, tzinfo=UTC).timestamp())
    assert morning._is_first(datetime(2026, 12, 1, 6, 30, tzinfo=UTC))
    assert not morning._is_first(datetime(2026, 12, 2, 6, 30, tzinfo=UTC))


def test_the_chained_price_fetch_is_never_earlier_than_seven():
    seven = datetime(2026, 12, 1, 7, 0, tzinfo=UTC)
    assert morning.prices_floor(datetime(2026, 12, 1, 6, 31, tzinfo=UTC)) == {"schedule_at": seven}
    assert morning.prices_floor(datetime(2026, 12, 1, 0, 5, tzinfo=UTC)) == {"schedule_at": seven}
    assert morning.prices_floor(seven) == {}
    assert morning.prices_floor(datetime(2026, 12, 1, 9, 0, tzinfo=UTC)) == {}


def _outcome(status, code=None):
    return SyncOutcome(
        address_id=uuid.uuid4(), chain="bitcoin", sync_id=None, status=status, error_code=code
    )


def test_only_a_failed_source_is_retried():
    ws = uuid.uuid4()
    failed = _outcome(WalletSyncStatus.FAILED, WALLET_SOURCE_FAILED)
    others = [
        _outcome(WalletSyncStatus.SUCCEEDED),
        _outcome(WalletSyncStatus.THROTTLED, WALLET_SOURCE_THROTTLED),
        _outcome(WalletSyncStatus.FAILED, "wallet.something_else"),
        _outcome(None),
    ]
    assert wallets_job._retry_targets(ws, [failed, *others]) == [(ws, failed.address_id)]


def test_the_retry_argument_round_trips_ids_only():
    pairs = [(uuid.uuid4(), uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())]
    encoded = wallets_job.encode_targets(pairs)
    assert all(isinstance(v, str) for v in encoded)
    assert wallets_job.decode_targets(encoded) == pairs


def test_what_the_chain_defers_binds_to_each_tasks_signature():
    """The kwargs jobs.morning writes into a job row (tests/db/test_jobs_morning.py reads
    them back) are what the worker passes to the wrapper in jobs.tasks."""
    app = _app()
    rows = {
        FETCH_PRICES_TASK: {"timestamp": 1},
        MONTH_END_TASK: {"timestamp": 1},
        HEALTH_DAILY_TASK: {"timestamp": 1},
        WARM_TASK: {"timestamp": 1},
        morning.GATE_TASK: {"timestamp": 1, "finished": "42"},
        wallets_job.SYNC_WALLETS_RETRY_TASK: {"timestamp": 1, "addresses": ["a:b"]},
    }
    for name, kwargs in rows.items():
        inspect.signature(app.tasks[name].func).bind(object(), **kwargs)
    inspect.signature(app.tasks[morning.GATE_TASK].func).bind(object(), timestamp=1, finished="")
