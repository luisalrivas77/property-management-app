"""Migration 0046 (docs/phases/phase-7.md D261): `exchange_deposit_address`,
`exchange_withdrawal_pair` and the counterparty columns of `asset_movement`: no plaintext
column, the CHECKs, one live row per address, an end set once, the grants, RLS and the
downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py, the
round trip there too. Synthetic data only: random HMAC bytes, invented masked forms."""

import hashlib
import os
import uuid

import pytest
from db.db_support import APP, OWNER, _admin, _insert, alembic, create_database, drop_database
from db.test_phase6_migration import _asset, _depot
from db.test_phase7_migration import _address, _wallet
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0046_learned_exchange_transfers"
PREVIOUS = "0045_app_secret"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
TABLES = ("exchange_deposit_address", "exchange_withdrawal_pair")


class World:
    def __init__(self, conn, ws):
        self.conn, self.ws = conn, ws
        self.exchange = _depot(conn, ws, kind="exchange", label="Börse")
        self.wallet = _wallet(conn, ws)
        self.address = _address(conn, ws, self.wallet)
        self.asset = _asset(conn, ws, symbol="ETH", name="Ether")
        self.send = _insert(conn, "asset_movement", self.movement())

    def movement(self, **overrides) -> dict:
        return {
            "workspace_id": self.ws,
            "account_id": self.exchange,
            "asset_id": self.asset,
            "kind": "internal_transfer_out",
            "quantity": "-1",
            "occurred_at": "2026-05-04T10:00:00Z",
            "source": "csv",
            "status": "confirmed",
            "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        } | overrides

    def deposit_row(self, **overrides) -> dict:
        return {
            "workspace_id": self.ws,
            "account_id": self.exchange,
            "chain": "ethereum",
            "address_hmac": os.urandom(32),
            "address_masked": "0x1a…9f0b",
            "learned_from": self.send,
        } | overrides

    def pair_row(self, **overrides) -> dict:
        return {
            "workspace_id": self.ws,
            "account_id": self.exchange,
            "address_id": self.address,
            "learned_from": self.send,
        } | overrides

    def row(self, table: str, **overrides) -> dict:
        return (self.deposit_row if table == TABLES[0] else self.pair_row)(**overrides)


@pytest.fixture
def world(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        yield World(conn, a)


@pytest.mark.parametrize("table", TABLES)
def test_app_role_may_read_insert_and_end_but_never_delete(connect, table):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, table, p)).fetchone()[0]
        }
    assert granted == {"SELECT", "INSERT", "UPDATE"}


def test_no_column_holds_an_address_in_clear(connect):
    with connect(OWNER) as conn:
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'exchange_deposit_address'"
            )
        }
    assert "address" not in columns
    assert {"address_hmac", "address_masked"} <= columns


def test_one_live_row_per_address_and_a_new_one_after_the_end(world):
    hmac = os.urandom(32)
    first = _insert(world.conn, "exchange_deposit_address", world.deposit_row(address_hmac=hmac))
    with pytest.raises(errors.UniqueViolation), world.conn.transaction():
        _insert(world.conn, "exchange_deposit_address", world.deposit_row(address_hmac=hmac))
    # The same address on another chain is another row (one 0x address on every EVM chain).
    _insert(
        world.conn,
        "exchange_deposit_address",
        world.deposit_row(address_hmac=hmac, chain="arbitrum"),
    )
    world.conn.execute(
        "UPDATE exchange_deposit_address SET ended_at = now() WHERE id = %s", (first,)
    )
    _insert(world.conn, "exchange_deposit_address", world.deposit_row(address_hmac=hmac))


def test_an_end_is_set_once_and_nothing_else_changes(world):
    row = _insert(world.conn, "exchange_withdrawal_pair", world.pair_row())
    with pytest.raises(errors.RestrictViolation), world.conn.transaction():
        world.conn.execute(
            "UPDATE exchange_withdrawal_pair SET set_by = %s WHERE id = %s", (uuid.uuid4(), row)
        )
    world.conn.execute("UPDATE exchange_withdrawal_pair SET ended_at = now() WHERE id = %s", (row,))
    with pytest.raises(errors.RestrictViolation), world.conn.transaction():
        world.conn.execute(
            "UPDATE exchange_withdrawal_pair SET ended_at = now() WHERE id = %s", (row,)
        )
    with pytest.raises(errors.InsufficientPrivilege), world.conn.transaction():
        world.conn.execute("DELETE FROM exchange_withdrawal_pair WHERE id = %s", (row,))


def test_even_the_owner_never_deletes_a_learned_row(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        row = _insert(conn, "exchange_withdrawal_pair", World(conn, a).pair_row())
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("DELETE FROM exchange_withdrawal_pair WHERE id = %s", (row,))


@pytest.mark.parametrize("table", TABLES)
def test_a_learned_row_belongs_to_an_exchange(world, table):
    with pytest.raises(errors.CheckViolation), world.conn.transaction():
        _insert(world.conn, table, world.row(table, account_id=world.wallet))


@pytest.mark.parametrize(
    "change",
    [
        {"address_hmac": os.urandom(16)},
        {"address_masked": "0x1a2b3c4d5e6f"},
        {"chain": "dogecoin"},
        {"ended_by": uuid.uuid4()},
    ],
    ids=["hmac_not_32_bytes", "not_masked", "unknown_chain", "ended_by_without_end"],
)
def test_a_deposit_address_outside_its_checks_is_refused(world, change):
    with pytest.raises(errors.CheckViolation), world.conn.transaction():
        _insert(world.conn, "exchange_deposit_address", world.deposit_row(**change))


def test_a_counterparty_is_hmac_and_mask_together_and_only_on_a_chain_movement(world):
    with pytest.raises(errors.CheckViolation), world.conn.transaction():
        _insert(world.conn, "asset_movement", world.movement(counterparty_hmac=os.urandom(32)))
    with pytest.raises(errors.CheckViolation), world.conn.transaction():
        _insert(
            world.conn,
            "asset_movement",
            world.movement(counterparty_hmac=os.urandom(32), counterparty_masked="0x1a…9f0b"),
        )


@pytest.mark.parametrize("table", TABLES)
def test_a_learned_row_is_invisible_to_another_workspace(connect, workspaces, table):
    a, b = workspaces
    with connect(APP, a) as conn:
        w = World(conn, a)
        _insert(conn, table, w.row(table))
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


# --- the downgrade -------------------------------------------------------------------------


def test_0046_downgrade_refuses_while_an_address_is_learned():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = uuid.uuid4()
        with _admin(name) as conn:
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
            w = World(conn, ws)
            _insert(conn, "exchange_deposit_address", w.deposit_row())
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
    finally:
        drop_database(name)


def test_0046_downgrade_without_data_drops_the_tables():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename LIKE 'exchange_%'"
            ).fetchone() == (0,)
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)
