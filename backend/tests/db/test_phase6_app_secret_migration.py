"""Migration 0045 (docs/phases/phase-6.md D260): `app_secret` (one encrypted provider key or
URL per workspace and allowlisted name, replaced in place and deleted by a person), its
CHECKs, the grants and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py, the
round trip there too. Synthetic bytes only: the database never sees a key or a readable value."""

import os
import uuid

import pytest
from db.db_support import APP, OWNER, _admin, _insert, alembic, create_database, drop_database
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0045_app_secret"
PREVIOUS = "0044_sync_original_for_questions"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
NAMES = (
    "anthropic_api_key",
    "etherscan_key",
    "solana_rpc_url",
    "bsc_rpc_url",
    "helius_key",
    "coingecko_key",
)


def _secret(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "name": "etherscan_key",
        "nonce": os.urandom(12),
        "ciphertext": os.urandom(40),
        "key_version": 1,
    } | overrides


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
                "SELECT has_table_privilege(%s, 'app_secret', %s)", (APP, p)
            ).fetchone()[0]
        }
    assert granted == {"SELECT", "INSERT", "UPDATE", "DELETE"}


def test_no_column_holds_a_readable_value(connect):
    with connect(OWNER) as conn:
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'app_secret'"
            )
        }
    assert columns == {
        "id",
        "workspace_id",
        "name",
        "nonce",
        "ciphertext",
        "key_version",
        "set_by",
        "created_at",
        "updated_at",
    }


def test_every_allowlisted_name_once_per_workspace(app_conn):
    conn, a = app_conn
    for name in NAMES:
        _insert(conn, "app_secret", _secret(a, name=name))
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "app_secret", _secret(a))
    conn.execute("DELETE FROM app_secret WHERE name = 'etherscan_key'")
    assert conn.execute("SELECT count(*) FROM app_secret").fetchone() == (len(NAMES) - 1,)


@pytest.mark.parametrize(
    "change",
    [
        {"name": "database_url"},
        {"name": "session_key"},
        {"name": "fints_product_id"},
        {"name": "Etherscan_Key"},
        {"nonce": os.urandom(16)},
        {"ciphertext": os.urandom(16)},
        {"ciphertext": os.urandom(529)},
        {"key_version": 0},
    ],
    ids=[
        "database_url",
        "session_key",
        "product_id",
        "upper_case",
        "nonce_not_96_bits",
        "no_room_for_tag",
        "longer_than_512",
        "version_0",
    ],
)
def test_a_secret_outside_its_checks_is_refused(app_conn, change):
    conn, a = app_conn
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "app_secret", _secret(a, **change))


def test_a_secret_is_invisible_to_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        _insert(conn, "app_secret", _secret(a, name="coingecko_key"))
        assert conn.execute("SELECT count(*) FROM app_secret").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute("SELECT count(*) FROM app_secret").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _insert(conn, "app_secret", _secret(a, name="helius_key"))


# --- the downgrade -------------------------------------------------------------------------


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


def test_0045_downgrade_refuses_while_a_key_is_stored():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _insert(conn, "app_secret", _secret(ws))
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            forced = conn.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'app_secret'::regclass"
            ).fetchone()
            assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0045_downgrade_without_keys_drops_the_table():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename = 'app_secret'"
            ).fetchone() == (0,)
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)
