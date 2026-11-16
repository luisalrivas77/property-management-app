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
        status: str = "confirmed",
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
                    "status": status,
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


# --- A withdrawal no hash proves: „An eigene Wallet“ and the learned pair -------------------

TODAY = NOW.date()


def _own_wallet(withdrawal, address, *, remember=True):
    from hausbuch.schemas.household import OwnWalletDepositCreate
    from hausbuch.services import transfers

    body = OwnWalletDepositCreate(
        withdrawal_movement_id=withdrawal, address_id=address, remember_withdrawal_pair=remember
    )
    return lambda ctx: transfers.own_wallet_deposit(ctx, body)


def _cards(read, ws):
    from hausbuch.services.chain.transfers import open_transfers

    return {o.leg.id: o for o in read(ws, open_transfers, TODAY)}


def _pairs(read, ws):
    from hausbuch.services.chain.learned import withdrawal_pairs

    return read(ws, withdrawal_pairs)


def _wallet_units(read, ws, wallet):
    """The wallet's coins as the lots read them (services.holdings, FIFO on read)."""
    from hausbuch.services.holdings import crypto_holdings

    coins = read(ws, crypto_holdings, TODAY).coins
    return sorted(c.units for c in coins if c.account_id == wallet)


def _current(read, ws):
    """The links that count (services.transfers._current_links: both legs live)."""
    from hausbuch.services.transfers import _current_links

    return read(ws, lambda conn: [r.id for r in _current_links(conn)])


def _relink(ctx):
    from hausbuch.services.chain.transfers import relink

    return relink(ctx)


def test_a_pick_writes_the_deposit_links_it_and_learns_the_pair(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    """BISON names no hash: its card offers the own BSC address; the pick writes the deposit
    (the withdrawal less its fee), links it as the person's answer and learns the pair. The
    address's balance check then reads it: equal → reconciled, no card; above → the card."""
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    withdrawal = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)

    card = _cards(read, ws)[withdrawal]
    assert card.code.value == "transfer.unmatched_leg" and card.paired_address_id is None
    (choice,) = card.own_wallets
    assert (choice.address_id, choice.account_label, choice.chain) == (
        seed.address,
        "Hardware-Wallet",
        "bsc",
    )
    assert choice.address_masked != OWN  # masked only (D179)

    link = run(ws, _own_wallet(withdrawal, seed.address))
    assert (link.matched_by.value, link.learned) == ("user", "learned")
    ((g_hash, g_kind, g_status, kind, quantity, status, m_hash),) = _deposits(dbname, seed.address)
    assert (g_kind, g_status, kind, quantity, status, m_hash) == (
        "exchange_transfer",
        "confirmed",
        "internal_transfer_in",
        Decimal("2"),
        "confirmed",
        None,
    )
    assert g_hash == f"exchange-withdrawal:{withdrawal}"
    ((out_id, in_id, matched_by, reason),) = _links(dbname, ws)
    assert (out_id, matched_by, reason) == (withdrawal, "user", "own_wallet")
    assert _pairs(read, ws) == {(seed.exchange, seed.address)}
    assert withdrawal not in _cards(read, ws)
    assert _wallet_units(read, ws, seed.wallet) == [Decimal("2")]
    # the written group is no person's answer: „Antwort zurücknehmen“ never lists it
    ((group_reason,),) = _q(
        dbname,
        "SELECT e.payload->>'reason' FROM event e JOIN asset_movement m ON m.group_id ="
        " e.entity_id WHERE m.id = %s AND e.type = 'movement_group.confirmed'",
        in_id,
    )
    assert group_reason == "routing.chain_written_deposit"

    node.native = 2 * BNB
    equal = _run(app_engine, blob_store, ws, seed.wallet)
    assert (equal.reconciled, equal.card) == (True, None)
    node.native = 3 * BNB
    above = _run(app_engine, blob_store, ws, seed.wallet, day=1)
    assert (above.reconciled, above.card) == (True, "transfer.unmatched_leg")


def test_the_next_withdrawal_is_linked_only_while_the_balance_covers_it(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    first = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    run(ws, _own_wallet(first, seed.address))
    node.native = 2 * BNB
    _run(app_engine, blob_store, ws, seed.wallet)

    # one more BNB left the exchange; the node still reads 2: nothing proves it arrived
    second = seed.withdrawal(
        "1.0005", fee="0.0005", tx_hash=None, network=None, at=SENT_AT + timedelta(days=3)
    )
    assert run(ws, _relink).written == 0
    card = _cards(read, ws)[second]
    assert card.paired_address_id == seed.address and card.own_wallets

    # the next read shows 3 BNB: the sync's relink writes and links it; a later run reconciles
    node.native = 3 * BNB
    _run(app_engine, blob_store, ws, seed.wallet, day=1)
    links = {r[0]: r for r in _links(dbname, ws)}
    assert links[second][2:] == ("matcher", "withdrawal_pair")
    assert sorted(r[4] for r in _deposits(dbname, seed.address)) == [Decimal("1"), Decimal("2")]
    after = _run(app_engine, blob_store, ws, seed.wallet, day=2)
    assert (after.reconciled, after.card) == (True, None)

    # two waiting withdrawals never pass on one balance: 3 + 1 + 1 > 4
    third = seed.withdrawal(
        "1", fee=None, tx_hash=None, network=None, at=SENT_AT + timedelta(days=4)
    )
    fourth = seed.withdrawal(
        "1", fee=None, tx_hash=None, network=None, at=SENT_AT + timedelta(days=5)
    )
    node.native = 4 * BNB
    _run(app_engine, blob_store, ws, seed.wallet, day=3)
    linked = {r[0] for r in _links(dbname, ws)}
    assert third in linked and fourth not in linked
    assert _cards(read, ws)[fourth].paired_address_id == seed.address


def test_release_ends_the_written_deposit_and_forgets_the_pair(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    """„Verknüpfung lösen“ on the person's pick: the deposit ends by a 0-unit successor
    (append-only), the withdrawal goes back to proposed (the pick had confirmed it), the pair
    is forgotten, the lots no longer hold the coins and the balance check counts nothing."""
    from hausbuch.services import transfers
    from hausbuch.services.chain.transfers import learned_links

    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    withdrawal = seed.withdrawal(
        "2.001", fee="0.001", tx_hash=None, network=None, status="proposed"
    )
    link = run(ws, _own_wallet(withdrawal, seed.address))
    assert read(ws, learned_links, [withdrawal]) == {
        withdrawal: link.id,
        link.in_movement_id: link.id,
    }

    run(ws, lambda ctx: transfers.release(ctx, link.id))
    assert _current(read, ws) == []
    assert _pairs(read, ws) == set()
    ((_, _, g_status, kind, quantity, status, _),) = _deposits(dbname, seed.address)
    assert (g_status, kind, quantity, status) == (
        "confirmed",
        "uncategorized",
        Decimal("0"),
        "confirmed",
    )
    assert _wallet_units(read, ws, seed.wallet) == []
    ((copy,),) = _q(
        dbname,
        "SELECT status FROM asset_movement WHERE id ="
        " (SELECT superseded_by FROM asset_movement WHERE id = %s)",
        withdrawal,
    )
    assert copy == "proposed"
    assert [c.own_wallets[0].address_id for c in _cards(read, ws).values()] == [seed.address]
    node.native = 0
    out = _run(app_engine, blob_store, ws, seed.wallet)
    assert (out.reconciled, out.card) == (True, None)
    assert _current(read, ws) == []  # nothing relinks it: the pair is forgotten


def test_a_re_booked_withdrawal_ends_its_written_deposit(dbname, fresh_workspaces, run, read):
    """The withdrawal answered otherwise (superseded): the link is no longer current, and the
    next relink ends the deposit it held, so no balance counts coins nothing proves."""
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    withdrawal = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    run(ws, _own_wallet(withdrawal, seed.address, remember=False))
    assert _pairs(read, ws) == set()
    with _admin(dbname) as conn:
        new = _insert(
            conn,
            "asset_movement",
            {
                "workspace_id": ws,
                "account_id": seed.exchange,
                "asset_id": seed.bnb,
                "kind": "uncategorized",
                "quantity": "-2.001",
                "occurred_at": SENT_AT,
                "source": "user",
                "status": "confirmed",
                "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            },
        )
        conn.execute(
            "UPDATE asset_movement SET superseded_by = %s WHERE id = %s", (new, withdrawal)
        )
    assert run(ws, _relink).ended == 1
    ((_, _, _, kind, quantity, _, _),) = _deposits(dbname, seed.address)
    assert (kind, quantity) == ("uncategorized", Decimal("0"))
    assert run(ws, _relink).ended == 0  # idempotent


def test_an_unknown_hash_is_offered_a_hash_still_to_look_up_is_not(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    unknown = seed.withdrawal(tx_hash=_hash("the node never saw it"))
    _run(app_engine, blob_store, ws, seed.wallet)  # asked a day later, in vain: remembered
    pending = seed.withdrawal(tx_hash=_hash("not asked yet"))

    cards = _cards(read, ws)
    assert pending not in cards  # the sync looks it up first (no dead-end card)
    assert [c.address_id for c in cards[unknown].own_wallets] == [seed.address]
    assert cards[unknown].paired_address_id is None  # a hashed one is never linked by a pair

    run(ws, _own_wallet(unknown, seed.address))
    ((g_hash, *_rest),) = _deposits(dbname, seed.address)
    assert g_hash == _hash("the node never saw it")  # held now: never looked up again


def test_a_pick_is_refused_where_it_does_not_apply(dbname, fresh_workspaces, run):
    from db.test_phase7_migration import EVM

    from hausbuch.services.errors import ConflictError, NotFoundError, RuleViolationError

    ws, other = fresh_workspaces
    seed = Seed(dbname, ws)
    theirs = Seed(dbname, other, address=OTHER)
    with _admin(dbname) as conn:
        eth = _address(conn, ws, seed.wallet, chain="ethereum", address=EVM)
    bison = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    hashed = seed.withdrawal(tx_hash=_hash("to be looked up"))
    their_bison = theirs.withdrawal(tx_hash=None, network=None)

    with pytest.raises(RuleViolationError):  # an address on another chain
        run(ws, _own_wallet(bison, eth))
    with pytest.raises(RuleViolationError):  # a hash the sync looks up first
        run(ws, _own_wallet(hashed, seed.address))
    with pytest.raises(NotFoundError):  # another workspace's address (row-level security)
        run(ws, _own_wallet(bison, theirs.address))
    with pytest.raises(NotFoundError):  # another workspace's withdrawal
        run(ws, _own_wallet(their_bison, seed.address))
    run(ws, _own_wallet(bison, seed.address))
    with pytest.raises(ConflictError):  # linked already
        run(ws, _own_wallet(bison, seed.address))
    assert len(_deposits(dbname, seed.address)) == 1


def test_a_released_pick_can_be_picked_again(dbname, fresh_workspaces, run, read):
    """A withdrawal confirmed before the pick keeps its id through the release: its card comes
    back, and a second pick writes a clean second deposit beside the ended one."""
    from hausbuch.services import transfers

    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    withdrawal = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    first = run(ws, _own_wallet(withdrawal, seed.address))
    run(ws, lambda ctx: transfers.release(ctx, first.id))
    assert _cards(read, ws)[withdrawal].own_wallets  # the card is back, on the same row

    second = run(ws, _own_wallet(withdrawal, seed.address))
    assert second.id != first.id and _current(read, ws) == [second.id]
    assert sorted(r[4] for r in _deposits(dbname, seed.address)) == [Decimal("0"), Decimal("2")]
    assert _wallet_units(read, ws, seed.wallet) == [Decimal("2")]


def test_an_unknown_hash_released_and_picked_again(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    from hausbuch.services import transfers

    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    withdrawal = seed.withdrawal(tx_hash=_hash("never mined"))
    _run(app_engine, blob_store, ws, seed.wallet)  # the node does not know it a day later
    first = run(ws, _own_wallet(withdrawal, seed.address))
    run(ws, lambda ctx: transfers.release(ctx, first.id))
    assert _cards(read, ws)[withdrawal].own_wallets
    run(ws, _own_wallet(withdrawal, seed.address))
    live = [r for r in _deposits(dbname, seed.address) if r[4] != 0]
    assert [(r[0], r[4]) for r in live] == [(_hash("never mined"), Decimal("2"))]


def test_a_balance_read_before_the_withdrawal_never_links_it(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    """The balance must have been read after the withdrawal left the exchange: an older read
    that happens to cover the amount proves nothing about it."""
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    first = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    run(ws, _own_wallet(first, seed.address))
    node.native = 5 * BNB
    _run(app_engine, blob_store, ws, seed.wallet)  # read on NOW
    later = seed.withdrawal("1", fee=None, tx_hash=None, network=None, at=NOW + timedelta(days=1))
    assert run(ws, _relink).written == 0
    assert later not in {r[0] for r in _links(dbname, ws)}
    _run(app_engine, blob_store, ws, seed.wallet, day=2)  # read after it: now it counts
    assert later in {r[0] for r in _links(dbname, ws)}


def test_an_exchange_paired_with_two_addresses_links_nothing_by_code(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    with _admin(dbname) as conn:
        from db.test_phase7_migration import _wallet

        other_wallet = _wallet(conn, ws, label="Zweite Wallet")
        second = _address(conn, ws, other_wallet, chain="bsc", address=OTHER)
    one = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    two = seed.withdrawal(
        "1.001", fee="0.001", tx_hash=None, network=None, at=SENT_AT + timedelta(days=1)
    )
    run(ws, _own_wallet(one, seed.address))
    run(ws, _own_wallet(two, second))
    assert len(_pairs(read, ws)) == 2
    third = seed.withdrawal(
        "1", fee=None, tx_hash=None, network=None, at=SENT_AT + timedelta(days=2)
    )
    node.native = 10 * BNB
    _run(app_engine, blob_store, ws, seed.wallet)
    assert third not in {r[0] for r in _links(dbname, ws)}
    card = _cards(read, ws)[third]
    assert card.paired_address_id is None and len(card.own_wallets) == 2


def test_releasing_a_link_code_wrote_ends_its_deposit(
    dbname, app_engine, blob_store, fresh_workspaces, node, run, read
):
    from hausbuch.services import transfers

    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    first = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    run(ws, _own_wallet(first, seed.address))
    second = seed.withdrawal(
        "1", fee=None, tx_hash=None, network=None, at=SENT_AT + timedelta(days=1)
    )
    node.native = 3 * BNB
    _run(app_engine, blob_store, ws, seed.wallet)
    (coded,) = [r for r in _links(dbname, ws) if r[0] == second]
    assert coded[2:] == ("matcher", "withdrawal_pair")
    link_id = read(ws, lambda conn: {r.out_movement_id: r.id for r in _current_rows(conn)})[second]

    run(ws, lambda ctx: transfers.release(ctx, link_id))
    assert _pairs(read, ws) == set()
    assert sorted(r[4] for r in _deposits(dbname, seed.address)) == [Decimal("0"), Decimal("2")]
    assert _wallet_units(read, ws, seed.wallet) == [Decimal("2")]
    assert len(_current(read, ws)) == 1  # the person's pick stands


def _current_rows(conn):
    from hausbuch.services.transfers import _current_links

    return _current_links(conn)
