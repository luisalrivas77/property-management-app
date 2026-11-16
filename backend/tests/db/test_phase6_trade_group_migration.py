"""Migration 0043 (docs/phases/phase-6.md D178 amended (3)): a movement group without a chain
and a transaction hash, an exchange trade's (kind swap only); the downgrade guard. Synthetic
data only."""

import hashlib
import importlib.util
import os
import uuid

import pytest
from db.db_support import BACKEND, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_migration import _as_owner, _depot, _forced, _new_workspace
from psycopg import errors
from sqlalchemy.exc import DBAPIError


def _migration():
    path = os.path.join(BACKEND, "migrations", "versions", "0043_exchange_trade_groups.py")
    spec = importlib.util.spec_from_file_location("migration_0043", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()
HEAD = MIGRATION.revision
PREVIOUS = MIGRATION.down_revision


def _group(ws, account_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account_id,
        "chain": "ethereum",
        "tx_hash": "0x" + hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        "occurred_at": "2026-03-02T10:15:00Z",
        "kind": "swap",
    } | overrides


def _nullable(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns"
        " WHERE table_name = 'movement_group' AND column_name IN ('chain', 'tx_hash')"
    )
    return dict(rows.fetchall())


def _seed(name, **group) -> None:
    ws = _new_workspace(name)
    with _admin(name) as conn:
        _as_owner(conn, ws)
        account = _depot(conn, ws)
        _insert(conn, "movement_group", _group(ws, account, **group))


def test_0043_allows_a_chainless_swap_and_nothing_else_without_a_chain():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            assert _nullable(conn) == {"chain": "YES", "tx_hash": "YES"}
            _as_owner(conn, ws)
            account = _depot(conn, ws)
            _insert(conn, "movement_group", _group(ws, account, chain=None, tx_hash=None))
            _insert(conn, "movement_group", _group(ws, account))  # a chain's, as in 0041
            for refused in (
                {"chain": None, "tx_hash": None, "kind": "transfer"},  # only a swap
                {"tx_hash": None},  # a chain names its transaction
                {"chain": None},  # a transaction names its chain
                {"chain": "dogecoin"},  # 0041's chain CHECK still holds
            ):
                with pytest.raises(errors.CheckViolation), conn.transaction():
                    _insert(conn, "movement_group", _group(ws, account, **refused))
    finally:
        drop_database(name)


def test_0043_runs_up_down_and_up_with_chain_groups():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        _seed(name)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert _nullable(conn) == {"chain": "NO", "tx_hash": "NO"}
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0043_downgrade_refuses_while_an_exchange_trade_group_exists():
    """A downgrade never drops data; the guard sees rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        _seed(name, chain=None, tx_hash=None)
        with _admin(name) as conn:
            forced = _forced(conn)
        assert "movement_group" in forced
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
            assert _nullable(conn) == {"chain": "YES", "tx_hash": "YES"}
    finally:
        drop_database(name)
