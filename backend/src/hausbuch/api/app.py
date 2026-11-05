"""The FastAPI application factory (§2, §3.3, §3.7).

create_app stores its dependencies on app.state and includes every router under /api. This
is the chunk 0 stub: the signature, the router includes and the Worker protocol are final;
chunk 1 adds the lifespan (worker start/stop, drain), error handlers, request logging, the
Origin check and /healthz. Routers are filled by chunk 1 (domain) and chunk 2 (auth);
neither needs to edit the include list.
"""

from importlib.metadata import version
from typing import Protocol, runtime_checkable

from fastapi import FastAPI
from sqlalchemy import Engine

from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

from .auth_context import SessionResolver
from .routes import (
    auth,
    documents,
    history,
    loans,
    owners,
    properties,
    tenancies,
    tenants,
    units,
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
    history,
    auth,
)


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
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.blob_store = blob_store
    app.state.session_resolver = session_resolver
    app.state.worker = worker
    for module in ROUTE_MODULES:
        app.include_router(module.router, prefix=API_PREFIX)
    return app
