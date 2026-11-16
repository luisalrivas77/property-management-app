"""Migration 0047 (docs/phases/phase-6.md D260 amended (10)): `elster_hersteller_id` in
app_secret's name CHECK, and the downgrade that deletes such rows and restores 0045's CHECK.
The CHECK against AppSecretName is test_schema_consistency.py's. Synthetic bytes only: the
database never sees a readable value."""

import os

import pytest
from db.db_support import APP, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_migration import _as_owner, _forced, _new_workspace
from psycopg import errors

HEAD = "0047_elster_hersteller_id"
PREVIOUS = "0046_learned_exchange_transfers"
NAME = "elster_hersteller_id"


def _secret(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "name": NAME,
        "nonce": os.urandom(12),
        "ciphertext": os.urandom(21),
        "key_version": 1,
    } | overrides


def test_the_id_is_accepted_once_per_workspace(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        _insert(conn, "app_secret", _secret(a))
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _insert(conn, "app_secret", _secret(a))


def _check(conn) -> str:
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'app_secret_name_check'"
    ).fetchone()
    return definition


def test_0047_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert NAME in _check(conn) and "inbox_label" in _check(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert NAME not in _check(conn) and "inbox_label" in _check(conn)
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0047_downgrade_deletes_the_id_only_and_keeps_rls_forced():
    """The delete sees the row under forced RLS (the owner has no workspace set), leaves every
    other key, and the old CHECK then refuses the name again."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _insert(conn, "app_secret", _secret(ws))
            _insert(conn, "app_secret", _secret(ws, name="etherscan_key"))
            forced = _forced(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert _forced(conn) == forced and "app_secret" in forced
            _as_owner(conn, ws)
            names = conn.execute("SELECT name FROM app_secret").fetchall()
            assert names == [("etherscan_key",)]
            with pytest.raises(errors.CheckViolation), conn.transaction():
                _insert(conn, "app_secret", _secret(ws))
    finally:
        drop_database(name)
