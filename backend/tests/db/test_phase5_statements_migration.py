"""Migration 0024 (docs/phases/phase-5.md D154): `bank_statement` (append-only, one row per
account, year and number, the sync of the same account), `bank_connection.fetch_statements`
(default off), the grants and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py, the
round trip there too. Synthetic data only."""

import hashlib
import uuid

import pytest
from db.db_support import APP, OWNER, _admin, _insert, alembic, create_database, drop_database
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0024_bank_statements"
PREVIOUS = "0023_payment_proof"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _document(conn, ws) -> uuid.UUID:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/pdf', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    row = {"workspace_id": ws, "file_sha256": digest, "source": "bank_sync", "type": "statement"}
    return _insert(conn, "document", row)


def _account(conn, ws) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "label": "Mietkonto",
        "bank": "Testbank",
        "iban_masked": "DE00 **** 0000",
        "iban_hmac": hashlib.sha256(uuid.uuid4().bytes).digest(),
        "opening_balance": "1000.00",
        "opening_balance_on": "2025-12-31",
    }
    return _insert(conn, "account", row)


def _connection(conn, ws, account) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "account_id": account,
        "bank_code": "12345678",
        "fints_url": "https://banking.example/fints",
        "login_name": "anmeldename42",
    }
    return _insert(conn, "bank_connection", row)


def _seed(conn, ws) -> dict:
    account = _account(conn, ws)
    connection = _connection(conn, ws, account)
    sync = _insert(
        conn,
        "bank_sync",
        {
            "workspace_id": ws,
            "account_id": account,
            "connection_id": connection,
            "trigger": "manual",
        },
    )
    return {"account": account, "connection": connection, "sync": sync}


def _statement(ws, ids, document, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": ids["account"],
        "year": 2026,
        "number": 1,
        "document_id": document,
        "sync_id": ids["sync"],
    } | overrides


@pytest.fixture
def scope(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        yield conn, a, _seed(conn, a)


def test_app_role_may_only_read_and_insert(connect):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute(
                "SELECT has_table_privilege(%s, 'bank_statement', %s)", (APP, p)
            ).fetchone()[0]
        }
    assert granted == {"SELECT", "INSERT"}


def test_no_column_holds_the_pdf_or_the_receipt(connect):
    with connect(OWNER) as conn:
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = 'bank_statement'"
            )
        }
    assert columns == {
        "id",
        "workspace_id",
        "account_id",
        "year",
        "number",
        "document_id",
        "sync_id",
        "fetched_at",
    }


def test_a_statement_is_stored_once_per_account_year_and_number(scope):
    conn, a, ids = scope
    _insert(conn, "bank_statement", _statement(a, ids, _document(conn, a)))
    _insert(conn, "bank_statement", _statement(a, ids, _document(conn, a), number=2))
    _insert(conn, "bank_statement", _statement(a, ids, _document(conn, a), year=2027))
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "bank_statement", _statement(a, ids, _document(conn, a)))


@pytest.mark.parametrize("change", [{"number": 0}, {"number": 100000}, {"year": 999}])
def test_a_statement_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "bank_statement", _statement(a, ids, _document(conn, a), **change))


def test_the_sync_is_the_same_accounts(scope):
    conn, a, ids = scope
    other = _seed(conn, a)
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(
            conn, "bank_statement", _statement(a, ids, _document(conn, a), sync_id=other["sync"])
        )


def test_a_statement_is_never_changed_or_removed_even_by_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        ids = _seed(conn, a)
        row = _insert(conn, "bank_statement", _statement(a, ids, _document(conn, a)))
        for statement in (
            "UPDATE bank_statement SET number = 2 WHERE id = %s",
            "DELETE FROM bank_statement WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
                conn.execute(statement, (row,))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("TRUNCATE bank_statement")


def test_fetch_statements_starts_off_and_the_app_may_switch_it(scope):
    conn, _, ids = scope
    row = conn.execute(
        "SELECT fetch_statements FROM bank_connection WHERE id = %s", (ids["connection"],)
    ).fetchone()
    assert row == (False,)
    conn.execute(
        "UPDATE bank_connection SET fetch_statements = true WHERE id = %s", (ids["connection"],)
    )


def test_a_statement_is_invisible_to_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        ids = _seed(conn, a)
        _insert(conn, "bank_statement", _statement(a, ids, _document(conn, a)))
        assert conn.execute("SELECT count(*) FROM bank_statement").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute("SELECT count(*) FROM bank_statement").fetchone() == (0,)


# --- the downgrade -------------------------------------------------------------------------


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


@pytest.mark.parametrize("data", ["statement", "switch"])
def test_0024_downgrade_refuses_while_it_would_drop_data(data):
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            ids = _seed(conn, ws)
            if data == "statement":
                _insert(conn, "bank_statement", _statement(ws, ids, _document(conn, ws)))
            else:
                conn.execute("UPDATE bank_connection SET fetch_statements = true")
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            for table in ("bank_statement", "bank_connection"):
                forced = conn.execute(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
                ).fetchone()
                assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0024_downgrade_with_the_switch_off_drops_the_table_and_the_column():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _seed(conn, ws)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename = 'bank_statement'"
            ).fetchone() == (0,)
            column = conn.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'bank_connection' AND column_name = 'fetch_statements'"
            ).fetchone()
            assert column is None
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
