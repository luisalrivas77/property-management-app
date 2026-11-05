"""The composition root: `uvicorn hausbuch.api.main:app` (§3.3, §14).

Builds the settings (environment), the app-role engine, the BlobStore and the session
resolver, and hands them to create_app. Nothing is built at import time: `app` is created on
first access (PEP 562 module __getattr__), so importing this module needs no environment.

The API refuses to start under a role that could step outside its least-privilege grants:
- superuser or BYPASSRLS (create_app_engine, hausbuch.db.session), and
- the schema owner (§10.5): a role that owns (or is a member of the owner of) the schema's
  tables, or that may INSERT into `workspace`. As the owner the API would gain INSERT on
  workspace and DELETE on event. Provisioning as the owner is the CLI's job (hausbuch.cli).
The owner check runs once at startup and again on every new pooled connection.

The startup check is retried on connection errors only, with a bounded exponential backoff
(§10.8 item 4): Scaleway can start the container before its private network is ready. A role
refusal is never retried; it fails at once.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import psycopg
from fastapi import FastAPI
from sqlalchemy import Engine, event, text
from sqlalchemy import exc as sa_exc

from hausbuch.api.app import engine_pool_options
from hausbuch.api.logging import configure_logging
from hausbuch.auth.sessions import SessionResolverImpl
from hausbuch.db.session import UnsafeDatabaseRoleError, create_app_engine
from hausbuch.settings import Settings, load_settings
from hausbuch.storage import BlobStore, LocalBlobStore

from .app import create_app

# Ownership through membership counts (pg_has_role MEMBER): SET ROLE would make it effective.
_OWNER_CHECK = """
SELECT current_user,
       EXISTS (
           SELECT 1 FROM pg_class c
           WHERE c.relnamespace = current_schema()::regnamespace
             AND c.relkind IN ('r', 'p')
             AND pg_has_role(current_user, c.relowner, 'MEMBER')
       ),
       coalesce(has_table_privilege(current_user, to_regclass('workspace'), 'INSERT'), false)
"""

log = logging.getLogger("hausbuch.api.main")

# Startup retry (§10.8 item 4): waits of 1, 2, 4, 8 and 16 s between six attempts, and no
# new attempt once the next wait would pass the budget (attempts that hang count too).
STARTUP_ATTEMPTS = 6
STARTUP_FIRST_DELAY_S = 1.0
STARTUP_BUDGET_S = 60.0
# The database could not be reached. Role refusals (UnsafeDatabaseRoleError and its
# subclass SchemaOwnerRoleError) are RuntimeErrors, deliberately outside this tuple.
_CONNECTION_ERRORS: tuple[type[Exception], ...] = (
    sa_exc.OperationalError,
    sa_exc.InterfaceError,
    psycopg.OperationalError,
)


class SchemaOwnerRoleError(UnsafeDatabaseRoleError):
    """The API's database role is the schema owner, or can create workspaces."""


def build_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    blob_store: BlobStore | None = None,
) -> FastAPI:
    """The production app. Tests may pass an engine (it gets the same owner refusal) and a
    store; everything else comes from the environment."""
    settings = settings if settings is not None else load_settings()
    engine = engine if engine is not None else create_api_engine(settings)
    refuse_schema_owner(engine)
    check_database_role_with_retry(engine)  # fail at startup, not on the first request
    store = blob_store if blob_store is not None else create_blob_store(settings)
    resolver = SessionResolverImpl(engine, settings)
    return create_app(settings, engine, store, resolver, worker=None)  # phase 1: no jobs


def create_api_engine(settings: Settings) -> Engine:
    # One source for the pool sizing (§3.7): chunk 1's engine_pool_options.
    return create_app_engine(settings.database_url, **engine_pool_options(settings))


def create_blob_store(settings: Settings) -> BlobStore:
    if settings.blob_store == "local":
        assert settings.blob_root is not None  # Settings checks it
        return LocalBlobStore(settings.blob_root)
    # s3 (chunk 5, §8). from_settings builds the client with the store's own timeouts, retry
    # mode and checksum settings (Content-MD5 instead of botocore's default checksums), which a
    # plain boto3.client would lose. Imported lazily so local setups need no boto3.
    from hausbuch.storage_s3 import S3BlobStore

    return S3BlobStore.from_settings(settings)


def refuse_schema_owner(engine: Engine) -> Engine:
    """Check every new physical connection of `engine` (idempotent)."""
    if not event.contains(engine, "connect", _refuse_owner_on_connect):
        event.listen(engine, "connect", _refuse_owner_on_connect)
    return engine


def check_database_role(engine: Engine) -> None:
    """Raise SchemaOwnerRoleError if the engine's role is the schema owner (or may create
    workspaces). Connecting also runs create_app_engine's superuser/BYPASSRLS refusal."""
    with engine.connect() as conn:
        role, owns_tables, inserts_workspace = conn.execute(text(_OWNER_CHECK)).one()
        conn.rollback()
    _raise_if_owner(role, owns_tables, inserts_workspace)


def check_database_role_with_retry(
    engine: Engine,
    *,
    attempts: int = STARTUP_ATTEMPTS,
    first_delay_s: float = STARTUP_FIRST_DELAY_S,
    budget_s: float = STARTUP_BUDGET_S,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """check_database_role, retried while the database cannot be reached. A role refusal
    raises at once. Each failed attempt is logged by error class only: a driver message can
    name the host, and the URL (with its password) is never logged."""
    sleep = sleep if sleep is not None else time.sleep
    started = clock()
    delay = first_delay_s
    for attempt in range(1, attempts + 1):
        try:
            check_database_role(engine)
        except _CONNECTION_ERRORS as exc:
            error = type(getattr(exc, "orig", None) or exc).__name__
            fields = {"event": "db_unreachable", "attempt": attempt, "of": attempts}
            if attempt == attempts or clock() - started + delay > budget_s:
                log.error(
                    "database unreachable at startup (%s); giving up after %d attempt(s)",
                    error,
                    attempt,
                    extra={"fields": fields | {"error": error}},
                )
                raise
            log.warning(
                "database unreachable at startup (%s), attempt %d of %d; retrying in %g s",
                error,
                attempt,
                attempts,
                delay,
                extra={"fields": fields | {"error": error, "retry_in_s": delay}},
            )
            sleep(delay)
            delay *= 2
        else:
            if attempt > 1:
                log.info(
                    "database reachable after %d attempts",
                    attempt,
                    extra={"fields": {"event": "db_reachable", "attempt": attempt}},
                )
            return


def _refuse_owner_on_connect(dbapi_connection, _connection_record) -> None:
    with dbapi_connection.cursor() as cursor:
        cursor.execute(_OWNER_CHECK)
        role, owns_tables, inserts_workspace = cursor.fetchone()
    dbapi_connection.rollback()  # leave no transaction open on the fresh connection
    _raise_if_owner(role, owns_tables, inserts_workspace)


def _raise_if_owner(role: str, owns_tables: bool, inserts_workspace: bool) -> None:
    if owns_tables or inserts_workspace:
        raise SchemaOwnerRoleError(
            f"database role {role!r} owns the schema's tables or may insert workspaces; the "
            "API must connect as the app role (hausbuch_app). The owner URL belongs to the "
            "CLI only (HAUSBUCH_ADMIN_DATABASE_URL)."
        )


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """`hausbuch.api.main:app`, built on first access."""
    global _app
    if name == "app":
        if _app is None:
            # JSON logs with redaction (chunk 1); run uvicorn with --no-access-log.
            configure_logging()
            _app = build_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
