"""Migration 0049 (docs/phases/phase-6.md D264): `exchange_close` in the price source CHECKs
and in the daily index, and the downgrade that refuses while such a row exists. The CHECKs
against PriceSourceKind are test_schema_consistency.py's. Invented values only."""

import pytest
from db.db_support import APP, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_migration import _as_owner, _asset, _forced, _new_workspace, _price
from psycopg import errors

HEAD = "0049_exchange_close_price"
PREVIOUS = "0048_household_plan"
SOURCE = "exchange_close"


def test_an_exchange_price_is_once_a_day_beside_a_nav(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        asset = _asset(conn, a, kind="etf", symbol=None, isin="IE000INVENT9", name="Fonds")
        _insert(conn, "price", _price(a, asset, source=SOURCE))
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _insert(conn, "price", _price(a, asset, source=SOURCE, price_eur="101.00000000"))
        _insert(conn, "price", _price(a, asset, source="issuer_nav"))


def _definitions(conn) -> tuple[str, str, str]:
    checks = dict(
        conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN"
            " ('price_source_check', 'valuation_snapshot_price_source_check')"
        ).fetchall()
    )
    (index,) = conn.execute("SELECT pg_get_indexdef('price_daily_key'::regclass)").fetchone()
    return checks["price_source_check"], checks["valuation_snapshot_price_source_check"], index


def test_0049_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            for definition in _definitions(conn):
                assert SOURCE in definition and "issuer_nav" in definition
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            price, snapshot, index = _definitions(conn)
            assert SOURCE not in price + snapshot + index
            assert "user" in price and "user" in snapshot and "ecb_fx" in index
            assert "UNIQUE" in index and "user" not in index
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0049_downgrade_refuses_while_an_exchange_price_exists_and_keeps_rls_forced():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            asset = _asset(conn, ws, kind="etf", symbol=None, isin="IE000INVENT9", name="F")
            _insert(conn, "price", _price(ws, asset, source=SOURCE))
            forced = _forced(conn)
        with pytest.raises(Exception, match="refused"):
            alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert _forced(conn) == forced and {"price", "valuation_snapshot"} <= forced
            assert SOURCE in _definitions(conn)[0]
    finally:
        drop_database(name)
