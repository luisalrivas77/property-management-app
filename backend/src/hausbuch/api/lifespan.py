"""Startup and shutdown of the API process, which also hosts the job worker (§3.7).

startup:   if settings.run_worker and app.state.worker: the readable bank logins left from
           before migration 0039 are encrypted (phase 6 D196, never raises), then
           await worker.start();
           if settings.kept_refresh and the kept answers' store is on, whether or not the
           worker runs: the refresh task of phase 10 §7.5 (c) (api/kept_refresh.py);
           if settings.kept_warm_at_start and the store is on: one warm of the kept answers
           15 s later, yielding to people's requests (api/kept_start_warm.py)
shutdown:  1. app.state.draining = True   new API requests get 503 shutting_down, Retry-After 2;
                                          /healthz still answers, so the platform does not
                                          kill the instance early
           1b. the refresh task and the start warm, if still running, cancelled and awaited
                                          for up to 5 s each (cancelling ends them at once; a
                                          route one ran in a thread finishes on its own and
                                          its answer is dropped)
           2. await worker.stop(grace)   stop fetching jobs, let the running one finish for up
                                          to settings.shutdown_grace_s; bounded here as well,
                                          so a stuck worker cannot hold the shutdown
           3. engine.dispose()            last: requests and jobs share the engine

Uvicorn runs this shutdown after in-flight requests have finished (--timeout-graceful-shutdown
25 bounds that), so draining mostly answers keep-alive stragglers and requests that arrive
while the worker winds down.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from hausbuch.services.bank_credentials import encrypt_all_logins

from .errors import render, shutting_down

HEALTH_PATH = "/healthz"
# Extra time past the worker's own grace before the lifespan stops waiting for stop().
STOP_MARGIN_S = 1.0

logger = logging.getLogger("hausbuch.api.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = app.state
    settings = state.settings
    state.draining = False
    worker = state.worker if settings.run_worker else None
    kept = getattr(state, "kept_answers", None)
    refresher = getattr(state, "kept_refresher", None)
    if not (settings.kept_refresh and kept is not None and kept.enabled):
        refresher = None
    start_warm = getattr(state, "kept_start_warm", None)
    if not (settings.kept_warm_at_start and kept is not None and kept.enabled):
        start_warm = None
    if worker is not None:
        # D196: before the worker takes a sync and before the instance serves (uvicorn binds
        # its port only after this start-up returns, so nothing is answering yet); in a
        # thread, so the blocking database work stays off the event loop.
        await asyncio.to_thread(encrypt_all_logins, state.engine, settings)
        await worker.start()
        logger.info("worker started", extra={"fields": {"event": "worker_started"}})
    if refresher is not None:
        refresher.start()
    if start_warm is not None:
        start_warm.start()
    try:
        yield
    finally:
        state.draining = True
        if refresher is not None:
            await refresher.stop()
        if start_warm is not None:
            await start_warm.stop()
        if worker is not None:
            await _stop_worker(worker, settings.shutdown_grace_s)
        state.engine.dispose()
        logger.info("shutdown complete", extra={"fields": {"event": "shutdown_complete"}})


async def _stop_worker(worker, grace_s: float) -> None:
    try:
        await asyncio.wait_for(worker.stop(grace_s), timeout=grace_s + STOP_MARGIN_S)
    except TimeoutError:
        fields = {"event": "worker_stop_timeout", "grace_s": grace_s}
        logger.warning("worker did not stop in time", extra={"fields": fields})
    except Exception as exc:  # the engine must still be disposed
        fields = {"event": "worker_stop_failed", "exc_type": type(exc).__name__}
        logger.error("worker stop failed", extra={"fields": fields})
    else:
        logger.info("worker stopped", extra={"fields": {"event": "worker_stopped"}})


class DrainMiddleware:
    """503 shutting_down for every request but /healthz once shutdown has begun."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and getattr(scope["app"].state, "draining", False)
            and scope["path"] != HEALTH_PATH
        ):
            rendered = render(shutting_down(), scope.get("state", {}).get("request_id"))
            scope.setdefault("state", {})["error_log"] = rendered.log
            await rendered.response()(scope, receive, send)
            return
        await self.app(scope, receive, send)
