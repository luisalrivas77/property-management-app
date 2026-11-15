"""Migration 0042 (docs/phases/phase-7.md §15.1 item 9): a coin's units to 18 decimal places and
`asset.decimals` to 0..18; the downgrade guard. Synthetic data only."""

import importlib.util
import os
from decimal import Decimal

import pytest
from db.db_support import BACKEND, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_migration import (
    _as_owner,
    _asset,
    _depot,
    _forced,
    _movement,
    _new_workspace,
)
from psycopg import errors
from sqlalchemy.exc import DBAPIError


def _migration():
    path = os.path.join(BACKEND, "migrations", "versions", "0042_quantity_scale.py")
    spec = importlib.util.spec_from_file_location("migration_0042", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()
HEAD = MIGRATION.revision
PREVIOUS = MIGRATION.down_revision

# evm_tx.yaml's fee_below_numeric_scale: 21,000,000,147,000 wei.
FEE = Decimal("0.000021000000147")


def _types(conn) -> dict[tuple[str, str], tuple[int, int]]:
    rows = conn.execute(
        "SELECT table_name, column_name, numeric_precision, numeric_scale"
        " FROM information_schema.columns WHERE table_schema = 'public'"
        " AND (table_name, column_name) IN ("
        + ", ".join(f"('{t}', '{c}')" for t, c in MIGRATION.QUANTITY_COLUMNS)
        + ")"
    )
    return {(t, c): (p, s) for t, c, p, s in rows}


def _decimals_check(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid = 'asset'::regclass AND conname = %s",
        (MIGRATION.DECIMALS_CHECK,),
    ).fetchone()[0]


def _seed(name, *, quantity="1.5000000000", decimals=8) -> None:
    ws = _new_workspace(name)
    with _admin(name) as conn:
        _as_owner(conn, ws)
        depot = _depot(conn, ws)
        asset = _asset(conn, ws, decimals=decimals)
        _insert(conn, "asset_movement", _movement(ws, depot, asset, quantity=quantity))
        _insert(
            conn,
            "holding_statement",
            {
                "workspace_id": ws,
                "account_id": depot,
                "asset_id": asset,
                "as_of": "2026-05-31",
                "declared_units": quantity.lstrip("-"),
                "source": "user",
            },
        )


def test_0042_widens_every_unit_column_and_keeps_the_values():
    name = create_database()
    try:
        alembic(name, "upgrade", PREVIOUS)
        _seed(name, quantity="12345678901234567.1234567891")  # 0025's widest value
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert _types(conn) == {col: (38, 18) for col in MIGRATION.QUANTITY_COLUMNS}
            assert "(decimals <= 18)" in _decimals_check(conn)
            # The price and every EUR figure keep their types.
            kept = dict(
                conn.execute(
                    "SELECT table_name || '.' || column_name,"
                    " numeric_precision || ',' || numeric_scale FROM information_schema.columns"
                    " WHERE (table_name, column_name) IN (('price', 'price_eur'),"
                    " ('valuation_snapshot', 'price_eur'), ('asset_movement', 'eur_amount'))"
                ).fetchall()
            )
            assert kept == {
                "price.price_eur": "20,8",
                "valuation_snapshot.price_eur": "20,8",
                "asset_movement.eur_amount": "14,2",
            }
            assert conn.execute("SELECT quantity FROM asset_movement").fetchone()[0] == Decimal(
                "12345678901234567.1234567891"
            )
            triggers = {
                r[0]
                for r in conn.execute(
                    "SELECT tgname FROM pg_trigger"
                    " WHERE tgrelid = 'holding_statement'::regclass AND NOT tgisinternal"
                )
            }
            assert triggers  # the append-only triggers survive the rewrite
        # An 18-decimal fee is stored exactly, and an asset may say 18 decimals.
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            depot = _depot(conn, ws)
            asset = _asset(conn, ws, symbol="ETH", decimals=18)
            _insert(
                conn,
                "asset_movement",
                _movement(ws, depot, asset, quantity=str(-FEE), kind="fee", eur_amount=None),
            )
            stored = conn.execute(
                "SELECT quantity FROM asset_movement WHERE asset_id = %s", (asset,)
            ).fetchone()[0]
            assert stored == -FEE
            with pytest.raises(errors.CheckViolation), conn.transaction():
                _asset(conn, ws, symbol="BIG", decimals=19)
    finally:
        drop_database(name)


def test_0042_runs_up_down_and_up_with_ten_place_data():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        _seed(name)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert _types(conn) == {col: (28, 10) for col in MIGRATION.QUANTITY_COLUMNS}
            assert "(decimals <= 10)" in _decimals_check(conn)
            assert conn.execute("SELECT quantity FROM asset_movement").fetchone()[0] == Decimal(
                "1.5"
            )
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


@pytest.mark.parametrize(
    ("seed", "what"),
    [
        ({"quantity": "0.000021000000147"}, "asset_movement.quantity holds a value"),
        ({"decimals": 18}, "an asset has more than 10 decimals"),
    ],
)
def test_0042_downgrade_refuses_what_ten_places_cannot_hold(seed, what):
    """A downgrade never drops or rounds data; the guard sees rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        _seed(name, **seed)
        with _admin(name) as conn:
            forced = _forced(conn)
        assert {"asset", "asset_movement", "holding_statement"} <= forced
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused: {what}") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
            assert _types(conn) == {col: (38, 18) for col in MIGRATION.QUANTITY_COLUMNS}
    finally:
        drop_database(name)
