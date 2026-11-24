"""Migration 0056 (docs/phases/phase-10.md §7.9 (f), D295 (2)): the append-only table
`spending_mark`, its target CHECK, the grants, workspace isolation, the data-version triggers,
the event entity type `household` and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py, the
round trip there too, and the three data-version triggers by test_data_version.py. Invented
values only."""

import hashlib
import uuid

import pytest
from db.db_support import (
    APP,
    OWNER,
    _admin,
    _insert,
    alembic,
    create_database,
    drop_database,
    insert_event,
)
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0056_spending_mark"
PREVIOUS = "0055_sign_out_keeps_data_version"
TABLE = "spending_mark"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _allocation(conn, ws) -> uuid.UUID:
    """One allocation on a private account, as the household's lines are stored."""
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'text/csv', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    doc = _insert(conn, "document", {"workspace_id": ws, "file_sha256": digest, "source": "upload"})
    account = _insert(
        conn,
        "account",
        {
            "workspace_id": ws,
            "label": "Girokonto",
            "bank": "Beispielbank",
            "iban_masked": "DE00 **** 0001",
            "iban_hmac": hashlib.sha256(uuid.uuid4().bytes).digest(),
            "opening_balance": "0.00",
            "opening_balance_on": "2026-01-01",
        },
    )
    imp = _insert(
        conn,
        "statement_import",
        {"workspace_id": ws, "account_id": account, "document_id": doc, "format": "csv_camt"},
    )
    tx = _insert(
        conn,
        "transaction",
        {
            "workspace_id": ws,
            "account_id": account,
            "import_id": imp,
            "line_hash": hashlib.sha256(uuid.uuid4().bytes).digest(),
            "booking_date": "2026-10-12",
            "amount": "-870.00",
            "purpose": "Inspektion",
        },
    )
    return _insert(
        conn,
        "allocation",
        {
            "workspace_id": ws,
            "transaction_id": tx,
            "amount": "-870.00",
            "category": "hh_car_service",
            "method": "user",
            "confidence": "1.000",
        },
    )


def _payment_mark(ws, allocation, answer="one_off") -> dict:
    return {"workspace_id": ws, "allocation_id": allocation, "answer": answer}


def _series_mark(ws, answer="hidden", **overrides) -> dict:
    return {
        "workspace_id": ws,
        "series_key": "STREAMING MUSTER",
        "series_interval": "monthly",
        "answer": answer,
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


def test_a_payment_and_a_series_are_answered(app_conn):
    conn, a = app_conn
    allocation = _allocation(conn, a)
    for answer in ("one_off", "regular"):
        _insert(conn, TABLE, _payment_mark(a, allocation, answer))
    for answer in ("hidden", "shown"):
        _insert(conn, TABLE, _series_mark(a, answer))
    for interval in ("quarterly", "yearly"):
        _insert(conn, TABLE, _series_mark(a, series_interval=interval))
    assert conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (6,)


@pytest.mark.parametrize(
    "row",
    [
        # a payment answered as a series, and the other way round
        lambda a, al: _payment_mark(a, al, "hidden"),
        lambda a, al: _series_mark(a, "one_off"),
        # both targets, or none
        lambda a, al: _series_mark(a) | {"allocation_id": al},
        lambda a, al: {"workspace_id": a, "answer": "one_off"},
        # a series needs its key and its interval
        lambda a, al: _series_mark(a, series_interval=None),
        lambda a, al: _series_mark(a, series_key=None),
        lambda a, al: _series_mark(a, series_key=""),
        lambda a, al: _series_mark(a, series_interval="weekly"),
        lambda a, al: _payment_mark(a, al, "maybe"),
    ],
)
def test_a_mark_names_one_target_and_an_answer_it_takes(app_conn, row):
    conn, a = app_conn
    allocation = _allocation(conn, a)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, TABLE, row(a, allocation))


def test_a_payment_mark_names_an_allocation_of_its_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, b) as other:
        foreign = _allocation(other, b)
        other.commit()
    with connect(APP, a) as conn:
        for allocation in (uuid.uuid4(), foreign):
            with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
                _insert(conn, TABLE, _payment_mark(a, allocation))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE {t} SET answer = 'regular' WHERE id = %s", "DELETE FROM {t} WHERE id = %s"],
)
def test_marks_are_append_only_even_for_the_owner(connect, workspaces, statement):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        row = _insert(conn, TABLE, _payment_mark(a, _allocation(conn, a)))
        with pytest.raises(errors.RestrictViolation, match="append-only"):
            conn.execute(statement.format(t=TABLE), (row,))


def test_the_table_cannot_be_truncated(connect, workspaces):
    with connect(OWNER, workspaces[0]) as conn, pytest.raises(errors.RestrictViolation):
        conn.execute(f"TRUNCATE {TABLE}")


def test_a_mark_is_invisible_to_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        _insert(conn, TABLE, _series_mark(a))
        assert conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _insert(conn, TABLE, _series_mark(a))


def test_events_take_the_household_entity_type(app_conn):
    conn, a = app_conn
    insert_event(conn, a, type="household.mark_recorded", entity_type="household")


# --- the downgrade -------------------------------------------------------------------------


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


def _refused(name) -> None:
    with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
        alembic(name, "downgrade", PREVIOUS)
    assert isinstance(refused.value.orig, errors.RestrictViolation)
    with _admin(name) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        for table in (TABLE, "event"):
            forced = conn.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
            ).fetchone()
            assert forced == (True,), table  # the refused downgrade's NO FORCE was rolled back


def test_0056_downgrade_refuses_while_a_mark_is_stored():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _insert(conn, TABLE, _series_mark(ws))
        _refused(name)
    finally:
        drop_database(name)


def test_0056_downgrade_refuses_while_an_event_names_household():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            insert_event(conn, ws, type="household.mark_recorded", entity_type="household")
        _refused(name)
    finally:
        drop_database(name)


def test_0056_downgrade_without_data_drops_the_table_and_the_entity_type():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename = %s", (TABLE,)
            ).fetchone() == (0,)
            _as_owner(conn, ws)
            with pytest.raises(errors.CheckViolation):
                insert_event(conn, ws, type="household.mark_recorded", entity_type="household")
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)
