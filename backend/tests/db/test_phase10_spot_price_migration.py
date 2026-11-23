"""Migration 0053 (docs/phases/phase-10.md §7.7, D291): `spot` in the price source CHECKs,
outside the daily index (several a day), and the downgrade that refuses while such a row
exists. The CHECKs against PriceSourceKind are test_schema_consistency.py's. Invented values
only."""

import pytest
from db.db_support import APP, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_exchange_price_migration import _definitions
from db.test_phase6_migration import _as_owner, _asset, _forced, _new_workspace, _price
from psycopg import errors

HEAD = "0053_spot_price"
PREVIOUS = "0052_depot_statement_trades"
SOURCE = "spot"


def test_a_spot_price_may_be_stored_several_times_a_day(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        asset = _asset(conn, a, kind="crypto", symbol="INVT", name="Invented Coin")
        _insert(conn, "price", _price(a, asset, source=SOURCE, fetched_at="2026-05-04T09:00Z"))
        _insert(conn, "price", _price(a, asset, source=SOURCE, fetched_at="2026-05-04T11:00Z"))
        _insert(conn, "price", _price(a, asset, source="kraken_daily"))
        # The daily sources keep their one row a day.
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _insert(conn, "price", _price(a, asset, source="kraken_daily", price_eur="101"))
        (n,) = conn.execute(
            "SELECT count(*) FROM price WHERE asset_id = %s AND source = 'spot'", (asset,)
        ).fetchone()
        assert n == 2


def test_0053_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            price, snapshot, index = _definitions(conn)
            assert SOURCE in price and SOURCE in snapshot and "exchange_close" in price
            assert SOURCE not in index and "exchange_close" in index and "UNIQUE" in index
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            price, snapshot, index = _definitions(conn)
            assert SOURCE not in price + snapshot + index
            assert "exchange_close" in price and "exchange_close" in snapshot
            assert "exchange_close" in index and "user" not in index
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0053_downgrade_refuses_while_a_spot_price_exists_and_keeps_rls_forced():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            asset = _asset(conn, ws, kind="crypto", symbol="INVT", name="Invented Coin")
            _insert(conn, "price", _price(ws, asset, source=SOURCE))
            forced = _forced(conn)
        with pytest.raises(Exception, match="refused"):
            alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert _forced(conn) == forced and {"price", "valuation_snapshot"} <= forced
            assert SOURCE in _definitions(conn)[0]
    finally:
        drop_database(name)
