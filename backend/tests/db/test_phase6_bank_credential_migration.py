"""Migration 0039 (docs/phases/phase-6.md D196): `bank_credential` (one encrypted PIN per
workspace and slot, replaced in place and deleted by a person), the encrypted login columns of
`bank_connection` with the CHECK that keeps exactly one form of the login, the grants and the
downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py, the
round trip there too. Synthetic bytes only: the database never sees a key or a readable PIN."""

import hashlib
import os
import uuid

import pytest
from db.db_support import APP, OWNER, _admin, _insert, alembic, create_database, drop_database
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0039_bank_credential"
PREVIOUS = "0038_no_pay_marker"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _credential(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "credential_slot": "primary",
        "nonce": os.urandom(12),
        "ciphertext": os.urandom(21),
        "key_version": 1,
    } | overrides


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


def _connection(conn, ws, **login) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "account_id": _account(conn, ws),
        "bank_code": "12345678",
        "fints_url": "https://banking.example/fints",
    } | (login or {"login_name": "anmeldename42"})
    return _insert(conn, "bank_connection", row)


def _sealed_login() -> dict:
    return {
        "login_nonce": os.urandom(12),
        "login_ciphertext": os.urandom(30),
        "login_key_version": 1,
    }


@pytest.fixture
def app_conn(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        yield conn, a


def test_app_role_may_read_write_and_delete_but_not_truncate(connect):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute(
                "SELECT has_table_privilege(%s, 'bank_credential', %s)", (APP, p)
            ).fetchone()[0]
        }
    assert granted == {"SELECT", "INSERT", "UPDATE", "DELETE"}


def test_no_column_holds_a_readable_pin_or_a_key(connect):
    with connect(OWNER) as conn:
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = 'bank_credential'"
            )
        }
    assert columns == {
        "id",
        "workspace_id",
        "credential_slot",
        "nonce",
        "ciphertext",
        "key_version",
        "set_by",
        "created_at",
        "updated_at",
    }


def test_one_pin_per_slot_replaced_in_place_and_deleted(app_conn):
    conn, a = app_conn
    row = _insert(conn, "bank_credential", _credential(a))
    _insert(conn, "bank_credential", _credential(a, credential_slot="owner_b"))
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "bank_credential", _credential(a))
    conn.execute(
        "UPDATE bank_credential SET nonce = %s, ciphertext = %s WHERE id = %s",
        (os.urandom(12), os.urandom(25), row),
    )
    conn.execute("DELETE FROM bank_credential WHERE id = %s", (row,))
    assert conn.execute("SELECT count(*) FROM bank_credential").fetchone() == (1,)


@pytest.mark.parametrize(
    "change",
    [
        {"credential_slot": "Owner_B"},
        {"credential_slot": "owner-b"},
        {"credential_slot": "a" * 33},
        {"nonce": os.urandom(16)},
        {"ciphertext": os.urandom(16)},
        {"key_version": 0},
    ],
    ids=["upper_case", "hyphen", "too_long", "nonce_not_96_bits", "no_room_for_tag", "version_0"],
)
def test_a_credential_outside_its_checks_is_refused(app_conn, change):
    conn, a = app_conn
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "bank_credential", _credential(a, **change))


def test_a_pin_is_invisible_to_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        _insert(conn, "bank_credential", _credential(a))
        assert conn.execute("SELECT count(*) FROM bank_credential").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute("SELECT count(*) FROM bank_credential").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _insert(conn, "bank_credential", _credential(a, credential_slot="dkb"))


def test_a_connection_keeps_its_login_readable_until_the_app_encrypts_it(app_conn):
    conn, a = app_conn
    plain = _connection(conn, a)
    sealed = _connection(conn, a, **_sealed_login())
    conn.execute(
        "UPDATE bank_connection SET login_name = NULL, login_nonce = %(login_nonce)s,"
        " login_ciphertext = %(login_ciphertext)s, login_key_version = %(login_key_version)s"
        " WHERE id = %(id)s",
        _sealed_login() | {"id": plain},
    )
    rows = conn.execute(
        "SELECT login_name IS NULL FROM bank_connection WHERE id IN (%s, %s)", (plain, sealed)
    ).fetchall()
    assert rows == [(True,), (True,)]


@pytest.mark.parametrize(
    "login",
    [
        {},  # neither form
        {"login_name": "anmeldename42", **_sealed_login()},  # both forms
        {"login_name": None, "login_nonce": os.urandom(12), "login_ciphertext": os.urandom(30)},
        {"login_nonce": os.urandom(11), "login_ciphertext": os.urandom(30), "login_key_version": 1},
    ],
    ids=["neither", "both", "no_key_version", "short_nonce"],
)
def test_a_login_is_stored_in_exactly_one_form(app_conn, login):
    conn, a = app_conn
    row = {
        "workspace_id": a,
        "account_id": _account(conn, a),
        "bank_code": "12345678",
        "fints_url": "https://banking.example/fints",
    } | login
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "bank_connection", row)


# --- the downgrade -------------------------------------------------------------------------


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


@pytest.mark.parametrize("data", ["pin", "encrypted_login"])
def test_0039_downgrade_refuses_while_it_would_drop_data(data):
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            if data == "pin":
                _insert(conn, "bank_credential", _credential(ws))
            else:
                _connection(conn, ws, **_sealed_login())
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            for table in ("bank_credential", "bank_connection"):
                forced = conn.execute(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
                ).fetchone()
                assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0039_downgrade_with_readable_logins_restores_0038():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _connection(conn, ws)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename = 'bank_credential'"
            ).fetchone() == (0,)
            nullable = conn.execute(
                "SELECT is_nullable FROM information_schema.columns"
                " WHERE table_name = 'bank_connection' AND column_name = 'login_name'"
            ).fetchone()
            assert nullable == ("NO",)
            gone = conn.execute(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'bank_connection' AND column_name LIKE 'login\\_%%'"
                " AND column_name <> 'login_name'"
            ).fetchone()
            assert gone == (0,)
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
