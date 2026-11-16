"""Migration 0044 (docs/phases/phase-7.md D182 (3) amended, review of PR #387, 16 Nov 2026):
a `wallet_sync` run keeps its original without a new movement (a question card), only on a
succeeded run; the downgrade restores 0041's CHECK and is refused while a row needs the new
one. The CHECK itself is test_phase7_migration.py's. Synthetic data only."""

from datetime import UTC, datetime, timedelta

import pytest
from db.db_support import _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_migration import _as_owner, _document, _forced, _new_workspace
from db.test_phase7_migration import _address, _wallet
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0044_sync_original_for_questions"
PREVIOUS = "0043_exchange_trade_groups"


def _checks(conn) -> dict[str, str]:
    return dict(
        conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'wallet_sync'::regclass AND contype = 'c'"
            " AND conname IN ('wallet_sync_check3', 'wallet_sync_original_check')"
        ).fetchall()
    )


def test_0044_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert set(_checks(conn)) == {"wallet_sync_original_check"}
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            (old,) = _checks(conn).items()
            assert old == (
                "wallet_sync_check3",
                "CHECK (((import_id IS NULL) OR (movements_new > 0)))",
            )
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def _run_with_original(conn, ws, movements_new: int) -> None:
    wallet = _wallet(conn, ws)
    address = _address(conn, ws, wallet)
    document = _document(conn, ws, type="statement", subtype="chain_sync")
    imported = _insert(
        conn,
        "movement_import",
        {"workspace_id": ws, "account_id": wallet, "document_id": document, "format": "chain_sync"},
    )
    _insert(
        conn,
        "wallet_sync",
        {
            "workspace_id": ws,
            "account_id": wallet,
            "address_id": address,
            "source_host": "mempool.space",
            "status": "succeeded",
            "finished_at": datetime.now(UTC) + timedelta(hours=1),
            "movements_new": movements_new,
            "import_id": imported,
            "document_id": document,
        },
    )


def test_0044_downgrade_refuses_while_a_run_keeps_an_original_without_a_movement():
    """A downgrade never leaves a row the old CHECK refuses; the guard sees rows under forced
    RLS and restores it."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _run_with_original(conn, ws, movements_new=0)
            forced = _forced(conn)
        with pytest.raises(
            DBAPIError, match=f"downgrade of {HEAD} refused: a sync run keeps an original"
        ) as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
            assert "wallet_sync" in forced
    finally:
        drop_database(name)


def test_0044_downgrade_with_new_movements_only_restores_0041():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _run_with_original(conn, ws, movements_new=1)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert set(_checks(conn)) == {"wallet_sync_check3"}
    finally:
        drop_database(name)
