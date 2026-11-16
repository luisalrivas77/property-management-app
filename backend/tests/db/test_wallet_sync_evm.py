"""Phase 7 step 3, the EVM family in the sync loop (docs/phases/phase-7.md D181 (b), (c), D182,
D186, D192): services/chain/sync over the REAL Etherscan and BSC sources (their
`make_source`, the EVM `classify_tx`) with only the HTTP faked, in Etherscan V2's and the
JSON-RPC's own shapes.

One address added on Ethereum, Arbitrum and BNB Smart Chain (`also_on`), as the wallet page
does. The Etherscan rows are evm_tx.yaml's recorded cases (invented addresses, hashes and
amounts; ME is that file's own address), plus two invented rows of this file: a funding
receive before them and an approval after. Every host is under `.invalid`.
"""

import functools
import hashlib
import itertools
import json
import logging
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY, _admin, _insert
from db.test_phase6_migration import _movement
from db.test_phase7_migration import _chain_movement, _group_row
from fixture_models_phase7 import load
from pydantic import SecretStr

from hausbuch.api.routes.wallet_sync import list_wallet_syncs
from hausbuch.db.session import workspace_transaction
from hausbuch.rules import errors as E
from hausbuch.rules.chain import evm
from hausbuch.rules.chain.classify import ClassifyContext
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.chain import Chain, ChainSourceKind, ChainTx, WalletSyncStatus
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.schemas.wallets import WalletAddressBody
from hausbuch.services import wallets as wallet_svc
from hausbuch.services.accounts import create_account
from hausbuch.services.chain import evm_etherscan, evm_rpc_balance
from hausbuch.services.chain.base import RequestBudget, register_source
from hausbuch.services.chain.sync import sync_wallets
from hausbuch.services.context import Actor, new_command
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

CASES = {c.id: c for c in load("evm_tx").cases}
ME = CASES["native_receive"].own[0]
FOREIGN = "0x96d82db02c1fdeb860fcb6c58f11625348b9b831"  # evm_tx.yaml's foreign address
APPROVED = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"  # USDC, the approval's target
KEY = "INVENTEDKEY0123456789ABCDEFGHIJKL"
URL_A = "https://explorer-a.invalid/v2/api"
URL_B = "https://explorer-b.invalid/api"
RPC_URL = "https://bsc-node.invalid/v1/INVENTEDPATHKEY"
ACTOR = Actor(ActorType.SYSTEM, "sync_wallets")
T0 = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ETH_ID, ARB_ID = 1, 42161
ETH_TIP, ARB_TIP, BSC_TIP = 21_000_900, 300_000_200, 50_000_100
FUNDING = 2 * 10**18
BNB = 10**18


def _settings(**overrides) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
        **(
            {
                "etherscan_key": SecretStr(KEY),
                "etherscan_url": URL_A,
                "bsc_rpc_url": SecretStr(RPC_URL),
            }
            | overrides
        ),
    )


def _hash(text: str) -> str:
    return "0x" + hashlib.sha256(text.encode()).hexdigest()


def _rows(case_ids, *extra) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for case_id in case_ids:
        for action, rows in CASES[case_id].records.items():
            out.setdefault(action, []).extend(rows)  # type: ignore[arg-type]
    for action, row in extra:
        out.setdefault(action, []).append(row)
    return out


FUND_ROW = {
    "hash": _hash("invented funding"),
    "blockNumber": "21000050",
    "timeStamp": "1772900000",
    "from": FOREIGN,
    "to": ME,
    "value": str(FUNDING),
    "gasUsed": "21000",
    "gasPrice": "20000000000",
    "isError": "0",
    "input": "0x",
}
ETH_CASES = (
    "native_receive",
    "native_send_with_fee",
    "failed_fee_only",
    "erc20_in",
    "erc20_out",
    "spam_token",
    "wrap_weth",
)


def _fee(case_id: str) -> int:
    (row,) = CASES[case_id].records["txlist"]  # type: ignore[misc]
    return int(row["gasUsed"]) * int(row["gasPrice"])  # type: ignore[index]


ETH_BALANCE = (
    FUNDING
    + 500_000_000_000_000_000
    - 200_000_000_000_000_000
    - 10**18  # wrapped into WETH
    - sum(_fee(c) for c in ("native_send_with_fee", "failed_fee_only", "erc20_out", "wrap_weth"))
)
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
SPAM = "0x8a3b8db704d0375052d72f723674f80f48649e42"


class FakeEtherscan:
    """Answers Etherscan V2 by `chainid`: the five lists filtered to the asked block range,
    the native balance, `eth_blockNumber` and a token's `balanceOf` by `eth_call`."""

    def __init__(self) -> None:
        self.chains: dict[int, dict] = {}
        self.calls: list[tuple[str, int, str]] = []  # (host, chainid, action)

    def chain(self, chain_id: int, *, tip: int, rows, native: int, tokens=None) -> None:
        self.chains[chain_id] = {"tip": tip, "rows": rows, "native": native, "tokens": tokens or {}}

    def __call__(self, url, headers, timeout, max_bytes):
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        assert q["apikey"] == KEY
        chain_id = int(q["chainid"])
        self.calls.append((parts.hostname, chain_id, q["action"]))
        c = self.chains[chain_id]
        action = q["action"]
        if action == "eth_blockNumber":
            return self._proxy(hex(c["tip"]))
        if action == "eth_call":
            assert int(q["tag"], 16) == c["tip"] - 12
            if q["to"] in c.get("revert", ()):  # a contract whose `balanceOf` reverts
                message = c.get("reason", "execution reverted")
                error = {"code": c.get("revert_code", 3), "message": message}
                return 200, json.dumps({"jsonrpc": "2.0", "id": 1, "error": error}).encode()
            return self._proxy(hex(c["tokens"].get(q["to"], 0)))
        if action == "balance":
            assert int(q["tag"], 16) == c["tip"] - 12
            return 200, json.dumps(
                {"status": "1", "message": "OK", "result": str(c["native"])}
            ).encode()
        start, end = int(q["startblock"]), int(q["endblock"])
        rows = [r for r in c["rows"].get(action, []) if start <= int(r["blockNumber"]) <= end]
        if not rows:
            body = {"status": "0", "message": "No transactions found", "result": []}
        else:
            body = {"status": "1", "message": "OK", "result": rows}
        return 200, json.dumps(body).encode()

    @staticmethod
    def _proxy(result: str):
        return 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


class FakeRpc:
    """BNB Smart Chain's JSON-RPC: the tip, the native balance and every `balanceOf` (0)."""

    def __init__(self, native: int) -> None:
        self.native = native
        self.urls: list[str] = []
        self.fail = None  # a callable(payload) -> (status, body): the node refuses

    def __call__(self, url, headers, body, timeout, max_bytes):
        self.urls.append(url)
        payload = json.loads(body)
        if self.fail is not None:
            return self.fail(payload)
        result = {
            "eth_blockNumber": hex(BSC_TIP),
            "eth_getBalance": hex(self.native),
            "eth_call": "0x0",
        }[payload["method"]]
        if payload["method"] == "eth_getBalance":
            assert payload["params"] == [ME, hex(BSC_TIP - 12)]
        return 200, json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()


@pytest.fixture
def http():
    """The real factories with fake transports (no sleep between calls)."""
    scan, rpc = FakeEtherscan(), FakeRpc(native=0)
    scan.chain(
        ETH_ID,
        tip=ETH_TIP,
        rows=_rows(ETH_CASES, ("txlist", FUND_ROW)),
        native=ETH_BALANCE,
        tokens={USDC: 150_000_000, WETH: 10**18},
    )
    scan.chain(ARB_ID, tip=ARB_TIP, rows=_rows(["internal_in"]), native=30_000_000_000_000_000)
    no_sleep = {"sleep": lambda s: None}
    register_source(
        ChainSourceKind.EVM_ETHERSCAN,
        functools.partial(evm_etherscan.make_source, fetch=scan, **no_sleep),
    )
    register_source(
        ChainSourceKind.EVM_RPC_BALANCE,
        functools.partial(evm_rpc_balance.make_source, post=rpc, **no_sleep),
    )
    yield scan, rpc
    register_source(ChainSourceKind.EVM_ETHERSCAN, None)
    register_source(ChainSourceKind.EVM_RPC_BALANCE, None)


def _wallet(app_engine, ws) -> tuple[uuid.UUID, dict[Chain, uuid.UUID]]:
    """A wallet with ME on Ethereum, Arbitrum and BSC, added once with `also_on`."""
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.USER, "user:test"), now=NOW)
        account = create_account(
            ctx,
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
        created = wallet_svc.add_address(
            ctx,
            account,
            WalletAddressBody(
                chain=Chain.ETHEREUM, address=ME, also_on=[Chain.ARBITRUM, Chain.BSC]
            ),
            key=TEST_IBAN_KEY,
        )
    return account, {a.chain: a.id for a in created}


def _run(app_engine, blob_store, ws, account, *, day=1, settings=None, budgets=None):
    ticks = itertools.count()
    outcomes = sync_wallets(
        app_engine,
        ws,
        settings=settings or _settings(),
        store=blob_store,
        actor=ACTOR,
        clock=lambda: T0 + timedelta(days=day, seconds=next(ticks)),
        account_id=account,
        budgets=budgets,
    )
    return {o.chain: o for o in outcomes}


def _q(dbname, query, *params):
    with _admin(dbname) as conn:
        return conn.execute(query, params).fetchall()


def _groups(dbname, address_id):
    """(tx_hash, kind, status, [(index, kind, quantity, status)]) per group of the address."""
    rows = _q(
        dbname,
        "SELECT g.tx_hash, g.kind, g.status, m.index, m.kind, m.quantity, m.status"
        " FROM movement_group g JOIN asset_movement m ON m.group_id = g.id"
        " WHERE m.address_id = %s AND m.superseded_by IS NULL ORDER BY g.tx_hash, m.index",
        address_id,
    )
    out: dict[str, tuple] = {}
    for tx_hash, kind, status, index, m_kind, quantity, m_status in rows:
        entry = out.setdefault(tx_hash, (kind, status, []))
        entry[2].append((index, m_kind, quantity.normalize(), m_status))
    return out


def _sync_row(dbname, address_id):
    (row,) = _q(
        dbname,
        "SELECT status, cursor_before, cursor_after, txs_seen, movements_new, reconciled,"
        " balances, difference, document_id, error_code FROM wallet_sync"
        " WHERE address_id = %s ORDER BY started_at DESC LIMIT 1",
        address_id,
    )
    return row


def _original_lines(dbname, blob_store, address_id) -> list[dict]:
    (key,) = _q(
        dbname,
        "SELECT f.storage_key FROM wallet_sync s JOIN document d ON d.id = s.document_id"
        " JOIN document_file f ON f.workspace_id = d.workspace_id AND f.sha256 = d.file_sha256"
        " WHERE s.address_id = %s AND s.document_id IS NOT NULL",
        address_id,
    )[0]
    return [json.loads(line) for line in blob_store.get(key).decode().splitlines()]


# --- Ethereum and Arbitrum -----------------------------------------------------------------


def test_one_address_on_ethereum_and_arbitrum_is_read_booked_and_reconciled(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    scan, _ = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)

    first = _run(app_engine, blob_store, ws, account)
    eth, arb = first[Chain.ETHEREUM], first[Chain.ARBITRUM]
    assert (eth.status, eth.reconciled, eth.deferred) == (WalletSyncStatus.SUCCEEDED, True, False)
    assert (arb.status, arb.reconciled, arb.deferred) == (WalletSyncStatus.SUCCEEDED, True, False)
    # the five lists were asked once per chain (the run's second page short-circuits at the
    # pinned end), so no row is read or stored twice
    for chain_id in (ETH_ID, ARB_ID):
        lists = [a for _, c, a in scan.calls if c == chain_id and a in evm.ACTIONS]
        assert sorted(lists) == sorted(evm.ACTIONS)
    assert {c for _, c, _ in scan.calls} == {ETH_ID, ARB_ID}
    assert {h for h, _, _ in scan.calls} == {"explorer-a.invalid"}
    assert eth.txs_seen == len(ETH_CASES) + 1 and arb.txs_seen == 1
    assert len(_original_lines(dbname, blob_store, ids[Chain.ETHEREUM])) == eth.txs_seen

    row = _sync_row(dbname, ids[Chain.ETHEREUM])
    assert row[0] == "succeeded" and row[1] is None and row[2] == str(ETH_TIP - 12)
    assert row[5] is True and row[7] is None and row[9] is None
    assert row[6]["native"] == str(ETH_BALANCE)
    assert row[6][USDC] == "150000000" and row[6][WETH] == str(10**18)
    assert SPAM not in row[6]  # a spam token's balance is not asked (D187 (1))
    arb_row = _sync_row(dbname, ids[Chain.ARBITRUM])
    assert arb_row[2] == str(ARB_TIP - 12) and arb_row[6]["native"] == "30000000000000000"

    groups = _groups(dbname, ids[Chain.ETHEREUM])
    by_case = {CASES[c].records[next(iter(CASES[c].records))][0]["hash"]: c for c in ETH_CASES}  # type: ignore[index]
    kinds = {by_case.get(h, "funding"): (g[0], g[1]) for h, g in groups.items()}
    # the address's first sync: a person reads each value group once; a failed one, a spam
    # one and a fee alone are confirmed by code (D186 (1))
    assert kinds == {
        "funding": ("transfer", "proposed"),
        "native_receive": ("transfer", "proposed"),
        "native_send_with_fee": ("transfer", "proposed"),
        "failed_fee_only": ("failed", "confirmed"),
        "erc20_in": ("transfer", "proposed"),
        "erc20_out": ("transfer", "proposed"),
        "spam_token": ("spam", "confirmed"),
        "wrap_weth": ("wrap", "proposed"),
    }
    for case_id in ETH_CASES:  # each leg as evm_tx.yaml expects it
        tx_hash = CASES[case_id].records[next(iter(CASES[case_id].records))][0]["hash"]  # type: ignore[index]
        got = {(m[0], m[1]) for m in groups[tx_hash][2]}
        want = {(str(m.index), m.kind.value) for m in CASES[case_id].expect.movements}
        assert got == want, case_id
    # the unlisted spam token kept its 18 decimals: 10^21 units are 1000 tokens, stored
    spam_hash = CASES["spam_token"].records["tokentx"][0]["hash"]  # type: ignore[index]
    assert groups[spam_hash][2] == [("3", "spam", Decimal("1000"), "confirmed")]
    (asset,) = _q(
        dbname,
        "SELECT symbol, decimals, spam, name FROM asset WHERE workspace_id = %s"
        " AND contract_address = %s",
        ws,
        SPAM,
    )
    assert asset[:3] == (None, 18, True) and "nennt sich CLAIM" in asset[3]
    (arb_group,) = _groups(dbname, ids[Chain.ARBITRUM]).values()
    assert arb_group[:2] == ("transfer", "proposed")
    assert arb_group[2] == [("internal:0_1", "uncategorized", Decimal("0.03"), "proposed")]

    # the next day: the chain moved on, an approval (a fee alone) is confirmed by code now
    # that the history was read once, and a new receive still waits (no link yet, D184 (1))
    approval = {
        "hash": _hash("invented approval"),
        "blockNumber": "21000950",
        "timeStamp": "1773800000",
        "from": ME,
        "to": APPROVED,
        "value": "0",
        "gasUsed": "46000",
        "gasPrice": "10000000000",
        "isError": "0",
        "input": "0x095ea7b3",
    }
    receive = FUND_ROW | {
        "hash": _hash("invented second receive"),
        "blockNumber": "21000960",
        "value": "1000",
    }
    eth_chain = scan.chains[ETH_ID]
    eth_chain["rows"]["txlist"] += [approval, receive]
    eth_chain["tip"] = ETH_TIP + 100
    eth_chain["native"] = ETH_BALANCE - 46000 * 10_000_000_000 + 1000
    second = _run(app_engine, blob_store, ws, account, day=2)
    eth2 = second[Chain.ETHEREUM]
    assert (eth2.reconciled, eth2.movements_new, eth2.txs_seen) == (True, 2, 2)
    row = _sync_row(dbname, ids[Chain.ETHEREUM])
    assert (row[1], row[2]) == (str(ETH_TIP - 12), str(ETH_TIP + 100 - 12))
    groups = _groups(dbname, ids[Chain.ETHEREUM])
    assert groups[approval["hash"]][:2] == ("contract_call", "confirmed")
    assert groups[receive["hash"]][:2] == ("transfer", "proposed")
    assert second[Chain.ARBITRUM].movements_new == 0


def test_a_wrong_balance_on_a_first_sync_is_a_mismatch_not_a_wait(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    """An EVM group asks no question (its legs are all booked), so a first sync's proposed
    groups never make a difference read as „wartet auf Antwort“."""
    scan, _ = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    scan.chains[ETH_ID]["native"] = ETH_BALANCE + 1
    eth = _run(app_engine, blob_store, ws, account)[Chain.ETHEREUM]
    assert (eth.reconciled, eth.waiting) == (False, False)
    assert _sync_row(dbname, ids[Chain.ETHEREUM])[7] == {"native": "1"}


def test_a_spent_budget_defers_the_window_without_a_half_read(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    """A window is read whole across the five lists or not at all: a budget that ends in it
    returns the cursor unmoved, the run is deferred, and the next run reads it whole."""
    scan, _ = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    budgets = {ChainSourceKind.EVM_ETHERSCAN: RequestBudget(3)}  # the tip and two lists
    first = _run(app_engine, blob_store, ws, account, budgets=budgets)
    # the first of the two Etherscan addresses spends the budget inside its window; the
    # other is never reached (no row), whichever ran first
    reached = [first[c] for c in (Chain.ETHEREUM, Chain.ARBITRUM) if first[c].sync_id]
    (ran,) = reached
    other = first[Chain.ARBITRUM if ran.chain is Chain.ETHEREUM else Chain.ETHEREUM]
    assert (ran.deferred, ran.txs_seen, ran.movements_new, ran.reconciled) == (True, 0, 0, None)
    assert (other.sync_id, other.error_code) == (None, E.WALLET_SYNC_DEFERRED)
    row = _sync_row(dbname, ids[ran.chain])
    assert (row[0], row[2], row[6]) == ("succeeded", None, None)  # the cursor unmoved
    assert first[Chain.BSC].status is WalletSyncStatus.SUCCEEDED  # its own budget

    second = _run(app_engine, blob_store, ws, account, day=2)
    assert second[Chain.ETHEREUM].reconciled is True
    assert second[Chain.ETHEREUM].txs_seen == len(ETH_CASES) + 1
    assert second[Chain.ARBITRUM].reconciled is True


def test_an_unset_key_skips_ethereum_and_arbitrum_and_bsc_still_syncs(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    scan, rpc = http
    rpc.native = BNB
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    run = _run(app_engine, blob_store, ws, account, settings=_settings(etherscan_key=None))
    for chain in (Chain.ETHEREUM, Chain.ARBITRUM):
        assert (run[chain].sync_id, run[chain].status, run[chain].error_code) == (
            None,
            None,
            E.WALLET_KEY_MISSING,
        )
    assert scan.calls == []  # nothing sent without a key
    assert run[Chain.BSC].status is WalletSyncStatus.SUCCEEDED
    counts = _q(
        dbname,
        "SELECT chain, count(*) FROM wallet_sync s JOIN wallet_address a"
        " ON a.id = s.address_id WHERE s.account_id = %s GROUP BY chain",
        account,
    )
    assert dict(counts) == {"bsc": 1}


# --- BNB Smart Chain, balance only (D181 (c)) ----------------------------------------------


def _deposit(dbname, ws, account, address_id, units: int, *, contract=None):
    """A deposit linked to the BSC address, as step 5 will write it from the exchange's
    withdrawal (seeded here: step 5's lookup by hash is not built)."""
    with _admin(dbname) as conn:
        if contract is None:
            asset_id = conn.execute(
                "SELECT id FROM asset WHERE workspace_id = %s AND chain = 'bsc'"
                " AND contract_address IS NULL",
                (ws,),
            ).fetchone()[0]
        else:
            asset_id = _insert(
                conn,
                "asset",
                {
                    "workspace_id": ws,
                    "kind": "crypto",
                    "name": "Token (unlisted)",
                    "chain": "bsc",
                    "contract_address": contract,
                    "decimals": 18,
                },
            )
        tx_hash = _hash(f"invented deposit {uuid.uuid4()}")
        group = _insert(
            conn,
            "movement_group",
            _group_row(ws, account, chain="bsc", tx_hash=tx_hash, kind="exchange_transfer")
            | {"status": "confirmed"},
        )
        _insert(
            conn,
            "asset_movement",
            _chain_movement(
                ws,
                account,
                asset_id,
                address_id,
                group_id=group,
                quantity=str(Decimal(units).scaleb(-18)),
                network="bsc",
                tx_hash=tx_hash,
                status="confirmed",
                index="value",
            ),
        )


def test_bsc_is_reconciled_against_its_linked_deposits(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    """wallet_reconcile.yaml's balance-only cases through the sync: no deposit yet and a
    balance → reconciled with transfer.unmatched_leg; Σ deposits above the balance →
    wallet.coins_left, stored as reconciled false with the negative shortfall; equal →
    reconciled, no card. A deposit in an asset the source reads no balance of is skipped."""
    scan, rpc = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    bsc_id = ids[Chain.BSC]

    rpc.native = 5 * BNB // 10
    first = _run(app_engine, blob_store, ws, account)[Chain.BSC]
    assert (first.status, first.reconciled, first.card) == (
        WalletSyncStatus.SUCCEEDED,
        True,
        E.TRANSFER_UNMATCHED_LEG,
    )
    row = _sync_row(dbname, bsc_id)
    assert (row[2], row[3], row[4], row[5], row[7], row[8]) == (None, 0, 0, True, None, None)
    listed = {t.contract for t in asset_registry().tokens if t.chain is Chain.BSC}
    assert set(row[6]) == {"native"} | listed
    assert {u for u in rpc.urls} == {RPC_URL}

    _deposit(dbname, ws, account, bsc_id, 6 * BNB // 10)
    below = _run(app_engine, blob_store, ws, account, day=2)[Chain.BSC]
    assert (below.reconciled, below.card) == (False, E.WALLET_COINS_LEFT)
    assert _sync_row(dbname, bsc_id)[7] == {"native": str(-(BNB // 10))}

    rpc.native = 6 * BNB // 10
    _deposit(dbname, ws, account, bsc_id, 10**18, contract="0x" + "ab" * 20)  # no balance row
    equal = _run(app_engine, blob_store, ws, account, day=3)[Chain.BSC]
    assert (equal.reconciled, equal.card) == (True, None)
    assert _sync_row(dbname, bsc_id)[7] is None


def _rpc_error(payload):
    error = {"code": -32000, "message": "invented node failure"}
    return 200, json.dumps({"jsonrpc": "2.0", "id": payload["id"], "error": error}).encode()


@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (lambda payload: (403, b""), "refused", E.WALLET_SOURCE_REFUSED),
        (lambda payload: (429, b""), "throttled", E.WALLET_SOURCE_THROTTLED),
        (_rpc_error, "failed", E.WALLET_SOURCE_FAILED),
    ],
    ids=["http403", "http429", "rpc_error"],
)
def test_a_bsc_node_that_refuses_every_call_is_a_failed_row_each_day(
    dbname, app_engine, blob_store, fresh_workspaces, http, answer, status, code
):
    """Review of PR #397: a balance-only address reads no history, so a node refusing every
    call stores nothing new. Each day is a refused, throttled or failed row with its code,
    on the row, on GET /wallets/{id}/syncs and on the wallet page's last_sync, never a
    succeeded row with no balance that reads deferred."""
    _, rpc = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    bsc_id = ids[Chain.BSC]
    rpc.fail = answer
    for day in (1, 2):
        out = _run(app_engine, blob_store, ws, account, day=day)[Chain.BSC]
        assert (out.status, out.error_code, out.deferred, out.reconciled) == (
            WalletSyncStatus(status),
            code,
            False,
            None,
        )
        row = _sync_row(dbname, bsc_id)
        assert (row[0], row[2], row[5], row[6], row[9]) == (status, None, None, None, code)
    with workspace_transaction(app_engine, ws) as conn:
        runs = [r for r in list_wallet_syncs(conn, account) if r.address_id == bsc_id]
        page = wallet_svc.wallet_page(conn, account)
    assert len(runs) == 2
    assert {(r.status.value, r.error_code, r.deferred) for r in runs} == {(status, code, False)}
    (last,) = [a.last_sync for a in page.addresses if a.id == bsc_id]
    assert (last.error_code, last.reconciled) == (code, None)

    rpc.fail = None  # the node answers again: the address reconciles
    rpc.native = 0
    back = _run(app_engine, blob_store, ws, account, day=3)[Chain.BSC]
    assert (back.status, back.error_code, back.reconciled) == (
        WalletSyncStatus.SUCCEEDED,
        None,
        True,
    )


def test_bsc_settings_url_is_used_and_only_its_host_recorded(
    dbname, app_engine, blob_store, fresh_workspaces, http, caplog
):
    _, rpc = http
    ws, _ = fresh_workspaces
    account, _ = _wallet(app_engine, ws)
    with caplog.at_level(logging.DEBUG):
        _run(app_engine, blob_store, ws, account)
    logged = caplog.text + "".join(repr(getattr(r, "fields", "")) for r in caplog.records)
    assert sum(getattr(r, "fields", {}).get("event") == "wallet_sync" for r in caplog.records) == 3
    assert "INVENTEDPATHKEY" not in logged and KEY not in logged
    assert set(rpc.urls) == {RPC_URL}
    hosts = _q(
        dbname,
        "SELECT payload->>'source_host' FROM event WHERE workspace_id = %s"
        " AND type = 'wallet.sync_completed'",
        ws,
    )
    assert {h[0] for h in hosts} == {"explorer-a.invalid", "bsc-node.invalid"}
    texts = _q(dbname, "SELECT payload::text FROM event WHERE workspace_id = %s", ws)
    assert not any("INVENTEDPATHKEY" in t[0] or KEY in t[0] for t in texts)


# --- D192: the provider is replaceable ------------------------------------------------------


def test_two_base_urls_give_the_same_movements_and_a_replay_needs_no_source(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    """chains.yaml's `providers` names no EVM case yet (a spec PR adds one); this is its
    test in plain code: the same records through two base URLs give the same movements, and
    the stored original, normalised and classified again with no source, gives the stored
    legs."""
    scan, _ = http
    a, b = fresh_workspaces
    account_a, ids_a = _wallet(app_engine, a)
    account_b, ids_b = _wallet(app_engine, b)
    _run(app_engine, blob_store, a, account_a, settings=_settings(etherscan_url=URL_A))
    _run(app_engine, blob_store, b, account_b, settings=_settings(etherscan_url=URL_B))
    assert {h for h, _, _ in scan.calls} == {"explorer-a.invalid", "explorer-b.invalid"}

    def strip(groups):
        return {h: (k, s, legs) for h, (k, s, legs) in groups.items()}

    for chain in (Chain.ETHEREUM, Chain.ARBITRUM):
        assert strip(_groups(dbname, ids_a[chain])) == strip(_groups(dbname, ids_b[chain]))

    # the replay: no source registered, only the stored NDJSON
    register_source(ChainSourceKind.EVM_ETHERSCAN, None)
    for chain in (Chain.ETHEREUM, Chain.ARBITRUM):
        stored = _groups(dbname, ids_a[chain])
        ctx = ClassifyContext(chain=chain, address=ME, own_addresses=frozenset({ME}))
        replayed: dict[str, list] = {}
        for raw in _original_lines(dbname, blob_store, ids_a[chain]):
            (tx,) = evm.normalise(chain, raw, ME)
            assert isinstance(tx, ChainTx)
            group = evm.classify_tx(tx, ctx)
            if group is not None:
                replayed[group.tx_hash] = sorted((leg.index, leg.kind.value) for leg in group.legs)
        assert replayed == {
            h: sorted((m[0], m[1]) for m in legs) for h, (_, _, legs) in stored.items()
        }


# --- a token an exchange sent (D183 (3)(b), review finding 1) -----------------------------


def _exchange_withdrawals(app_engine, dbname, ws, *tx_hashes: str) -> None:
    """An own exchange's crypto withdrawals, each naming one of `tx_hashes`, as an import
    stores them."""
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.USER, "user:test"), now=NOW)
        exchange = create_account(
            ctx,
            AccountCreate(
                label="Börse",
                bank="BISON",
                kind=AccountKind.EXCHANGE,
                provider="BISON",
                opening_balance=Decimal("0.00"),
                opening_balance_on=datetime(2026, 1, 1).date(),
            ),
            iban_key=TEST_IBAN_KEY,
        ).id
    with _admin(dbname) as conn:
        coin = _insert(
            conn, "asset", {"workspace_id": ws, "kind": "crypto", "symbol": "XTK", "name": "X"}
        )
        for tx_hash in tx_hashes:
            _insert(
                conn,
                "asset_movement",
                _movement(
                    ws,
                    exchange,
                    coin,
                    kind="internal_transfer_out",
                    quantity="-40.0000000000",
                    eur_amount=None,
                    source="csv",
                    network="ETH",
                    tx_hash=tx_hash,
                ),
            )


def test_an_unlisted_token_an_exchange_sent_is_an_exchange_transfer_not_spam(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    """An unlisted ERC-20 credited by a transaction an own exchange withdrawal names is an
    `exchange_transfer` (D183 (3)(b), before the spam test): proposed until step 5 links it,
    its asset not spam. Its twin, the same shape with no withdrawal naming it, stays spam,
    confirmed and hidden. The hash is in no event."""
    scan, _ = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    sent, twin = "0x" + "e1" * 20, "0x" + "e2" * 20
    sent_hash, twin_hash = _hash("invented exchange withdrawal"), _hash("invented twin")
    for contract, tx_hash, block in ((sent, sent_hash, "21000860"), (twin, twin_hash, "21000861")):
        scan.chains[ETH_ID]["rows"]["tokentx"].append(
            {
                "hash": tx_hash,
                "blockNumber": block,
                "timeStamp": "1773760000",
                "from": "0xb70f11bb61b8bc94b873e7bc2b1f47eea34a158a",  # the exchange's hot wallet
                "to": ME,
                "contractAddress": contract,
                "tokenSymbol": "XTK",
                "tokenDecimal": "18",
                "value": str(40 * 10**18),
                "logIndex": "2",
            }
        )
    native_hash = _hash("invented native withdrawal")
    scan.chains[ETH_ID]["rows"]["txlist"].append(
        {
            "hash": native_hash,
            "blockNumber": "21000862",
            "timeStamp": "1773770000",
            "from": "0xb70f11bb61b8bc94b873e7bc2b1f47eea34a158a",
            "to": ME,
            "value": "299500000000000000",
            "gasUsed": "21000",
            "gasPrice": "15000000000",
            "isError": "0",
            "input": "0x",
        }
    )
    scan.chains[ETH_ID]["native"] += 299_500_000_000_000_000
    scan.chains[ETH_ID]["tokens"][sent] = 40 * 10**18
    # stored as the export spelled it (upper-case hex): the classifier compares lower-cased
    _exchange_withdrawals(
        app_engine, dbname, ws, sent_hash.upper().replace("0X", "0x"), native_hash
    )

    eth = _run(app_engine, blob_store, ws, account)[Chain.ETHEREUM]
    # finding 1 was also "left out of the reconciliation": the token's balance is read
    assert eth.reconciled is True

    groups = _groups(dbname, ids[Chain.ETHEREUM])
    kind, status, legs = groups[sent_hash]
    assert (kind, status) == ("exchange_transfer", "proposed")
    assert [(i, k) for i, k, _, _ in legs] == [("2", "internal_transfer_in")]
    # the native coin too: an exchange_transfer, not a transfer with an unmatched leg, and
    # proposed like every EVM group until step 5 links it (never confirmed by the hash alone)
    kind, status, legs = groups[native_hash]
    assert (kind, status) == ("exchange_transfer", "proposed")
    assert [(i, k) for i, k, _, _ in legs] == [("value", "internal_transfer_in")]
    kind, status, legs = groups[twin_hash]
    assert (kind, status) == ("spam", "confirmed")
    assert [(i, k) for i, k, _, _ in legs] == [("2", "spam")]
    spam = dict(_q(dbname, "SELECT contract_address, spam FROM asset WHERE workspace_id = %s", ws))
    assert spam[sent] is False
    assert spam[twin] is True
    events = _q(dbname, "SELECT payload::text FROM event WHERE workspace_id = %s", ws)
    hashes = (sent_hash[2:], twin_hash[2:], native_hash[2:])
    assert not [p for (p,) in events if any(h in p.lower() for h in hashes)]


# --- an unlisted token reconciles (review finding 3, option (a)) ---------------------------


def test_a_claimed_unlisted_token_reconciles(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    """A non-spam token the registry does not list (an airdrop the address signed for) has
    its `balanceOf` read in the same run as its first movement, and again on the next run
    from the stored movements, so the address reconciles both times."""
    scan, _ = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    token = "0x" + "cd" * 20
    claim = {
        "hash": _hash("invented claim"),
        "blockNumber": "21000850",
        "timeStamp": "1773750000",
        "from": ME,
        "to": token,
        "value": "0",
        "gasUsed": "50000",
        "gasPrice": "10000000000",
        "isError": "0",
        "input": "0x4e71d92d",
    }
    credited = {
        "hash": claim["hash"],
        "blockNumber": "21000850",
        "timeStamp": "1773750000",
        "from": "0x" + "0" * 40,
        "to": ME,
        "contractAddress": token,
        "tokenSymbol": "CLM",
        "tokenDecimal": "18",
        "value": str(5 * 10**18),
        "logIndex": "1",
    }
    eth_chain = scan.chains[ETH_ID]
    eth_chain["rows"]["txlist"].append(claim)
    eth_chain["rows"]["tokentx"].append(credited)
    eth_chain["native"] = ETH_BALANCE - 50000 * 10_000_000_000
    eth_chain["tokens"][token] = 5 * 10**18
    eth = _run(app_engine, blob_store, ws, account)[Chain.ETHEREUM]
    assert eth.reconciled is True
    assert _sync_row(dbname, ids[Chain.ETHEREUM])[6][token] == str(5 * 10**18)

    # the next run: nothing new, the token's balance read again from its stored movement
    again = _run(app_engine, blob_store, ws, account, day=2)[Chain.ETHEREUM]
    assert (again.reconciled, again.movements_new) == (True, 0)
    assert _sync_row(dbname, ids[Chain.ETHEREUM])[6][token] == str(5 * 10**18)


# --- a reverted balanceOf (follow-up of the EVM wiring's review, item 8) -------------------


def test_a_reverted_balance_of_an_unlisted_token_marks_it_unreadable_and_the_run_goes_on(
    dbname, app_engine, blob_store, fresh_workspaces, http
):
    """An unlisted token whose `balanceOf` reverts (a contract the classifier took for a
    token) no longer fails every run: the run's movements are written, the token is left out
    of both sides, the row stores the balances read, and the run reads unreconciled with the
    card wallet.token_unreadable. A registry token that reverts still fails the balance read
    (a run with nothing new is then a failed row, review of PR #397)."""
    scan, _ = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    token = "0x" + "ce" * 20
    claim = {
        "hash": _hash("invented claim of a contract"),
        "blockNumber": "21000850",
        "timeStamp": "1773750000",
        "from": ME,
        "to": token,
        "value": "0",
        "gasUsed": "50000",
        "gasPrice": "10000000000",
        "isError": "0",
        "input": "0x4e71d92d",
    }
    credited = {
        "hash": claim["hash"],
        "blockNumber": "21000850",
        "timeStamp": "1773750000",
        "from": "0x" + "0" * 40,
        "to": ME,
        "contractAddress": token,
        "tokenSymbol": "ODD",
        "tokenDecimal": "18",
        "value": str(5 * 10**18),
        "logIndex": "1",
    }
    eth_chain = scan.chains[ETH_ID]
    eth_chain["rows"]["txlist"].append(claim)
    eth_chain["rows"]["tokentx"].append(credited)
    eth_chain["native"] = ETH_BALANCE - 50000 * 10_000_000_000
    eth_chain["revert"] = {token}
    eth = _run(app_engine, blob_store, ws, account)[Chain.ETHEREUM]
    assert (eth.status, eth.error_code, eth.reconciled, eth.card, eth.waiting) == (
        WalletSyncStatus.SUCCEEDED,
        None,
        False,
        E.WALLET_TOKEN_UNREADABLE,
        False,
    )
    row = _sync_row(dbname, ids[Chain.ETHEREUM])
    assert (row[0], row[5], row[7], row[9]) == ("succeeded", False, None, None)
    assert token not in row[6] and row[6][USDC] == "150000000"
    held = _q(
        dbname,
        "SELECT count(*) FROM asset_movement m JOIN asset a ON a.id = m.asset_id"
        " WHERE m.address_id = %s AND a.contract_address = %s",
        ids[Chain.ETHEREUM],
        token,
    )
    assert held[0][0] == 1

    # the next run: nothing new, the token still unreadable, still unreconciled
    again = _run(app_engine, blob_store, ws, account, day=2)[Chain.ETHEREUM]
    assert (again.movements_new, again.reconciled, again.card) == (
        0,
        False,
        E.WALLET_TOKEN_UNREADABLE,
    )
    # once it answers, the address reconciles
    eth_chain["revert"] = set()
    eth_chain["tokens"][token] = 5 * 10**18
    assert _run(app_engine, blob_store, ws, account, day=3)[Chain.ETHEREUM].reconciled is True

    # a registry token that reverts is no misread contract: the balance read fails, and with
    # nothing new the run is a failed row (review of PR #397)
    eth_chain["revert"] = {USDC}
    listed = _run(app_engine, blob_store, ws, account, day=4)[Chain.ETHEREUM]
    assert (listed.status, listed.error_code, listed.reconciled) == (
        WalletSyncStatus.FAILED,
        E.WALLET_SOURCE_FAILED,
        None,
    )


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("execution reverted", 3),
        ("execution reverted: Access denied", 3),
        ("execution reverted: transfers not supported", -32000),
    ],
)
def test_a_revert_reason_is_never_read_as_a_provider_refusal(
    dbname, app_engine, blob_store, fresh_workspaces, http, reason, code
):
    """Review of PR #397: a token's own revert text („Access denied“, „not supported“) holds
    words the refusal classifier keys on; a reverted call (code 3, or a message starting
    „execution reverted“) is the contract's answer, so the token is unreadable and the run
    stores its balances, never refused."""
    scan, _ = http
    ws, _ = fresh_workspaces
    account, ids = _wallet(app_engine, ws)
    token = "0x" + "cf" * 20
    tx_hash = _hash(f"invented claim, {reason}")
    eth_chain = scan.chains[ETH_ID]
    eth_chain["rows"]["txlist"].append(
        {
            "hash": tx_hash,
            "blockNumber": "21000850",
            "timeStamp": "1773750000",
            "from": ME,
            "to": token,
            "value": "0",
            "gasUsed": "50000",
            "gasPrice": "10000000000",
            "isError": "0",
            "input": "0x4e71d92d",
        }
    )
    eth_chain["rows"]["tokentx"].append(
        {
            "hash": tx_hash,
            "blockNumber": "21000850",
            "timeStamp": "1773750000",
            "from": "0x" + "0" * 40,
            "to": ME,
            "contractAddress": token,
            "tokenSymbol": "ODD",
            "tokenDecimal": "18",
            "value": str(5 * 10**18),
            "logIndex": "1",
        }
    )
    eth_chain["native"] = ETH_BALANCE - 50000 * 10_000_000_000
    eth_chain["revert"] = {token}
    eth_chain["reason"] = reason
    eth_chain["revert_code"] = code
    eth = _run(app_engine, blob_store, ws, account)[Chain.ETHEREUM]
    assert (eth.status, eth.error_code, eth.reconciled, eth.card) == (
        WalletSyncStatus.SUCCEEDED,
        None,
        False,
        E.WALLET_TOKEN_UNREADABLE,
    )
    row = _sync_row(dbname, ids[Chain.ETHEREUM])
    assert (row[0], row[9]) == ("succeeded", None)
    assert token not in row[6] and row[6][USDC] == "150000000"

    # a registry token reverting with the same text still fails the balance read: the
    # contract's answer (wallet.source_failed), nothing new, a failed row; never refused
    eth_chain["revert"] = {USDC}
    listed = _run(app_engine, blob_store, ws, account, day=2)[Chain.ETHEREUM]
    assert (listed.status, listed.error_code) == (
        WalletSyncStatus.FAILED,
        E.WALLET_SOURCE_FAILED,
    )
