"""The in-process job worker (phase-2.md §5.1; api/app.py's Worker protocol, §3.7).

JobWorker wraps procrastinate.App.run_worker_async in a background asyncio task of the API
process. Jobs are synchronous functions, which Procrastinate runs in a thread each, so the
event loop serving requests is never blocked by a model call.

- start(): open the app's connection pool (Procrastinate's own, psycopg async, as the same app
  role as the API) and start the supervisor task; returns at once.
- The supervisor restarts run_worker_async after a backoff if it crashes (the database went
  away, say), so one outage does not leave the instance without a worker until redeploy.
- stop(timeout): cancelling run_worker_async is Procrastinate's graceful stop: no new jobs are
  fetched and running ones get shutdown_graceful_timeout to finish. A sync job still running
  after that cannot be killed; its row stays `doing` when the process exits.
- Stalled jobs. Procrastinate itself never resets such a row: at start a worker only prunes
  the procrastinate_workers rows whose heartbeat is older than 30 s, which sets the jobs'
  worker_id to NULL, and the job stays `doing`, its lock (doc:{id}) blocking every later job
  of the document. So the supervisor sweeps once after opening the pool, before the worker
  loop starts, and then every STALLED_SWEEP_INTERVAL_S while it runs: every `doing` job whose
  worker row is gone, or whose worker missed its heartbeat for STALLED_AFTER_S (it beats
  every 10 s, so a live worker's jobs are never selected), is retried now (Procrastinate's
  get_stalled_jobs + retry_job; the retry counts as an attempt). A failed sweep is logged and
  the next one tries again; it never stops the worker.
- running: whether the worker loop is up; /healthz reports it as `worker: ok | stopped`.
"""

import asyncio
import logging
from collections.abc import Callable

import procrastinate
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from hausbuch.ai.client import AnthropicModelClient, ModelClient
from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

from .document_read import JobDeps
from .tasks import DEPS_KEY, register_tasks

WORKER_NAME = "hausbuch-api"
RESTART_FIRST_DELAY_S = 1.0
RESTART_MAX_DELAY_S = 60.0
STALLED_SWEEP_INTERVAL_S = 60.0
# Procrastinate's own stalled_worker_timeout default; heartbeats come every 10 s.
STALLED_AFTER_S = 30

logger = logging.getLogger("hausbuch.jobs.worker")


class JobWorker:
    def __init__(
        self,
        app: procrastinate.App,
        deps: JobDeps,
        *,
        concurrency: int,
        graceful_timeout_s: float,
        restart_first_delay_s: float = RESTART_FIRST_DELAY_S,
        stalled_sweep_interval_s: float = STALLED_SWEEP_INTERVAL_S,
        run_options: dict | None = None,
    ) -> None:
        self._app = app
        self._deps = deps
        self._concurrency = concurrency
        self._graceful_timeout_s = graceful_timeout_s
        self._restart_first_delay_s = restart_first_delay_s
        self._stalled_sweep_interval_s = stalled_sweep_interval_s
        self._run_options = run_options or {}
        self._supervisor: asyncio.Task | None = None
        self._stopping = False
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and self._supervisor is not None and not self._supervisor.done()

    async def start(self) -> None:
        """Start the supervisor and return; the pool opens inside it, so a database that is
        not reachable yet delays the worker, never the API's startup."""
        if self._supervisor is not None:
            raise RuntimeError("worker already started")
        self._stopping = False
        self._supervisor = asyncio.create_task(self._supervise(), name="hausbuch job worker")

    async def stop(self, timeout: float) -> None:
        self._stopping = True
        supervisor, self._supervisor = self._supervisor, None
        try:
            if supervisor is not None and not supervisor.done():
                supervisor.cancel()
                done, _ = await asyncio.wait({supervisor}, timeout=timeout)
                if not done:
                    fields = {"event": "worker_stop_timeout", "timeout_s": timeout}
                    logger.warning("jobs still running at shutdown", extra={"fields": fields})
        finally:
            self._running = False
            await self._close()

    async def wait(self) -> None:
        """Until the supervisor ends (tests run it with wait=False: drain the queue, stop)."""
        if self._supervisor is not None:
            await asyncio.shield(self._supervisor)

    async def _supervise(self) -> None:
        delay = self._restart_first_delay_s
        options = {
            "name": WORKER_NAME,
            "concurrency": self._concurrency,
            "wait": True,
            "install_signal_handlers": False,
            "shutdown_graceful_timeout": self._graceful_timeout_s,
            "additional_context": {DEPS_KEY: self._deps},
        } | self._run_options
        while not self._stopping:
            try:
                await self._app.open_async()
                await self.sweep_stalled_jobs()  # before the loop: a wait=False run sees it
                sweeper = asyncio.create_task(self._sweep_periodically(), name="stalled sweep")
                try:
                    self._running = True
                    await self._app.run_worker_async(**options)
                finally:
                    sweeper.cancel()
                    await asyncio.gather(sweeper, return_exceptions=True)
            except asyncio.CancelledError:
                self._running = False
                raise
            except Exception as exc:  # class name only: driver messages can name the host
                fields = {"event": "worker_crashed", "exc_type": type(exc).__name__}
                logger.error("job worker stopped; restarting", extra={"fields": fields})
                await self._close()
            self._running = False
            if self._stopping or not options["wait"]:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, RESTART_MAX_DELAY_S)

    async def sweep_stalled_jobs(self) -> int:
        """Retry the `doing` jobs of dead workers (module docstring); how many were retried.
        Never raises: a failure is logged (class name only) and 0 returned."""
        manager = self._app.job_manager
        retried = 0
        try:
            stalled = await manager.get_stalled_jobs(seconds_since_heartbeat=STALLED_AFTER_S)
            for job in stalled:
                await manager.retry_job(job)
                retried += 1
                fields = {"event": "stalled_job_retried", "job_id": job.id, "task": job.task_name}
                logger.warning("stalled job retried", extra={"fields": fields})
        except Exception as exc:
            fields = {"event": "stalled_sweep_failed", "exc_type": type(exc).__name__}
            logger.error("stalled-job sweep failed", extra={"fields": fields})
        return retried

    async def _sweep_periodically(self) -> None:
        while True:
            await asyncio.sleep(self._stalled_sweep_interval_s)
            await self.sweep_stalled_jobs()

    async def _close(self) -> None:
        try:
            await self._app.close_async()
        except Exception as exc:
            fields = {"event": "worker_close_failed", "exc_type": type(exc).__name__}
            logger.warning("job worker pool not closed cleanly", extra={"fields": fields})


def procrastinate_conninfo(database_url: str) -> str:
    """The libpq URL of the app role (SQLAlchemy's postgresql+psycopg:// form without the
    driver suffix). Holds the password: never logged."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def build_worker(
    settings: Settings,
    engine: Engine,
    blob_store: BlobStore,
    *,
    model_client: ModelClient | None = None,
    connector_kwargs: dict | None = None,
    clock: Callable | None = None,
) -> JobWorker:
    """The production worker. With the AI layer off there is no model client and every
    document_read ends at gate 1 (ai.disabled); the worker still runs the queue."""
    if model_client is None and settings.ai_enabled:
        assert settings.anthropic_api_key  # Settings checks it
        model_client = AnthropicModelClient(
            settings.anthropic_api_key, timeout_s=settings.ai_timeout_s
        )
    connector = procrastinate.PsycopgConnector(
        conninfo=procrastinate_conninfo(settings.database_url),
        min_size=1,
        max_size=settings.worker_concurrency + 2,  # jobs' bookkeeping, LISTEN, heartbeat
        **(connector_kwargs or {}),
    )
    app = register_tasks(procrastinate.App(connector=connector))
    deps = JobDeps(
        engine=engine,
        settings=settings,
        blob_store=blob_store,
        model_client=model_client,
        **({"clock": clock} if clock is not None else {}),
    )
    return JobWorker(
        app,
        deps,
        concurrency=settings.worker_concurrency,
        graceful_timeout_s=settings.shutdown_grace_s,
    )
