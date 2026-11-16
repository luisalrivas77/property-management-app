"""Phase 7 D181 (c) amended 16 Nov 2026: a balance-only address (BNB Smart Chain) looks the
exchange withdrawals on its chain up by their hash, so a deposit is written, linked and
confirmed; nothing is asked twice once the node gave a lasting answer.

services/chain/sync over the REAL balance-only source (`evm_rpc_balance.make_source`) with
only the JSON-RPC transport faked, in the node's own shapes; then the matcher's relink as
the sync job runs it. Every address, hash, host and amount is invented.
"""

import functools
import hashlib
import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import _admin, _insert
from db.test_phase6_migration import _asset, _depot
from db.test_phase7_migration import _address, _wallet
from sqlalchemy import text

from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import Chain, ChainSourceKind, WalletSyncStatus
from hausbuch.schemas.enums import ActorType
from hausbuch.services.chain import evm_rpc_balance
from hausbuch.services.chain.base import RequestBudget, register_source
from hausbuch.services.chain.sync import (
    queue_deposit_lookups,
    sync_wallets,
    wallets_awaiting_deposits,
)
from hausbuch.services.chain.transfers import relink_after_sync
from hausbuch.services.context import Actor
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

OWN = "0x" + "4b" * 20  # the household's invented BSC address
OTHER = "0x" + "7c" * 20  # an address the household does not hold
HOT = "0x" + "e1" * 20  # the exchange's invented hot wallet
RPC_URL = "https://bsc-node.invalid/v1/INVENTEDPATHKEY"
ACTOR = Actor(ActorType.SYSTEM, "sync_wallets")
TIP = 50_000_100
BNB = 10**18
SENT_AT = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def _hash(text_: str) -> str:
    return "0x" + hashlib.sha256(text_.encode()).hexdigest()


def _settings() -> Settings:
    from pydantic import SecretStr

    return Settings(
        database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
        bsc_rpc_url=SecretStr(RPC_URL),
    )


class Node:
    """A BSC node: the tip, the native balance, every `balanceOf` 0, and the transactions,
    receipts and block headers it knows by hash (a hash it does not know answers null)."""

    def __init__(self) -> None:
        self.native = 0
        self.txs: dict[str, dict] = {}
        self.receipts: dict[str, dict] = {}
        self.calls: list[tuple[str, list]] = []

    def send(self, tx_hash: str, to: str, units: int, *, block: int = TIP - 100) -> None:
        self.txs[tx_hash] = {
            "hash": tx_hash,
            "from": HOT,
            "to": to,
            "value": hex(units),
            "blockNumber": hex(block),
        }
        self.receipts[tx_hash] = {"transactionHash": tx_hash, "status": "0x1", "logs": []}

    def asked(self, method: str) -> list[list]:
        return [params for m, params in self.calls if m == method]

    def __call__(self, url, headers, body, timeout, max_bytes):
        assert url == RPC_URL
        payload = json.loads(body)
        method, params = payload["method"], payload["params"]
        self.calls.append((method, params))
        if method == "eth_blockNumber":
            result = hex(TIP)
        elif method == "eth_getBalance":
            assert params == [OWN, hex(TIP - 12)]
            result = hex(self.native)
        elif method == "eth_call":
            result = "0x0"
        elif method == "eth_getTransactionByHash":
            result = self.txs.get(params[0])
        elif method == "eth_getTransactionReceipt":
            result = self.receipts.get(params[0])
        elif method == "eth_getBlockByNumber":
            seconds = int(SENT_AT.timestamp()) + 30
            result = {"number": params[0], "timestamp": hex(seconds)}
        else:  # pragma: no cover - a call the source must not make
            raise AssertionError(method)
        return 200, json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()


@pytest.fixture
def node():
    fake = Node()
    register_source(
        ChainSourceKind.EVM_RPC_BALANCE,
        functools.partial(evm_rpc_balance.make_source, post=fake, sleep=lambda s: None),
    )
    yield fake
    register_source(ChainSourceKind.EVM_RPC_BALANCE, None)


class Seed:
    """A wallet with OWN on BSC and an exchange holding plain BNB, written as the superuser."""

    def __init__(self, dbname: str, ws: uuid.UUID, *, address: str = OWN) -> None:
        self.dbname, self.ws = dbname, ws
        with _admin(dbname) as conn:
            self.wallet = _wallet(conn, ws, label="Hardware-Wallet")
            self.address = _address(conn, ws, self.wallet, chain="bsc", address=address)
            self.exchange = _depot(
                conn, ws, kind="exchange", external_ref_masked="**** 0815", label="Börse"
            )
            self.bnb = _asset(conn, ws, symbol="BNB", name="BNB")

    def withdrawal(
        self,
        units: str = "2.001",
        *,
        fee: str | None = "0.001",
        tx_hash: str | None = None,
        network: str | None = "BSC",
        at: datetime = SENT_AT,
    ) -> uuid.UUID:
        with _admin(self.dbname) as conn:
            return _insert(
                conn,
                "asset_movement",
                {
                    "workspace_id": self.ws,
                    "account_id": self.exchange,
                    "asset_id": self.bnb,
                    "kind": "internal_transfer_out",
                    "quantity": f"-{units}",
                    "fee_quantity": fee,
                    "occurred_at": at,
                    "tx_hash": tx_hash,
                    "network": network,
                    "source": "csv",
                    "status": "confirmed",
                    "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                },
            )


def _run(app_engine, blob_store, ws, account, *, day=0, budgets=None):
    """One sync of the wallet, then the matcher's relink, as jobs/sync_wallets runs them."""
    ticks = itertools.count()
    clock = lambda: NOW + timedelta(days=day, seconds=next(ticks))  # noqa: E731
    outcomes = sync_wallets(
        app_engine,
        ws,
        settings=_settings(),
        store=blob_store,
        actor=ACTOR,
        clock=clock,
        account_id=account,
        budgets=budgets,
    )
    relink_after_sync(app_engine, ws, actor=ACTOR, clock=clock)
    (outcome,) = outcomes
    return outcome


def _q(dbname, query, *params):
    with _admin(dbname) as conn:
        return conn.execute(query, params).fetchall()


def _deposits(dbname, address_id):
    return _q(
        dbname,
        "SELECT g.tx_hash, g.kind, g.status, m.kind, m.quantity, m.status, m.tx_hash"
        " FROM asset_movement m JOIN movement_group g ON g.id = m.group_id"
        " WHERE m.address_id = %s AND m.superseded_by IS NULL",
        address_id,
    )


def _links(dbname, ws):
    return _q(
        dbname,
        "SELECT l.out_movement_id, l.in_movement_id, l.matched_by, e.payload->>'reason'"
        " FROM transfer_link l JOIN event e ON e.entity_id = l.id AND e.type = 'transfer.linked'"
        " WHERE l.workspace_id = %s AND l.superseded_by IS NULL",
        ws,
    )


def _remembered(dbname, ws):
    return _q(
        dbname,
        "SELECT payload->>'movement_id', payload->>'outcome', payload->>'source_host'"
        " FROM event WHERE workspace_id = %s AND type = 'wallet.deposit_looked_up'",
        ws,
    )


def test_a_withdrawal_named_by_its_hash_is_written_linked_and_confirmed(
    dbname, app_engine, blob_store, fresh_workspaces, node
):
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    tx_hash = _hash("invented withdrawal")
    withdrawal = seed.withdrawal(tx_hash=tx_hash.upper().replace("0X", ""))  # as printed
    node.send(tx_hash, OWN, 2 * BNB)
    node.native = 2 * BNB

    out = _run(app_engine, blob_store, ws, seed.wallet)
    assert (out.status, out.movements_new, out.deferred) == (WalletSyncStatus.SUCCEEDED, 1, False)
    # the hash was normalised before it was sent, the balances read first
    assert node.asked("eth_getTransactionByHash") == [[tx_hash]]
    methods = [m for m, _ in node.calls]
    assert methods.index("eth_getBalance") < methods.index("eth_getTransactionByHash")

    ((g_hash, g_kind, g_status, kind, quantity, status, m_hash),) = _deposits(dbname, seed.address)
    assert (g_hash, g_kind, kind, quantity) == (
        tx_hash,
        "exchange_transfer",
        "internal_transfer_in",
        Decimal("2"),
    )
    assert (g_status, status) == ("confirmed", "confirmed")  # by the hash link (D186 (1))
    ((out_id, in_id, matched_by, reason),) = _links(dbname, ws)
    assert (out_id, matched_by, reason) == (withdrawal, "matcher", "hash")
    assert in_id is not None and m_hash == tx_hash

    # the node's answers are the run's original (D182 (3)), block header included
    (key,) = _q(
        dbname,
        "SELECT f.storage_key FROM wallet_sync s JOIN document d ON d.id = s.document_id"
        " JOIN document_file f ON f.workspace_id = d.workspace_id AND f.sha256 = d.file_sha256"
        " WHERE s.address_id = %s AND s.document_id IS NOT NULL",
        seed.address,
    )[0]
    (line,) = [json.loads(x) for x in blob_store.get(key).decode().splitlines()]
    assert set(line) == {"transaction", "receipt", "block"}
    assert line["transaction"]["hash"] == tx_hash

    # the next run asks nothing: the wallet holds the transaction now; and it reconciles
    node.calls.clear()
    again = _run(app_engine, blob_store, ws, seed.wallet, day=1)
    assert node.asked("eth_getTransactionByHash") == []
    assert (again.movements_new, again.reconciled, again.card) == (0, True, None)


def test_a_receipt_crediting_another_address_writes_nothing_and_is_asked_once(
    dbname, app_engine, blob_store, fresh_workspaces, node
):
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    tx_hash = _hash("invented withdrawal to a friend")
    withdrawal = seed.withdrawal(tx_hash=tx_hash)
    node.send(tx_hash, OTHER, 2 * BNB)

    first = _run(app_engine, blob_store, ws, seed.wallet)
    assert (first.status, first.movements_new) == (WalletSyncStatus.SUCCEEDED, 0)
    assert _deposits(dbname, seed.address) == [] and _links(dbname, ws) == []
    assert _remembered(dbname, ws) == [(str(withdrawal), "absent", "bsc-node.invalid")]

    node.calls.clear()
    _run(app_engine, blob_store, ws, seed.wallet, day=1)
    assert node.asked("eth_getTransactionByHash") == []  # a lasting no: never asked again
    assert len(_remembered(dbname, ws)) == 1


def test_an_unknown_hash_is_asked_again_until_it_is_a_day_old(
    dbname, app_engine, blob_store, fresh_workspaces, node
):
    """A node that lags behind the exchange does not know a fresh hash: asked again next run.
    A hash still unknown a day after the withdrawal is remembered for that node."""
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    young = seed.withdrawal(tx_hash=_hash("young"), at=NOW - timedelta(hours=2))
    old = seed.withdrawal(tx_hash=_hash("old"))

    _run(app_engine, blob_store, ws, seed.wallet)
    assert len(node.asked("eth_getTransactionByHash")) == 2
    assert _remembered(dbname, ws) == [(str(old), "unknown", "bsc-node.invalid")]

    node.calls.clear()
    _run(app_engine, blob_store, ws, seed.wallet, day=0)
    assert node.asked("eth_getTransactionByHash") == [[_hash("young")]]
    assert young not in {r[0] for r in _remembered(dbname, ws)}


def test_a_malformed_or_foreign_network_hash_costs_no_request(
    dbname, app_engine, blob_store, fresh_workspaces, node
):
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    seed.withdrawal(tx_hash="not-a-hash")
    seed.withdrawal(tx_hash=_hash("on ethereum"), network="ETH")
    seed.withdrawal(tx_hash=None, network=None)  # BISON: no hash (the card's pick)
    out = _run(app_engine, blob_store, ws, seed.wallet)
    assert out.status is WalletSyncStatus.SUCCEEDED
    assert node.asked("eth_getTransactionByHash") == []
    assert _remembered(dbname, ws) == []


def test_another_workspace_s_withdrawal_is_never_looked_up(
    dbname, app_engine, blob_store, fresh_workspaces, node
):
    """Row-level security: the sync runs as the app role in its workspace; a withdrawal of
    another workspace, even naming a hash the node would credit to this address, is unseen."""
    ws, other = fresh_workspaces
    seed = Seed(dbname, ws)
    theirs = Seed(dbname, other, address=OTHER)
    tx_hash = _hash("the other household's withdrawal")
    theirs.withdrawal(tx_hash=tx_hash)
    node.send(tx_hash, OWN, 2 * BNB)

    out = _run(app_engine, blob_store, ws, seed.wallet)
    assert (out.status, out.movements_new) == (WalletSyncStatus.SUCCEEDED, 0)
    assert node.asked("eth_getTransactionByHash") == []
    assert _deposits(dbname, seed.address) == []


def test_a_budget_spent_on_the_lookups_keeps_the_balances(
    dbname, app_engine, blob_store, fresh_workspaces, node
):
    """The lookups come after the balances: a budget they spend stops them only, the run is
    reconciled and not deferred, and the withdrawal is asked again next run."""
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    tx_hash = _hash("invented withdrawal")
    seed.withdrawal(tx_hash=tx_hash)
    node.send(tx_hash, OWN, 2 * BNB)
    tokens = sum(1 for t in asset_registry().tokens if t.chain is Chain.BSC)
    # the tip, the native balance, each token's balanceOf, and one lookup request
    budgets = {ChainSourceKind.EVM_RPC_BALANCE: RequestBudget(2 + tokens + 1)}
    out = _run(app_engine, blob_store, ws, seed.wallet, budgets=budgets)
    assert (out.status, out.deferred, out.reconciled) == (WalletSyncStatus.SUCCEEDED, False, True)
    assert out.movements_new == 0 and _remembered(dbname, ws) == []

    second = _run(app_engine, blob_store, ws, seed.wallet, day=1)
    assert second.movements_new == 1


def test_an_import_or_answer_queues_the_wallet_s_sync(dbname, app_engine, fresh_workspaces, run):
    """The hook: a wallet with a withdrawal to look up gets a sync queued (the request never
    calls the node); a wallet with none, or once the deposit is linked, gets none."""
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    with app_engine.begin() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'sync_wallet'")
        ).scalar_one()
    try:
        assert run(ws, lambda ctx: wallets_awaiting_deposits(ctx.conn)) == []
        seed.withdrawal(tx_hash=_hash("invented withdrawal"))
        assert run(ws, lambda ctx: wallets_awaiting_deposits(ctx.conn)) == [seed.wallet]
        assert run(ws, queue_deposit_lookups) == 1
        with app_engine.begin() as conn:
            after = conn.execute(
                text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'sync_wallet'")
            ).scalar_one()
        assert after == before + 1
    finally:
        with app_engine.begin() as conn:
            conn.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'sync_wallet'"))
