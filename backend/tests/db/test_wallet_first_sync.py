"""An added address queues its wallet's first sync (docs/phases/phase-7.md D182; step 2).

services/wallets.enqueue_first_sync defers one `sync_wallet` job on the command's own
connection, so it commits with the address and a rolled-back add queues nothing. The wallet
page shows each address's newest finished run. Every address is a published test vector.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY, _admin

from hausbuch.db.session import workspace_transaction
from hausbuch.jobs.sync_wallets import SYNC_WALLET_TASK
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
SOL = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"  # chains.yaml sol_generated


def _wallet(app_engine, ws):
    with workspace_transaction(app_engine, ws) as conn:
        return create_account(
            new_command(conn, ws, ACTOR, now=NOW),
            AccountCreate(
                label="Hardware-Wallet",
                bank="Testwallet",
                kind=AccountKind.WALLET,
                provider="Testwallet",
                opening_balance=Decimal("0.00"),
                opening_balance_on=datetime(2026, 3, 31).date(),
            ),
            iban_key=TEST_IBAN_KEY,
        ).id


def _jobs(dbname, ws) -> list[dict]:
    with _admin(dbname) as conn:  # the job table has no RLS: this workspace's jobs only
        return [
            row[0]
            for row in conn.execute(
                "SELECT args FROM procrastinate_jobs"
                " WHERE task_name = %s AND args->>'workspace_id' = %s",
                (SYNC_WALLET_TASK, str(ws)),
            ).fetchall()
        ]


def test_adding_an_address_queues_one_sync_of_its_wallet(app_engine, dbname, fresh_workspaces):
    ws, _ = fresh_workspaces
    wallet = _wallet(app_engine, ws)
    assert _jobs(dbname, ws) == []
    with workspace_transaction(app_engine, ws) as conn:
        created = svc.add_address(
            new_command(conn, ws, ACTOR, now=NOW),
            wallet,
            # one add on three chains is one job: the job reads every active address
            WalletAddressBody(
                chain=Chain.ETHEREUM, address=EVM, also_on=[Chain.ARBITRUM, Chain.BSC]
            ),
            key=TEST_IBAN_KEY,
        )
    assert len(created) == 3
    assert _jobs(dbname, ws) == [{"workspace_id": str(ws), "account_id": str(wallet)}]


def test_a_rolled_back_add_queues_nothing(app_engine, dbname, fresh_workspaces):
    ws, _ = fresh_workspaces
    wallet = _wallet(app_engine, ws)

    class Abort(Exception):
        pass

    with pytest.raises(Abort), workspace_transaction(app_engine, ws) as conn:
        svc.add_address(
            new_command(conn, ws, ACTOR, now=NOW),
            wallet,
            WalletAddressBody(chain=Chain.SOLANA, address=SOL),
            key=TEST_IBAN_KEY,
        )
        raise Abort  # a later step of the same request fails
    assert _jobs(dbname, ws) == []
    with _admin(dbname) as conn:
        count = conn.execute(
            "SELECT count(*) FROM wallet_address WHERE account_id = %s", (wallet,)
        ).fetchone()
    assert count[0] == 0


def test_the_wallet_page_shows_each_address_newest_finished_run(
    app_engine, dbname, fresh_workspaces
):
    ws, _ = fresh_workspaces
    wallet = _wallet(app_engine, ws)
    with workspace_transaction(app_engine, ws) as conn:
        [sol] = svc.add_address(
            new_command(conn, ws, ACTOR, now=NOW),
            wallet,
            WalletAddressBody(chain=Chain.SOLANA, address=SOL),
            key=TEST_IBAN_KEY,
        )
        [evm] = svc.add_address(
            new_command(conn, ws, ACTOR, now=NOW),
            wallet,
            WalletAddressBody(chain=Chain.ETHEREUM, address=EVM),
            key=TEST_IBAN_KEY,
        )
    insert = (
        "INSERT INTO wallet_sync (workspace_id, account_id, address_id, started_at,"
        " finished_at, status, source_host, block, movements_new, balances, reconciled,"
        " error_code) VALUES (%s, %s, %s, %s, %s, %s, 'rpc.example', %s, %s, %s, %s, %s)"
    )
    day = datetime(2026, 11, 16, 6, 30, tzinfo=UTC)
    with _admin(dbname) as conn:
        conn.execute(
            insert,
            (ws, wallet, sol.id, day, day, "succeeded", 10, 3, '{"native": "1"}', True, None),
        )
        later = day.replace(day=17)
        conn.execute(
            insert,
            (
                ws,
                wallet,
                sol.id,
                later,
                later,
                "failed",
                None,
                0,
                None,
                None,
                "wallet.source_failed",
            ),
        )
        conn.execute(
            insert,
            (ws, wallet, sol.id, later.replace(hour=7), None, "running", None, 0, None, None, None),
        )
    with workspace_transaction(app_engine, ws) as conn:
        page = svc.wallet_page(conn, wallet)
    assert page is not None
    by_chain = {a.chain: a.last_sync for a in page.addresses}
    assert by_chain[Chain.ETHEREUM] is None
    last = by_chain[Chain.SOLANA]
    assert last is not None
    assert (last.finished_at, last.error_code, last.reconciled, last.movements_new) == (
        later,
        "wallet.source_failed",
        None,
        0,
    )
    assert page.balances == [] and page.syncs == []
