"""The FastAPI application factory (§2, §3.3, §3.7).

create_app stores its dependencies on app.state, installs the error handlers and the
middlewares, and includes /healthz plus every router under /api. The signature, the router
includes and the Worker protocol are fixed by chunk 0; routers are filled by chunk 1
(domain) and chunk 2 (auth), neither of which edits the include list.

Middleware order, outermost first (each sees what the next one returns):

    RequestMiddleware      request id, catch-all -> 500 ErrorResponse, one log line (logging.py)
    DrainMiddleware        503 shutting_down while the process shuts down (lifespan.py)
    OriginCheckMiddleware  403 forbidden_origin for unsafe methods from elsewhere (csrf.py)
    BodyLimitMiddleware    413 before a too-large body is read (limits.py)
    routes                 exception handlers -> ErrorResponse (errors.py)
"""

import json
from importlib.metadata import version
from typing import Any, Protocol, runtime_checkable

from fastapi import FastAPI
from sqlalchemy import Engine

from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

from . import health
from .auth_context import SessionResolver
from .csrf import OriginCheckMiddleware
from .errors import ERROR_RESPONSES, install_error_handlers
from .lifespan import DrainMiddleware, lifespan
from .limits import BodyLimitMiddleware
from .logging import RequestMiddleware
from .routes import (
    accounts,
    allocations,
    auth,
    documents,
    history,
    loans,
    owners,
    properties,
    proposals,
    statements,
    tax_inputs,
    tenancies,
    tenants,
    units,
    workspace,
)

API_PREFIX = "/api"
# Each module exposes `router`; it is looked up when the app is built.
ROUTE_MODULES = (
    owners,
    properties,
    units,
    tenants,
    tenancies,
    loans,
    documents,
    proposals,
    accounts,
    statements,
    allocations,
    tax_inputs,
    history,
    workspace,
    auth,
)
REQUEST_POOL_SIZE = 5  # §3.2, §3.7
POOL_MAX_OVERFLOW = 5
# FastAPI's built-in OpenTelemetry hooks would record request bodies and validation errors
# (IBANs, tax numbers) once a provider is configured; Hausbuch logs through logging.py only.
_NO_TELEMETRY = {"tracing": False, "metrics": False, "logs": False, "auto_configure": False}


@runtime_checkable
class Worker(Protocol):
    """The in-process job worker the lifespan starts and stops (§3.7). Phase 1 passes None;
    phase 2 wraps Procrastinate's worker. Tests pass a fake."""

    async def start(self) -> None:
        """Start fetching and running jobs in the background; return once started."""
        ...

    async def stop(self, timeout: float) -> None:
        """Stop fetching new jobs, and wait up to `timeout` seconds for the running ones.
        A job still running after that is retried by the next instance."""
        ...


def engine_pool_options(settings: Settings) -> dict[str, Any]:
    """create_app_engine keyword arguments for the shared engine (§3.7): five connections
    for requests plus one per concurrent job, and a small overflow."""
    return {
        "pool_size": REQUEST_POOL_SIZE + settings.worker_concurrency,
        "max_overflow": POOL_MAX_OVERFLOW,
    }


def create_app(
    settings: Settings,
    engine: Engine,
    blob_store: BlobStore,
    session_resolver: SessionResolver,
    worker: Worker | None = None,
) -> FastAPI:
    """The API. `engine` is the app-role engine (create_app_engine); requests and the worker
    share it. No interactive docs are served; `hausbuch openapi` prints app.openapi()."""
    app = FastAPI(
        title="Hausbuch",
        version=version("hausbuch"),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        telemetry=_NO_TELEMETRY,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.blob_store = blob_store
    app.state.session_resolver = session_resolver
    app.state.worker = worker
    app.state.draining = False
    install_error_handlers(app)
    # add_middleware wraps: the last one added is the outermost.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(OriginCheckMiddleware)
    app.add_middleware(DrainMiddleware)
    app.add_middleware(RequestMiddleware)
    app.include_router(health.router)
    for module in ROUTE_MODULES:
        app.include_router(module.router, prefix=API_PREFIX, responses=ERROR_RESPONSES)
    return app


def openapi_document(app: FastAPI) -> str:
    """app.openapi() as committed in frontend/src/api/openapi.json (§5): two-space JSON,
    keys in FastAPI's order, UTF-8 as is, one trailing newline. `hausbuch openapi` should
    print exactly this, so the committed file and the CLI never differ by formatting."""
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
