"""JobWorker's supervisor and the retry strategy, with a fake Procrastinate app (no database)."""

import asyncio

from procrastinate.jobs import Job

from hausbuch.jobs.document_read import RetryLater
from hausbuch.jobs.tasks import DEPS_KEY, RetryWhenAsked
from hausbuch.jobs.worker import JobWorker


class FakeJobManager:
    def __init__(self, stalled=(), fail: bool = False) -> None:
        self.stalled = list(stalled)
        self.fail = fail
        self.sweeps = 0
        self.retried: list = []

    async def get_stalled_jobs(self, **kwargs):
        self.sweeps += 1
        self.kwargs = kwargs
        if self.fail:
            raise OSError("connection refused by db.internal")
        stalled, self.stalled = self.stalled, []
        return stalled

    async def retry_job(self, job):
        self.retried.append(job.id)


class FakeApp:
    def __init__(self, crashes: int = 0, job_manager: FakeJobManager | None = None) -> None:
        self.job_manager = job_manager or FakeJobManager()
        self.crashes = crashes
        self.runs = 0
        self.opened = 0
        self.closed = 0
        self.options: dict = {}
        self.cancelled = asyncio.Event()

    async def open_async(self):
        self.opened += 1

    async def close_async(self):
        self.closed += 1

    async def run_worker_async(self, **options):
        self.runs += 1
        self.options = options
        if self.runs <= self.crashes:
            raise OSError("connection refused by db.internal")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def _worker(app: FakeApp, **kwargs) -> JobWorker:
    return JobWorker(
        app,
        deps="DEPS",
        concurrency=2,
        graceful_timeout_s=20,
        restart_first_delay_s=0.01,
        **kwargs,
    )


async def _until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_start_runs_and_stop_cancels_gracefully():
    app = FakeApp()
    worker = _worker(app)

    async def scenario():
        assert not worker.running
        await worker.start()
        await _until(lambda: app.runs == 1)
        assert worker.running
        await worker.stop(1)
        assert app.cancelled.is_set()
        assert not worker.running

    asyncio.run(scenario())
    assert app.options["concurrency"] == 2
    assert app.options["install_signal_handlers"] is False
    assert app.options["shutdown_graceful_timeout"] == 20
    assert app.options["additional_context"] == {DEPS_KEY: "DEPS"}
    assert app.closed >= 1


def test_a_crashed_worker_is_restarted(caplog):
    app = FakeApp(crashes=2)
    worker = _worker(app)

    async def scenario():
        await worker.start()
        await _until(lambda: app.runs == 3)
        assert worker.running
        await worker.stop(1)

    asyncio.run(scenario())
    assert app.opened == 3
    assert "db.internal" not in caplog.text  # only the exception class is logged


def test_retry_strategy_retries_only_when_asked():
    strategy = RetryWhenAsked()
    job = Job(id=1, queue="default", lock=None, queueing_lock=None, task_name="t", attempts=0)
    decision = strategy.get_retry_decision(exception=RetryLater(60), job=job)
    assert decision is not None and decision.retry_at is not None
    assert strategy.get_retry_decision(exception=RuntimeError("x"), job=job) is None


def _job(job_id: int) -> Job:
    return Job(id=job_id, queue="default", lock="doc:x", queueing_lock=None, task_name="t")


def test_stalled_jobs_are_retried_at_start_and_then_periodically():
    manager = FakeJobManager(stalled=[_job(7), _job(8)])
    app = FakeApp(job_manager=manager)
    worker = _worker(app, stalled_sweep_interval_s=0.01)

    async def scenario():
        await worker.start()
        await _until(lambda: app.runs == 1 and manager.sweeps >= 3)
        assert worker.running
        await worker.stop(1)
        sweeps = manager.sweeps
        await asyncio.sleep(0.05)
        assert manager.sweeps == sweeps  # the periodic sweep stopped with the worker

    asyncio.run(scenario())
    assert manager.retried == [7, 8]
    # Only jobs whose worker is gone or missed its heartbeat for 30 s (it beats every 10 s).
    assert manager.kwargs == {"seconds_since_heartbeat": 30}


def test_a_failing_sweep_is_logged_and_the_worker_keeps_running(caplog):
    manager = FakeJobManager(fail=True)
    app = FakeApp(job_manager=manager)
    worker = _worker(app, stalled_sweep_interval_s=0.01)

    async def scenario():
        await worker.start()
        await _until(lambda: app.runs == 1 and manager.sweeps >= 3)
        assert worker.running
        await worker.stop(1)

    asyncio.run(scenario())
    assert app.runs == 1  # the worker loop was never restarted by a sweep failure
    assert "stalled_sweep_failed" in str([getattr(r, "fields", None) for r in caplog.records])
    assert "db.internal" not in caplog.text
