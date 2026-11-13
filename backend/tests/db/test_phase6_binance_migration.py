"""Migration 0028 (docs/phases/phase-6.md D166 as amended, D178): movement_import.format
accepts `binance_csv`, the downgrade restores 0025's CHECK, and it is refused while a
Binance import exists. The enum CHECK against MovementImportFormat is
test_schema_consistency.py's. Synthetic data only."""

import pytest
from db.db_support import OWNER, _admin, _insert, alembic, create_database, drop_database
from db.test_phase2_catalogue_migration import _document
from db.test_phase6_migration import _account, _as_owner, _forced, _new_workspace
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0028_binance_csv"
PREVIOUS = "0027_depot"


def _exchange(conn, ws):
    return _account(
        conn,
        ws,
        label="Binance",
        bank="Binance",
        kind="exchange",
        provider="Binance",
        iban_masked=None,
        iban_hmac=None,
    )


def _movement_import(conn, ws, account_id, fmt: str):
    row = {
        "workspace_id": ws,
        "account_id": account_id,
        "document_id": _document(conn, ws),
        "format": fmt,
    }
    return _insert(conn, "movement_import", row)


def test_a_binance_import_is_accepted_and_no_other_new_format(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        exchange = _exchange(conn, a)
        assert _movement_import(conn, a, exchange, "binance_csv") is not None
        assert _movement_import(conn, a, exchange, "bison_csv") is not None
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _movement_import(conn, a, exchange, "binance_spot_csv")


def _format_check(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'movement_import_format_check'"
    ).fetchone()[0]


def test_0028_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert "binance_csv" in _format_check(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            check = _format_check(conn)
            assert "bison_csv" in check and "binance_csv" not in check
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0028_downgrade_refuses_while_a_binance_import_exists():
    """A downgrade never leaves a row the old CHECK refuses; the guard sees rows under forced
    RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _movement_import(conn, ws, _exchange(conn, ws), "binance_csv")
            forced = _forced(conn)
        reason = "an import is of format binance_csv"
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused: {reason}") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
    finally:
        drop_database(name)
