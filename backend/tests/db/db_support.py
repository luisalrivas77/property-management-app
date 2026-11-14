"""Database helpers shared by the test suites (imported as `db.db_support`; tests/ is on
sys.path). The fixtures built on them stay in tests/db/conftest.py.

Kept out of conftest.py: tests/api, tests/auth and tests/db each have a conftest.py, and all
three are importable as `conftest`, so `from conftest import ...` picks whichever was loaded
last (docs/phases/phase-1-step-3.md §10.7).

Needs HAUSBUCH_TEST_ADMIN_URL: a superuser libpq URL, e.g.
postgresql://postgres:postgres@localhost:5432/postgres.
"""

import os
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, pool, text

ADMIN_URL = os.environ.get("HAUSBUCH_TEST_ADMIN_URL")
OWNER, APP = "hausbuch_owner", "hausbuch_app"
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEST_IBAN_KEY = bytes(range(32))  # test key only; production keys come from the secrets manager


def _admin(dbname: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(ADMIN_URL, autocommit=True, **({"dbname": dbname} if dbname else {}))
    return conn


# Arbitrary key of the session-level advisory lock that serialises role setup (below).
_ROLE_SETUP_LOCK = 7_310_420_016


def create_database() -> str:
    name = f"hausbuch_test_{uuid.uuid4().hex[:10]}"
    with _admin() as conn:
        # Roles are cluster-wide, so parallel pytest-xdist workers would race on CREATE ROLE
        # (the check-then-create is not atomic). The advisory lock serialises the check; it is
        # taken on the admin database, which every worker connects to, and released at once.
        conn.execute("SELECT pg_advisory_lock(%s)", (_ROLE_SETUP_LOCK,))
        try:
            for role in (OWNER, APP):
                exists = conn.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
                ).fetchone()
                if not exists:
                    conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_ROLE_SETUP_LOCK,))
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
