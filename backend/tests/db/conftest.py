"""Database fixtures. Needs HAUSBUCH_TEST_ADMIN_URL: a superuser libpq URL, e.g.
postgresql://postgres:postgres@localhost:5432/postgres. Without it, db tests are skipped.

Each session creates a throwaway database owned by hausbuch_owner, migrates it as that role,
and drops it afterwards. Tests act as a role with SET ROLE, so RLS and grants apply exactly as
in production (superusers bypass RLS; the roles used here do not).
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, pool, text

ADMIN_URL = os.environ.get("HAUSBUCH_TEST_ADMIN_URL")
OWNER, APP = "hausbuch_owner", "hausbuch_app"
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.db


def pytest_collection_modifyitems(items):
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="HAUSBUCH_TEST_ADMIN_URL not set")
    for item in items:
        if "tests/db/" in item.nodeid:
            item.add_marker(skip)


def _admin(dbname: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(ADMIN_URL, autocommit=True, **({"dbname": dbname} if dbname else {}))
    return conn


def create_database() -> str:
    name = f"hausbuch_test_{uuid.uuid4().hex[:10]}"
    with _admin() as conn:
        for role in (OWNER, APP):
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(OWNER)
            )
        )
    return name


def drop_database(name: str) -> None:
    with _admin() as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def alembic(dbname: str, action: str, target: str) -> None:
    """Run an Alembic command as the schema owner on a connection we control."""
    url = psycopg.conninfo.make_conninfo(ADMIN_URL, dbname=dbname)
    engine = create_engine(
        "postgresql+psycopg://", creator=lambda: psycopg.connect(url), poolclass=pool.NullPool
    )
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    with engine.connect() as connection:
        connection.execute(text(f"SET ROLE {OWNER}"))
        cfg.attributes["connection"] = connection
        getattr(command, action)(cfg, target)
        connection.commit()
    engine.dispose()


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
        conn = psycopg.connect(ADMIN_URL, dbname=dbname)
        opened.append(conn)
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        if workspace is not None:
            conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(workspace),))
        try:
            yield conn
        finally:
            conn.rollback()

    yield _connect
    for conn in opened:
        conn.close()


# --- SQLAlchemy engines for service tests --------------------------------------


def make_engine(dbname: str, role: str = APP, **engine_kwargs):
    """An app engine on the test database whose connections act as `role` (SET ROLE once per
    physical connection, so it survives the pool's reset-on-return rollback)."""
    from hausbuch.db.session import create_app_engine

    url = psycopg.conninfo.make_conninfo(ADMIN_URL, dbname=dbname)

    def creator() -> psycopg.Connection:
        conn = psycopg.connect(url, autocommit=True)
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        conn.autocommit = False
        return conn

    return create_app_engine("postgresql+psycopg://", creator=creator, **engine_kwargs)


@pytest.fixture
def app_engine(dbname) -> Iterator:
    """Engine acting as hausbuch_app: services run under the app role and RLS."""
    engine = make_engine(dbname)
    yield engine
    engine.dispose()


# --- Seed helpers (plain SQL; there is no service layer yet) -----------------


def insert_property(conn, ws, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "label": "Wohnung",
        "kind": "etw",
        "street": "Lindenstraße",
        "house_number": "12",
        "postal_code": "10115",
        "city": "Berlin",
        "build_year": 1994,
        "notarized_on": "2024-03-15",
        "purchase_date": "2024-05-01",
        "purchase_price": "289000.00",
    } | overrides
    return _insert(conn, "property", row)


def insert_unit(conn, ws, property_id, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "property_id": property_id,
        "label": "WE 3",
        "floor_area_m2": "68.40",
    } | overrides
    return _insert(conn, "unit", row)


def insert_tenancy(conn, ws, unit_id, start, end=None, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "unit_id": unit_id,
        "start_date": start,
        "end_date": end,
        "rent_model": "fixed",
    } | overrides
    return _insert(conn, "tenancy", row)


def insert_event(conn, ws, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "type": "property.created",
        "entity_type": "property",
        "entity_id": uuid.uuid4(),
        "actor_type": "user",
        "actor_id": "user:test",
        "correlation_id": uuid.uuid4(),
        "payload": psycopg.types.json.Jsonb({}),
    } | overrides
    return _insert(conn, "event", row)


def _insert(conn, table: str, row: dict) -> uuid.UUID:
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, row)),
        sql.SQL(", ").join(sql.Placeholder() * len(row)),
    )
    return conn.execute(query, list(row.values())).fetchone()[0]


# --- Document vault ------------------------------------------------------------


@pytest.fixture
def blob_store(tmp_path):
    """A LocalBlobStore of its own per test, under pytest's tmp_path."""
    from hausbuch.storage import LocalBlobStore

    return LocalBlobStore(tmp_path / "blobs")
