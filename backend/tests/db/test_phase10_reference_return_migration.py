"""Migration 0057 (docs/phases/phase-10.md §7.11 (c) 3, D310 amended, „Route 3 as decided for
the build“ point 8): the insert-only table `reference_return`, its CHECKs, the grants, workspace
isolation and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py, the
round trip there too, and the three data-version triggers by test_data_version.py. Invented
values only, never a published figure."""

import uuid

import pytest
from db.db_support import APP, OWNER, _admin, _insert, alembic, create_database, drop_database
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0057_reference_return"
PREVIOUS = "0056_spending_mark"
TABLE = "reference_return"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _row(ws, download=None, year=2001, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "download_id": download or uuid.uuid4(),
        "series": "french_developed",
        "year": year,
        "market_excess_pct": "10.0000",
        "risk_free_pct": "2.0000",
        "currency": "USD",
        "source": "Beispielquelle",
        "url": "https://example.invalid/file.zip",
        "series_key": "Annual Factors: January-December, Mkt-RF + RF",
        "read_on": "2026-11-25",
        "licence": "Eigene Nutzung",
        "attribution": "Beispiel",
    } | overrides


@pytest.fixture
def app_conn(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        yield conn, a


def test_app_role_may_only_read_and_insert(connect):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, TABLE, p)).fetchone()[0]
        }
    assert granted == {"SELECT", "INSERT"}


def test_a_download_is_one_row_per_year_and_the_flags_default_false(app_conn):
    conn, a = app_conn
    download = uuid.uuid4()
    for year in (2000, 2001, 2002):
        _insert(conn, TABLE, _row(a, download, year))
    rows = conn.execute(
        f"SELECT count(*), bool_or(repo_allowed), bool_or(commercial_use), count(DISTINCT"
        f" fetched_at) FROM {TABLE}"
    ).fetchone()
    assert rows == (3, False, False, 1)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, TABLE, _row(a, download, 2001))
    _insert(conn, TABLE, _row(a, uuid.uuid4(), 2001))  # the same year in another download


@pytest.mark.parametrize(
    "overrides",
    [
        {"series": "msci_world"},
        {"currency": "EUR"},
        {"repo_allowed": True},
        {"commercial_use": True},
        {"year": 1800},
        {"market_excess_pct": "-250"},
        {"risk_free_pct": "150"},
        {"source": ""},
        {"attribution": ""},
    ],
)
def test_a_row_is_refused_outside_its_checks(app_conn, overrides):
    conn, a = app_conn
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, TABLE, _row(a, **overrides))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE {t} SET year = 1999 WHERE id = %s", "DELETE FROM {t} WHERE id = %s"],
)
def test_rows_are_insert_only_even_for_the_owner(connect, workspaces, statement):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        row = _insert(conn, TABLE, _row(a))
        with pytest.raises(errors.RestrictViolation, match="append-only"):
            conn.execute(statement.format(t=TABLE), (row,))


def test_the_table_cannot_be_truncated(connect, workspaces):
    with connect(OWNER, workspaces[0]) as conn, pytest.raises(errors.RestrictViolation):
        conn.execute(f"TRUNCATE {TABLE}")


def test_a_download_is_invisible_to_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        _insert(conn, TABLE, _row(a))
        assert conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _insert(conn, TABLE, _row(a))


# --- the downgrade -------------------------------------------------------------------------


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def test_0057_downgrade_refuses_while_a_download_is_stored():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            conn.execute(f"SET ROLE {OWNER}")
            conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))
            _insert(conn, TABLE, _row(ws))
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            forced = conn.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (TABLE,)
            ).fetchone()
            assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0057_downgrade_without_data_drops_the_table():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename = %s", (TABLE,)
            ).fetchone() == (0,)
    finally:
        drop_database(name)
