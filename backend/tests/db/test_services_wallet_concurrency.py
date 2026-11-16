"""Two adds of one address at once (docs/phases/phase-7.md D179; review of PR #374, 16 Nov 2026).

The wallet's row lock serialises adds to one wallet, and the unique key of wallet_address is
per (workspace, chain, HMAC), so two adds of one 0x address to two wallets on two chains both
committed. add_address now takes an advisory lock on the address (workspace and HMAC) before
its „taken elsewhere“ read: the second add waits for the first and is then refused with
wallet.address_taken. The unique key stays as the backstop, covered here with the lock taken
out. Services under hausbuch_app and RLS; every address is EIP-55's published example.
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY, _admin

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.errors import WALLET_ADDRESS_TAKEN, RuleError
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.chain import Chain
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.schemas.wallets import WalletAddressBody
from hausbuch.services import wallets as svc
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor, new_command

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55's first example


def _wallet(app_engine, ws, label: str):
    with workspace_transaction(app_engine, ws) as conn:
        return create_account(
            new_command(conn, ws, ACTOR, now=NOW),
            AccountCreate(
                label=label,
                bank="Testwallet",
                kind=AccountKind.WALLET,
                provider="Testwallet",
                opening_balance=Decimal("0.00"),
                opening_balance_on=datetime(2026, 3, 31).date(),
            ),
            iban_key=TEST_IBAN_KEY,
        ).id


def _body(chain: Chain, also_on: list[Chain]) -> WalletAddressBody:
    return WalletAddressBody(chain=chain, address=EVM, also_on=also_on)


def _race(app_engine, ws, first: tuple, second: tuple) -> list[Exception]:
    """The first add holds its transaction open while the second starts in a thread: the
    second must still be waiting a second later, and finish once the first commits. The
    second's lock_timeout and the join's timeout turn a regression into a failure, never a
    hang. Returns what the second add raised."""
    errors: list[Exception] = []
    finished = threading.Event()

    def second_add():
        try:
            with workspace_transaction(app_engine, ws) as conn:
                conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
                svc.add_address(
                    new_command(conn, ws, ACTOR, now=NOW),
                    second[0],
                    second[1],
                    key=TEST_IBAN_KEY,
                )
        except Exception as exc:  # noqa: BLE001 - collected for the assertions below
            errors.append(exc)
        finally:
            finished.set()

    with workspace_transaction(app_engine, ws) as conn:
        svc.add_address(
            new_command(conn, ws, ACTOR, now=NOW), first[0], first[1], key=TEST_IBAN_KEY
        )
        other = threading.Thread(target=second_add)
        other.start()
        assert not finished.wait(timeout=1), "the second add did not wait for the first"
    other.join(timeout=20)
    assert not other.is_alive()
    return errors


def _rows(dbname, ws) -> list[tuple]:
    with _admin(dbname) as conn:
        return conn.execute(
            "SELECT account_id, chain FROM wallet_address WHERE workspace_id = %s ORDER BY chain",
            (ws,),
        ).fetchall()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ((Chain.ETHEREUM, []), (Chain.ARBITRUM, [])),
        # The shortcut on both sides, in opposite order: one lock per add, so no deadlock.
        ((Chain.ETHEREUM, [Chain.ARBITRUM]), (Chain.BSC, [Chain.ETHEREUM])),
    ],
    ids=["two_chains", "also_on"],
)
def test_one_address_on_two_wallets_on_two_chains_cannot_both_commit(
    app_engine, dbname, fresh_workspaces, first, second
):
    ws, _ = fresh_workspaces
    wallet_a, wallet_b = _wallet(app_engine, ws, "Wallet A"), _wallet(app_engine, ws, "Wallet B")
    errors = _race(
        app_engine,
        ws,
        (wallet_a, _body(*first)),
        (wallet_b, _body(*second)),
    )
    [error] = errors
    assert isinstance(error, RuleError) and error.code == WALLET_ADDRESS_TAKEN
    rows = _rows(dbname, ws)
    assert {account_id for account_id, _ in rows} == {wallet_a}
    assert len(rows) == 1 + len(first[1])


def test_one_address_on_two_wallets_on_one_chain_waits_then_is_taken(
    app_engine, dbname, fresh_workspaces
):
    """With the lock, the second add's „taken elsewhere“ read refuses it after the wait."""
    ws, _ = fresh_workspaces
    wallet_a, wallet_b = _wallet(app_engine, ws, "Wallet A"), _wallet(app_engine, ws, "Wallet B")
    errors = _race(
        app_engine,
        ws,
        (wallet_a, _body(Chain.ETHEREUM, [])),
        (wallet_b, _body(Chain.ETHEREUM, [])),
    )
    [error] = errors
    assert isinstance(error, RuleError) and error.code == WALLET_ADDRESS_TAKEN
    assert [account_id for account_id, _ in _rows(dbname, ws)] == [wallet_a]


def test_the_unique_key_is_the_backstop_on_one_chain(
    app_engine, dbname, fresh_workspaces, monkeypatch
):
    """The lock taken out: both adds pass the read, the second's INSERT waits on the unique
    key until the first commits, and its IntegrityError becomes wallet.address_taken, the
    DBAPI text (which names the values) never chained."""
    ws, _ = fresh_workspaces
    wallet_a, wallet_b = _wallet(app_engine, ws, "Wallet A"), _wallet(app_engine, ws, "Wallet B")
    monkeypatch.setattr(svc, "_lock_address", lambda ctx, digest: None)
    errors = _race(
        app_engine,
        ws,
        (wallet_a, _body(Chain.ETHEREUM, [])),
        (wallet_b, _body(Chain.ETHEREUM, [])),
    )
    [error] = errors
    assert isinstance(error, RuleError) and error.code == WALLET_ADDRESS_TAKEN
    assert error.__cause__ is None and error.__suppress_context__
    assert [account_id for account_id, _ in _rows(dbname, ws)] == [wallet_a]
