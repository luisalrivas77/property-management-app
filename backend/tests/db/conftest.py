"""Database fixtures. Needs HAUSBUCH_TEST_ADMIN_URL: a superuser libpq URL, e.g.
postgresql://postgres:postgres@localhost:5432/postgres. Without it, db tests are skipped.

Each session creates a throwaway database owned by hausbuch_owner, migrates it as that role,
and drops it afterwards. Tests act as a role with SET ROLE, so RLS and grants apply exactly as
in production (superusers bypass RLS; the roles used here do not).

The plain helpers (database lifecycle, engines, seed inserts) live in db_support.py: test
modules import them as `from db.db_support import ...`, never from `conftest` (§10.7).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from db.db_support import (
    ADMIN_URL,
    APP,
    _admin,
    alembic,
    create_database,
    drop_database,
    make_engine,
)
from psycopg import sql

pytestmark = pytest.mark.db


def pytest_collection_modifyitems(items):
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="HAUSBUCH_TEST_ADMIN_URL not set")
    for item in items:
        if "tests/db/" in item.nodeid:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def dbname() -> Iterator[str]:
    name = create_database()
    try:
        alembic(name, "upgrade", "head")
        yield name
    finally:
        drop_database(name)


@pytest.fixture(scope="session")
def workspaces(dbname) -> tuple[uuid.UUID, uuid.UUID]:
    """Two workspaces, created as superuser (sign-up is an admin path)."""
    a, b = uuid.uuid4(), uuid.uuid4()
    with _admin(dbname) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A'), (%s, 'B')", (a, b))
    return a, b


@pytest.fixture
def connect(dbname) -> Iterator:
    """connect(role, workspace) -> a connection in a transaction that is rolled back."""
    opened: list[psycopg.Connection] = []

    @contextmanager
    def _connect(role: str = APP, workspace: uuid.UUID | None = None):
        # SET ROLE and the session-level workspace run under autocommit, so a rollback inside
        # a test can never undo them and silently continue as the superuser (review of step 3
        # chunk 0: SET ROLE inside the implicit transaction reverted to postgres on rollback).
        conn = psycopg.connect(ADMIN_URL, dbname=dbname, autocommit=True)
        opened.append(conn)
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        if workspace is not None:
            conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(workspace),))
        conn.autocommit = False
        try:
            yield conn
        finally:
            conn.rollback()

    yield _connect
    for conn in opened:
        conn.close()


@pytest.fixture
def app_engine(dbname) -> Iterator:
    """Engine acting as hausbuch_app: services run under the app role and RLS."""
    engine = make_engine(dbname)
    yield engine
    engine.dispose()


# --- Document vault ------------------------------------------------------------


@pytest.fixture
def blob_store(tmp_path):
    """A LocalBlobStore of its own per test, under pytest's tmp_path."""
    from hausbuch.storage import LocalBlobStore

    return LocalBlobStore(tmp_path / "blobs")


# --- Domain service helpers ---------------------------------------------------


@pytest.fixture
def fresh_workspaces(dbname) -> tuple[uuid.UUID, uuid.UUID]:
    """Two new, empty workspaces for one test, so committed service data never leaks into
    tests that count the rows of the session-wide `workspaces`."""
    a, b = uuid.uuid4(), uuid.uuid4()
    with _admin(dbname) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A'), (%s, 'B')", (a, b))
    return a, b


@pytest.fixture
def run(app_engine):
    """run(ws, command, *args, now=None, **kwargs): one service command (a function taking a
    CommandContext) in its own workspace transaction, committed on return, as a test user."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command

    actor = Actor(ActorType.USER, "user:test")

    def _run(ws, command, *args, now=None, **kwargs):
        with workspace_transaction(app_engine, ws) as conn:
            return command(new_command(conn, ws, actor, now=now), *args, **kwargs)

    return _run


@pytest.fixture
def read(app_engine):
    """read(ws, query, *args, **kwargs): one query (a function taking a Connection) in its
    own workspace transaction."""
    from hausbuch.db.session import workspace_transaction

    def _read(ws, query, *args, **kwargs):
        with workspace_transaction(app_engine, ws) as conn:
            return query(conn, *args, **kwargs)

    return _read
