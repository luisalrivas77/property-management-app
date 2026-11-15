"""Migration 0040 (docs/phases/phase-6.md D167 (5) amended, 15 Nov 2026): a month-end row
values exactly one of an account, a property or a loan, so a stored run holds the property's
estimate and the loans' balances too; the downgrade guard. Synthetic data only."""

import importlib.util
import os
import uuid

import pytest
from db.db_support import (
    APP,
    BACKEND,
    _admin,
    _insert,
    alembic,
    create_database,
    drop_database,
    insert_property,
)
from db.test_phase3_migration import _loan
from db.test_phase6_migration import _account, _as_owner, _forced, _new_workspace
from psycopg import errors
from sqlalchemy.exc import DBAPIError


def _migration():
    path = os.path.join(BACKEND, "migrations", "versions", "0040_valuation_snapshot_scope.py")
    spec = importlib.util.spec_from_file_location("migration_0040", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()
HEAD = MIGRATION.revision
PREVIOUS = MIGRATION.down_revision


RUN = uuid.uuid4()


def _row(ws, **scope) -> dict:
    return {
        "workspace_id": ws,
        "as_of": "2026-10-31",
        "value_eur": "1000.00",
        "net_worth_run_id": RUN,
    } | scope


def test_a_row_values_exactly_one_account_property_or_loan(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        account = _account(conn, a)
        prop = insert_property(conn, a)
        loan = _loan(conn, a, prop)
        _insert(conn, "valuation_snapshot", _row(a, account_id=account))
        _insert(conn, "valuation_snapshot", _row(a, property_id=prop, value_eur="320000.00"))
        _insert(conn, "valuation_snapshot", _row(a, loan_id=loan, value_eur="-240000.00"))
        # A second property row of the same run is refused by the run's key.
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _insert(conn, "valuation_snapshot", _row(a, property_id=prop))
        for scope in (
            {},  # none
            {"account_id": account, "property_id": prop},
            {"property_id": prop, "loan_id": loan},
            {"property_id": prop, "asset_id": None, "account_id": account, "loan_id": loan},
        ):
            with pytest.raises(errors.CheckViolation), conn.transaction():
                _insert(
                    conn,
                    "valuation_snapshot",
                    _row(a, **scope) | {"net_worth_run_id": uuid.uuid4()},
                )
        # Still append-only for the app role.
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE valuation_snapshot SET value_eur = 1")


def _definitions(conn) -> dict[str, str]:
    return dict(
        conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'valuation_snapshot'::regclass"
        ).fetchall()
    )


def _account_nullable(conn) -> str:
    return conn.execute(
        "SELECT is_nullable FROM information_schema.columns"
        " WHERE table_name = 'valuation_snapshot' AND column_name = 'account_id'"
    ).fetchone()[0]


def test_0040_runs_up_down_and_up_and_keeps_account_rows():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            names = _definitions(conn)
            assert {MIGRATION.ONE_SCOPE, MIGRATION.ASSET_ON_ACCOUNT, MIGRATION.NEW_KEY} <= set(
                names
            )
            assert MIGRATION.OLD_KEY not in names and _account_nullable(conn) == "YES"
            triggers = {
                r[0]
                for r in conn.execute(
                    "SELECT tgname FROM pg_trigger"
                    " WHERE tgrelid = 'valuation_snapshot'::regclass AND NOT tgisinternal"
                )
            }
            assert triggers == {"valuation_snapshot_append_only", "valuation_snapshot_no_truncate"}
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _insert(conn, "valuation_snapshot", _row(ws, account_id=_account(conn, ws)))
        # Only account rows: the downgrade keeps them under 0025's key and NOT NULL.
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            names = _definitions(conn)
            assert MIGRATION.OLD_KEY in names and MIGRATION.ONE_SCOPE not in names
            assert _account_nullable(conn) == "NO"
            assert conn.execute("SELECT count(*) FROM valuation_snapshot").fetchone()[0] == 1
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0040_downgrade_refuses_while_a_property_or_loan_row_exists():
    """A downgrade never drops data; the guard sees rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            prop = insert_property(conn, ws)
            _insert(conn, "valuation_snapshot", _row(ws, property_id=prop))
            forced = _forced(conn)
        with pytest.raises(
            DBAPIError, match=f"downgrade of {HEAD} refused: a property or loan month-end row"
        ) as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
            assert "valuation_snapshot" in forced
    finally:
        drop_database(name)
