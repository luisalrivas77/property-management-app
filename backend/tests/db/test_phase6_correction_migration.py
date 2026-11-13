"""Migration 0030 (docs/phases/phase-6.md D178 amended 13 Nov 2026): asset_movement.kind
accepts `balance_correction`, a correction carries its note and nothing else does, the note
never changes after the insert, the downgrade restores 0025's CHECK and drops the column, and
it is refused while a correction exists. The enum CHECK against MovementKind is
test_schema_consistency.py's. Synthetic data only."""

import pytest
from db.db_support import OWNER, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_binance_migration import _exchange
from db.test_phase6_migration import _as_owner, _asset, _forced, _movement, _new_workspace
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0030_balance_correction"
PREVIOUS = "0029_household_categories"
NOTE = "Gutschrift fehlt im Export"


def _correction(ws, account_id, asset_id, **overrides) -> dict:
    return (
        _movement(
            ws,
            account_id,
            asset_id,
            kind="balance_correction",
            quantity="0.0100000000",
            eur_amount=None,
            status="confirmed",
            note=NOTE,
        )
        | overrides
    )


def test_a_correction_carries_its_note_and_nothing_else_does(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        exchange, coin = _exchange(conn, a), _asset(conn, a)
        moved = _insert(conn, "asset_movement", _correction(a, exchange, coin))
        refused = (
            {"note": None},  # a correction without its reason
            {"source": "csv"},  # read from a file, not typed by a person
            {"eur_amount": "-1.00"},  # a correction has no EUR leg
            {"note": ""},  # an empty reason
            {"note": "x" * 501},
        )
        for change in refused:
            with pytest.raises(errors.CheckViolation), conn.transaction():
                _insert(conn, "asset_movement", _correction(a, exchange, coin, **change))
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "asset_movement", _movement(a, exchange, coin, note=NOTE))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("UPDATE asset_movement SET note = 'anders' WHERE id = %s", (moved,))


def _kind_check(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'asset_movement_kind_check'"
    ).fetchone()[0]


def _has_note(conn) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_name = 'asset_movement' AND column_name = 'note'"
        ).fetchone()
        is not None
    )


def test_0030_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert "balance_correction" in _kind_check(conn)
            assert _has_note(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            check = _kind_check(conn)
            assert "uncategorized" in check and "balance_correction" not in check
            assert not _has_note(conn)
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0030_downgrade_refuses_while_a_correction_exists():
    """A downgrade never leaves a row the old CHECK refuses nor drops a reason; the guard
    sees rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            exchange, coin = _exchange(conn, ws), _asset(conn, ws)
            _insert(conn, "asset_movement", _correction(ws, exchange, coin))
            forced = _forced(conn)
        with pytest.raises(
            DBAPIError, match=f"downgrade of {HEAD} refused: a movement is a balance correction"
        ) as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
            assert _has_note(conn)
    finally:
        drop_database(name)
