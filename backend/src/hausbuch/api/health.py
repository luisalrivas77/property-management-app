"""GET /healthz: the container health check (§3.7). Public, no session, outside /api.

200 {"status": "ok"} when `SELECT 1` succeeds on the app engine within 2 seconds, else 503
{"status": "unavailable"}. The body never says why: no DB host, role or error text. It keeps
answering while the process drains, so the platform does not kill an instance that is still
finishing its work.

Phase 2 (§5.1): with a job worker in the process, the body also says `"worker": "ok"` while
its loop runs and `"stopped"` otherwise (crashed and waiting to restart, or shutting down).
A stopped worker does not make the instance unhealthy: the API still serves, and the
worker's supervisor restarts it. No worker (HAUSBUCH_RUN_WORKER=false, phase 1 tests) and a
worker that does not report `running` leave the field out; api/app.py's Worker protocol does
not declare `running` yet.
"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text

from .lifespan import HEALTH_PATH

PROBE_TIMEOUT_S = 2.0

router = APIRouter()


def _probe(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SET LOCAL statement_timeout = 2000"))
        conn.execute(text("SELECT 1"))


@router.get(HEALTH_PATH, include_in_schema=False)
async def healthz(request: Request) -> JSONResponse:
    engine = request.app.state.engine
    try:
        # The probe blocks (sync driver), so it runs in a thread; if it hangs (pool
        # exhausted, network stall) the answer is 503 after the timeout all the same.
        await asyncio.wait_for(asyncio.to_thread(_probe, engine), timeout=PROBE_TIMEOUT_S)
    except Exception as exc:  # any failure is "unavailable"; only its class is logged
        request.state.error_log = {"error": "unhealthy", "exc_type": type(exc).__name__}
        return JSONResponse({"status": "unavailable"}, status_code=503)
    body = {"status": "ok"}
    worker = getattr(request.app.state, "worker", None)
    running = getattr(worker, "running", None)
    if isinstance(running, bool):
        body["worker"] = "ok" if running else "stopped"
    return JSONResponse(body)
