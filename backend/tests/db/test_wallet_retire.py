"""Retiring a wallet address that still holds coins is refused (a sync follow-up of the
Bitcoin wiring's review; docs/phases/phase-7.md §15.1, „Sync follow-ups“ item 3).

A retired address is no longer synced, so coins on it would leave the wallet's figures
unseen. The newest balance read that covers the address decides: its own row, or on Bitcoin
(reconciled per wallet, D185) the wallet's newest row on the chain. Invented addresses only.
"""

from __future__ import annotations

import pytest
from db.db_support import _admin, _insert
from db.test_phase6_migration import _asset
from db.test_phase7_migration import (
    BECH32,
    EVM,
    EVM_2,
    LEGACY,
    _address,
    _chain_movement,
    _group_row,
    _wallet,
)
from psycopg.types.json import Jsonb

from hausbuch.db.session import workspace_transaction
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.enums import ActorType
from hausbuch.services.context import Actor, new_command
from hausbuch.services.wallets import update_address

ACTOR = Actor(ActorType.USER, "user:test")
HOLDS_COINS = (
    "Diese Adresse hält laut dem letzten Abruf noch Coins. Erst die Coins wegsenden und den "
    "nächsten Abruf abwarten, dann die Adresse stilllegen."
)
HOLDS_BITCOIN = (
    "Diese Wallet hält laut dem letzten Abruf noch Bitcoin. Bitcoin wird je Wallet abgeglichen, "
    "nicht je Adresse, deshalb lässt sich eine einzelne Bitcoin-Adresse erst stilllegen, wenn "
    "die Wallet keine Bitcoin mehr hält. Erst die Bitcoin wegsenden und den nächsten Abruf "
    "abwarten."
)
NEVER_READ = (
    "Für diese Adresse sind Bewegungen gespeichert, aber ihr Kontostand wurde noch nie "
    "abgerufen. Erst den nächsten vollständigen Abruf abwarten; zeigt er keine Coins mehr, "
    "lässt sich die Adresse stilllegen."
)


def _sync_row(conn, ws, account, address, *, at: str, balances, status="succeeded"):
    row = {
        "workspace_id": ws,
        "account_id": account,
        "address_id": address,
        "started_at": at,
        "finished_at": at,
        "status": status,
        "source_host": "chain.example.test",
    }
    if balances is not None:
        row["balances"] = Jsonb(balances)
    if status != "succeeded":
        row["error_code"] = E.WALLET_SOURCE_FAILED
    return _insert(conn, "wallet_sync", row)


def _retire(app_engine, ws, account, address):
    with workspace_transaction(app_engine, ws) as conn:
        return update_address(new_command(conn, ws, ACTOR), account, address, active=False)


@pytest.fixture
def evm_wallet(dbname, fresh_workspaces):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        a = _address(
            conn,
            ws,
            account,
            chain="ethereum",
            address=EVM,
            address_masked=EVM[:4] + "…" + EVM[-4:],
        )
        b = _address(
            conn,
            ws,
            account,
            chain="arbitrum",
            address=EVM_2,
            address_masked=EVM_2[:4] + "…" + EVM_2[-4:],
        )
    return ws, account, a, b


def test_an_address_whose_newest_balance_is_not_zero_cannot_be_retired(
    dbname, app_engine, evm_wallet
):
    ws, account, a, b = evm_wallet
    with _admin(dbname) as conn:
        _sync_row(conn, ws, account, a, at="2026-05-01T06:45:00Z", balances={"native": "0"})
        _sync_row(conn, ws, account, a, at="2026-05-02T06:45:00Z", balances={"native": "7"})
        # a later run that read no balance (deferred, or its balance read failed) and a failed
        # run do not hide the coins the newest balance read showed
        _sync_row(conn, ws, account, a, at="2026-05-03T06:45:00Z", balances=None)
        _sync_row(conn, ws, account, a, at="2026-05-04T06:45:00Z", balances=None, status="failed")
        # another address's empty balance says nothing about this one (address scope)
        _sync_row(conn, ws, account, b, at="2026-05-05T06:45:00Z", balances={"native": "0"})
    with pytest.raises(RuleError) as refused:
        _retire(app_engine, ws, account, a)
    assert (refused.value.code, str(refused.value)) == (E.WALLET_ADDRESS_HOLDS_COINS, HOLDS_COINS)
    (active,) = _admin_one(dbname, "SELECT active FROM wallet_address WHERE id = %s", a)
    assert active is True

    # emptied: the next balance read is zero, so it may be retired; relabelling never asks
    with _admin(dbname) as conn:
        _sync_row(conn, ws, account, a, at="2026-05-06T06:45:00Z", balances={"native": "0"})
    assert _retire(app_engine, ws, account, a).active is False
    assert _retire(app_engine, ws, account, b).active is False  # its newest read is zero


def test_an_address_never_read_and_a_token_balance(dbname, app_engine, evm_wallet):
    ws, account, a, b = evm_wallet
    with _admin(dbname) as conn:
        _sync_row(
            conn,
            ws,
            account,
            b,
            at="2026-05-01T06:45:00Z",
            balances={"native": "0", "0x" + "ab" * 20: "5"},
        )
    assert _retire(app_engine, ws, account, a).active is False  # no balance ever read
    with pytest.raises(RuleError) as refused:
        _retire(app_engine, ws, account, b)
    assert (refused.value.code, str(refused.value)) == (E.WALLET_ADDRESS_HOLDS_COINS, HOLDS_COINS)


def test_bitcoin_the_wallets_newest_balance_on_the_chain_decides(
    dbname, app_engine, fresh_workspaces
):
    """Bitcoin is reconciled per wallet: every row stores the wallet's sum over its
    addresses, and no per-address figure is kept, so an address of a wallet whose newest
    Bitcoin balance is not zero cannot be retired, even when its own older row read zero."""
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        a = _address(
            conn,
            ws,
            account,
            chain="bitcoin",
            address=BECH32,
            address_masked=BECH32[:4] + "…" + BECH32[-4:],
        )
        b = _address(
            conn,
            ws,
            account,
            chain="bitcoin",
            address=LEGACY,
            address_masked=LEGACY[:4] + "…" + LEGACY[-4:],
        )
        _sync_row(conn, ws, account, a, at="2026-05-01T06:45:00Z", balances={"native": "0"})
        _sync_row(conn, ws, account, b, at="2026-05-02T06:45:00Z", balances={"native": "1500"})
    with pytest.raises(RuleError) as refused:
        _retire(app_engine, ws, account, a)
    # its own text: the wallet holds bitcoin, not necessarily this address (review of PR #397)
    assert (refused.value.code, str(refused.value)) == (
        E.WALLET_ADDRESS_HOLDS_COINS,
        HOLDS_BITCOIN,
    )
    with _admin(dbname) as conn:
        _sync_row(conn, ws, account, a, at="2026-05-03T06:45:00Z", balances={"native": "0"})
    assert _retire(app_engine, ws, account, a).active is False


def test_an_address_with_movements_and_no_balance_ever_read_cannot_be_retired(
    dbname, app_engine, evm_wallet
):
    """Review of PR #397: an address whose movements are stored but whose balance was never
    read (every run so far deferred, or its balance read failed) may still hold coins, so it
    is refused with its own text; a superseded movement does not count."""
    ws, account, a, b = evm_wallet
    with _admin(dbname) as conn:
        coin = _asset(conn, ws, symbol="ETH", name="Ether", chain="ethereum")
        group = _insert(conn, "movement_group", _group_row(ws, account))
        _insert(conn, "asset_movement", _chain_movement(ws, account, coin, a, group_id=group))
        _sync_row(conn, ws, account, a, at="2026-05-01T06:45:00Z", balances=None)
    with pytest.raises(RuleError) as refused:
        _retire(app_engine, ws, account, a)
    assert (refused.value.code, str(refused.value)) == (E.WALLET_ADDRESS_HOLDS_COINS, NEVER_READ)

    # dust still blocks once read: no threshold
    with _admin(dbname) as conn:
        _sync_row(conn, ws, account, a, at="2026-05-02T06:45:00Z", balances={"native": "1"})
    with pytest.raises(RuleError) as dust:
        _retire(app_engine, ws, account, a)
    assert str(dust.value) == HOLDS_COINS
    with _admin(dbname) as conn:
        _sync_row(conn, ws, account, a, at="2026-05-03T06:45:00Z", balances={"native": "0"})
    assert _retire(app_engine, ws, account, a).active is False

    # a movement superseded by one on another address is no live movement of b: b was never
    # read and holds none, so it may be retired
    with _admin(dbname) as conn:
        on_arbitrum = _asset(conn, ws, symbol="ETH", name="Ether", chain="arbitrum")
        group = _insert(conn, "movement_group", _group_row(ws, account, chain="arbitrum"))
        old = _insert(
            conn, "asset_movement", _chain_movement(ws, account, on_arbitrum, b, group_id=group)
        )
        new = _insert(
            conn,
            "asset_movement",
            _chain_movement(ws, account, on_arbitrum, a, group_id=group, index="9"),
        )
        conn.execute("UPDATE asset_movement SET superseded_by = %s WHERE id = %s", (new, old))
    assert _retire(app_engine, ws, account, b).active is False


def _admin_one(dbname, query, *params):
    with _admin(dbname) as conn:
        return conn.execute(query, params).fetchone()
