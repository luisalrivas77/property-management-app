"""Migration 0052 (docs/phases/phase-6.md D292): a movement read from a depot cash statement
line (`depot_statement`) names its line and its document, one live such movement per line, and
the downgrade guard. Synthetic data only."""

import hashlib
import importlib.util
import os
import uuid

import pytest
from db.db_support import BACKEND, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_migration import (
    _as_owner,
    _asset,
    _depot,
    _document,
    _forced,
    _import,
    _movement,
    _new_workspace,
)
from psycopg import errors
from sqlalchemy.exc import DBAPIError


def _migration():
    path = os.path.join(BACKEND, "migrations", "versions", "0052_depot_statement_trades.py")
    spec = importlib.util.spec_from_file_location("migration_0052", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()
HEAD = MIGRATION.revision
PREVIOUS = MIGRATION.down_revision


def _line(conn, ws, account_id) -> tuple[uuid.UUID, uuid.UUID]:
    """A purchase line of an imported depot statement and the statement's document."""
    document_id = _document(conn, ws)
    import_id = _import(conn, ws, account_id, document_id=document_id)
    line = _insert(
        conn,
        "transaction",
        {
            "workspace_id": ws,
            "account_id": account_id,
            "import_id": import_id,
            "line_hash": hashlib.sha256(uuid.uuid4().bytes).digest(),
            "booking_date": "2026-05-06",
            "amount": "-150.00",
            "purpose": "Testfonds A",
        },
    )
    return line, document_id


def _statement_movement(ws, account_id, asset_id, line, document_id, **overrides) -> dict:
    return _movement(
        ws,
        account_id,
        asset_id,
        source="depot_statement",
        status="confirmed",
        transaction_id=line,
        document_id=document_id,
        **overrides,
    )


def _seed(name) -> None:
    ws = _new_workspace(name)
    with _admin(name) as conn:
        _as_owner(conn, ws)
        depot = _depot(conn, ws)
        fund = _asset(conn, ws, kind="etf", symbol=None, isin="XX0000000001", name="Testfonds A")
        line, document_id = _line(conn, ws, depot)
        _insert(conn, "asset_movement", _statement_movement(ws, depot, fund, line, document_id))


def test_0052_a_statement_movement_names_its_line_and_is_one_per_line():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            depot = _depot(conn, ws)
            fund = _asset(conn, ws, kind="etf", symbol=None, isin="XX0000000001", name="Fonds")
            line, document_id = _line(conn, ws, depot)
            for refused in ({"transaction_id": None}, {"document_id": None}):
                with pytest.raises(errors.CheckViolation), conn.transaction():
                    _insert(
                        conn,
                        "asset_movement",
                        _statement_movement(ws, depot, fund, line, document_id) | refused,
                    )
            first = _insert(
                conn, "asset_movement", _statement_movement(ws, depot, fund, line, document_id)
            )
            with pytest.raises(errors.UniqueViolation), conn.transaction():
                _insert(
                    conn, "asset_movement", _statement_movement(ws, depot, fund, line, document_id)
                )
            # A trade confirmation that supersedes it keeps the line linked (D292 (2)).
            successor = _insert(
                conn,
                "asset_movement",
                _movement(
                    ws,
                    depot,
                    fund,
                    source="trade_confirmation",
                    status="confirmed",
                    transaction_id=line,
                    document_id=_document(conn, ws),
                ),
            )
            conn.execute(
                "UPDATE asset_movement SET superseded_by = %s WHERE id = %s", (successor, first)
            )
            # Once superseded, the line may carry a new statement movement again.
            _insert(conn, "asset_movement", _statement_movement(ws, depot, fund, line, document_id))
    finally:
        drop_database(name)


def test_0052_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        alembic(name, "downgrade", PREVIOUS)
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0052_downgrade_refuses_while_a_statement_movement_exists():
    """A downgrade never drops data; the guard sees rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        _seed(name)
        with _admin(name) as conn:
            forced = _forced(conn)
        assert "asset_movement" in forced
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
    finally:
        drop_database(name)
