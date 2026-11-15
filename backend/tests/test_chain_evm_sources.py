"""services/chain/evm_etherscan and evm_rpc_balance without a network (docs/phases/phase-7.md
D181 (b), (c), D182 (1), (4), D186 (2)): fake transports answer in Etherscan V2's and the
JSON-RPC's own shapes (invented addresses, hashes and amounts), so paging, the pinned window,
the failure codes and the key's absence from every error are checked as the provider would
answer. evm_tx.yaml's `key_unset` and `bsc_balance_only` are replayed here."""

import json
import urllib.parse

import pytest
from fixture_models_phase7 import load
from pydantic import SecretStr

from hausbuch.rules import errors as E
from hausbuch.rules.chain import evm
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import AssetKey, Chain, GroupKind
from hausbuch.schemas.household import MovementKind
from hausbuch.services.chain import evm_etherscan, evm_http
from hausbuch.services.chain.base import ChainSourceError
from hausbuch.services.chain.evm_etherscan import EtherscanSource
from hausbuch.services.chain.evm_rpc_balance import RpcBalanceSource

EVM = {c.id: c for c in load("evm_tx").cases}
KEY = "INVENTEDKEY0123456789ABCDEFGHIJKL"
RPC_URL = "https://bsc.invalid/v1/INVENTEDPATHKEY"
ME = "0x627f3684df28ba04ebecf523c815218a0f4346f6"
FOREIGN = "0x96d82db02c1fdeb860fcb6c58f11625348b9b831"
CONTRACT = "0xbac1b60215808a2bce58af79feb1efcaebbcdb3a"
NFT = "0x3c5f1e7a2b9d4c6e8f0a1b2c3d4e5f60718293a4"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
TIP = 21_000_112  # the window ends at 21_000_100


def _h(n: int) -> str:
    return "0x" + f"{n:064x}"


def _ok(result) -> tuple[int, bytes]:
    return 200, json.dumps({"status": "1", "message": "OK", "result": result}).encode()


def _notok(result: str) -> tuple[int, bytes]:
    return 200, json.dumps({"status": "0", "message": "NOTOK", "result": result}).encode()


def _proxy(result) -> tuple[int, bytes]:
    return 200, json.dumps({"jsonrpc": "2.0", "id": 83, "result": result}).encode()


def _row(n: int, block: int, **kw) -> dict:
    return {
        "blockNumber": str(block),
        "timeStamp": str(1_773_000_000 + block),
        "hash": _h(n),
        "nonce": "1",
        "blockHash": _h(10_000 + block),
        "transactionIndex": "0",
        "gas": "60000",
        "gasPrice": "1000000000",
        "gasUsed": "21000",
        "cumulativeGasUsed": "100000",
        "confirmations": "12",
        "isError": "0",
        "txreceipt_status": "1",
        "input": "0x",
        "methodId": "0x",
        "functionName": "",
        "contractAddress": "",
        "value": "0",
    } | kw


class Etherscan:
    """Answers like Etherscan V2: the account lists filtered by startblock/endblock, sorted,
    sliced by page and offset; `eth_blockNumber`, `balance`, `eth_call`. Records each query."""

    def __init__(self, lists: dict[str, list[dict]] | None = None, **override) -> None:
        self.lists = lists or {}
        self.override = override  # action -> (status, body) or an exception
        self.queries: list[dict[str, str]] = []
        self.urls: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.urls.append(url)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.queries.append(query)
        action = query["action"]
        if action in self.override:
            answer = self.override[action]
            if isinstance(answer, Exception):
                raise answer
            return answer
        if action == "eth_blockNumber":
            return _proxy(hex(TIP))
        if action == "balance":
            return _ok("799580000000000000")
        if action == "eth_call":
            return _proxy(hex(50_000_000) if query["to"] == USDC else "0x")
        start, end = int(query["startblock"]), int(query["endblock"])
        rows = sorted(
            (r for r in self.lists.get(action, []) if start <= int(r["blockNumber"]) <= end),
            key=lambda r: int(r["blockNumber"]),
        )
        page, offset = int(query["page"]), int(query["offset"])
        part = rows[(page - 1) * offset : page * offset]
        if not part:
            return 200, json.dumps(
                {"status": "0", "message": "No transactions found", "result": []}
            ).encode()
        return _ok(part)


def _source(fetch, chain=Chain.ETHEREUM, key=KEY, sleeps=None):
    return EtherscanSource(
        chain,
        SecretStr(key) if key else None,
        fetch=fetch,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


# --- Etherscan ------------------------------------------------------------------------------


def test_key_unset_case():
    case = EVM["key_unset"]
    fetch = Etherscan()
    with pytest.raises(ChainSourceError) as caught:
        _source(fetch, chain=case.chain, key=None).transactions(case.own[0], None)
    assert caught.value.code == case.expect.error == E.WALLET_KEY_MISSING
    assert fetch.urls == []  # nothing sent


def test_window_pinned_for_all_five_lists_and_the_cursor():
    fetch = Etherscan()
    page = _source(fetch).transactions(ME, "20999000")
    lists = [q for q in fetch.queries if q["module"] == "account"]
    assert [q["action"] for q in lists] == list(evm.ACTIONS)
    assert {(q["startblock"], q["endblock"]) for q in lists} == {("20998988", "21000100")}
    assert all(q["chainid"] == "1" and q["sort"] == "asc" for q in lists)
    assert page.items == [] and page.next_cursor == "21000100"


def test_a_first_sync_reads_from_genesis_and_arbitrum_by_its_chain_id():
    fetch = Etherscan()
    _source(fetch, chain=Chain.ARBITRUM).transactions(ME, None)
    lists = [q for q in fetch.queries if q["module"] == "account"]
    assert {(q["chainid"], q["startblock"]) for q in lists} == {("42161", "0")}


def test_a_tip_behind_the_cursor_reads_nothing():
    fetch = Etherscan()
    page = _source(fetch).transactions(ME, str(TIP + 50))
    assert page.items == [] and page.next_cursor == str(TIP + 50)
    assert [q["action"] for q in fetch.queries] == ["eth_blockNumber"]


def test_paging_by_block_loses_and_doubles_nothing(monkeypatch):
    monkeypatch.setattr(evm_etherscan, "PAGE_ROWS", 3)
    blocks = [10, 11, 12, 12, 12, 12, 13, 14]  # block 12 spills over two pages
    rows = [
        _row(i, b, **{"from": FOREIGN, "to": ME}, value=str(1000 + i)) for i, b in enumerate(blocks)
    ]
    fetch = Etherscan({"txlist": rows})
    page = _source(fetch).transactions(ME, None)
    got = sorted(t.transfers[0].units for t in page.items)
    assert got == [1000 + i for i in range(len(blocks))]
    pages = [(q["startblock"], q["page"]) for q in fetch.queries if q.get("action") == "txlist"]
    # 10,11,12 → keep 10,11, restart at 12; 12,12,12 one block → page 2; 12,13,14 → keep
    # 12,13, restart at 14; 14 → the end
    assert pages == [("0", "1"), ("12", "1"), ("12", "2"), ("14", "1")]


def test_records_normalise_failed_internal_erc20_and_nfts_as_spam():
    lists = {
        "txlist": [
            _row(
                1,
                100,
                **{"from": ME, "to": CONTRACT},
                value="5",
                isError="1",
                txreceipt_status="0",
                gasUsed="45000",
                gasPrice="10000000000",
                methodId="0x38ed1739",
                input="0x38ed1739",
            ),
        ],
        "txlistinternal": [
            {
                "blockNumber": "101",
                "timeStamp": "1773000101",
                "hash": _h(2),
                "from": CONTRACT,
                "to": ME,
                "value": "30000000000000000",
                "contractAddress": "",
                "input": "",
                "type": "call",
                "gas": "2300",
                "gasUsed": "0",
                "traceId": "0_1",
                "isError": "0",
                "errCode": "",
            },
        ],
        "tokentx": [
            _row(
                3,
                102,
                **{"from": FOREIGN, "to": ME},
                contractAddress=USDC,
                tokenName="USDC",
                tokenSymbol="USDC",
                tokenDecimal="6",
                value="250000000",
                logIndex="12",
            ),
        ],
        "tokennfttx": [
            _row(
                4,
                103,
                **{"from": FOREIGN, "to": ME},
                contractAddress=NFT,
                tokenID="77",
                tokenName="Claim",
                tokenSymbol="CLAIM",
                tokenDecimal="0",
            ),
        ],
        "token1155tx": [
            _row(
                5,
                104,
                **{"from": FOREIGN, "to": ME},
                contractAddress=NFT,
                tokenID="8",
                tokenValue="3",
                tokenName="Gift",
                tokenSymbol="GIFT",
            ),
        ],
    }
    page = _source(Etherscan(lists)).transactions(ME, None)
    groups = [evm.classify_evm(Chain.ETHEREUM, tx, ME) for tx in page.items]
    assert [g.kind for g in groups] == [
        GroupKind.FAILED,
        GroupKind.TRANSFER,
        GroupKind.TRANSFER,
        GroupKind.SPAM,
        GroupKind.SPAM,
    ]
    failed = groups[0]
    assert [(g.kind, g.units) for g in failed.legs] == [(MovementKind.FEE, 450000000000000)]
    assert [g.index for g in groups[1].legs] == ["internal:0_1"]
    assert [(g.index, g.asset_key.contract) for g in groups[2].legs] == [("12", USDC)]
    assert [(g.kind, g.units) for g in groups[3].legs + groups[4].legs] == [
        (MovementKind.SPAM, 1),
        (MovementKind.SPAM, 3),
    ]
    assert page.items[0].raw["txlist"][0]["isError"] == "1"  # the stored original


def test_one_run_reads_history_and_balances_to_one_block():
    class Moving(Etherscan):  # the chain moves on between the calls
        tips = iter(range(TIP, TIP + 100))

        def __call__(self, url, headers, timeout, max_bytes):
            if "eth_blockNumber" in url:
                self.urls.append(url)
                self.queries.append({"action": "eth_blockNumber"})
                return _proxy(hex(next(self.tips)))
            return super().__call__(url, headers, timeout, max_bytes)

    fetch = Moving()
    source = _source(fetch)
    page = source.transactions(ME, None)
    balances = source.balances(ME)
    assert [q["action"] for q in fetch.queries].count("eth_blockNumber") == 1
    assert {b.at_block for b in balances} == {int(page.next_cursor or 0)}


def test_balances_at_the_window_end():
    fetch = Etherscan()
    got = _source(fetch).balances(ME)
    at = TIP - 12
    assert got[0].asset_key == AssetKey(chain=Chain.ETHEREUM, contract=None)
    assert (got[0].units, got[0].at_block) == (799580000000000000, at)
    tokens = {b.asset_key.contract: b.units for b in got[1:]}
    listed = {t.contract for t in asset_registry().tokens if t.chain is Chain.ETHEREUM}
    assert set(tokens) == listed and tokens[USDC] == 50_000_000
    calls = [q for q in fetch.queries if q["action"] in ("balance", "eth_call")]
    assert {q["tag"] for q in calls} == {hex(at)}
    assert all(q["data"] == "0x70a08231" + ME[2:].rjust(64, "0") for q in calls[1:])


def test_paced_at_two_and_a_half_calls_a_second():
    sleeps: list[float] = []
    fetch = Etherscan()
    _source(fetch, sleeps=sleeps).transactions(ME, None)
    assert sleeps == [0.4] * (len(fetch.urls) - 1)


@pytest.mark.parametrize(
    ("answer", "code", "status"),
    [
        ((429, b""), E.WALLET_SOURCE_THROTTLED, 429),
        ((401, b""), E.WALLET_SOURCE_REFUSED, 401),
        ((403, b""), E.WALLET_SOURCE_REFUSED, 403),
        ((502, b""), evm_http.SOURCE_FAILED, 502),
        (
            _notok("Max rate limit reached, please use API Key for higher rate limit"),
            E.WALLET_SOURCE_THROTTLED,
            None,
        ),
        (_notok(f"Invalid API Key (#err2)|{KEY}"), E.WALLET_SOURCE_REFUSED, None),
        (
            _notok("Too many invalid api key attempts, please try again later"),
            E.WALLET_SOURCE_REFUSED,
            None,
        ),
        (_notok("Missing/Invalid API Key"), E.WALLET_SOURCE_REFUSED, None),
        ((200, b"<html>maintenance</html>"), evm_http.SOURCE_FAILED, None),
        (TimeoutError("timed out reading " + KEY), evm_http.SOURCE_FAILED, None),
    ],
)
def test_failures_map_to_codes_without_the_key(answer, code, status):
    fetch = Etherscan(eth_blockNumber=answer)
    with pytest.raises(ChainSourceError) as caught:
        _source(fetch).transactions(ME, None)
    assert (caught.value.code, caught.value.status) == (code, status)
    assert KEY not in str(caught.value) and "etherscan" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


def test_a_list_refusal_in_the_middle_of_a_sync():
    body = b'{"status":"0","message":"NOTOK","result":"Max rate limit reached"}'
    fetch = Etherscan(tokentx=(200, body))
    with pytest.raises(ChainSourceError) as caught:
        _source(fetch).transactions(ME, None)
    assert caught.value.code == E.WALLET_SOURCE_THROTTLED


# --- BSC over JSON-RPC ----------------------------------------------------------------------

BSC_ME = "0xed052f45caae52c75f9087ae2440d534765570e1"
BSC_TIP = 0x2FAF200


class Rpc:
    """Answers JSON-RPC by method; `eth_call` by the contract it is sent to."""

    def __init__(self, answers: dict | None = None, raw: dict | None = None) -> None:
        self.answers = {"eth_blockNumber": hex(BSC_TIP), "eth_getBalance": "0x0"} | (answers or {})
        self.raw = raw or {}  # method -> (status, body) or an exception
        self.calls: list[dict] = []
        self.urls: list[str] = []

    def __call__(self, url, headers, body, timeout, max_bytes):
        self.urls.append(url)
        payload = json.loads(body)
        self.calls.append(payload)
        assert headers["Content-Type"] == "application/json"
        method = payload["method"]
        if method in self.raw:
            answer = self.raw[method]
            if isinstance(answer, Exception):
                raise answer
            return answer
        result = self.answers.get(method)
        if method == "eth_call":
            result = (self.answers.get("balanceOf") or {}).get(payload["params"][0]["to"], "0x")
        return 200, json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()


def _rpc(post, sleeps=None):
    return RpcBalanceSource(
        Chain.BSC,
        RPC_URL,
        post=post,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


def test_bsc_balance_only_case():
    case = EVM["bsc_balance_only"]
    post = Rpc(
        {"eth_getBalance": case.records["eth_getBalance"], "balanceOf": case.records["balanceOf"]}
    )
    source = _rpc(post)
    assert source.transactions(case.own[0], "77").items == []
    assert source.transactions(case.own[0], "77").next_cursor == "77"
    got = source.balances(case.own[0])
    at = BSC_TIP - 12
    assert got[0].units == int(str(case.records["eth_getBalance"]), 16) and got[0].at_block == at
    tokens = {b.asset_key.contract: b.units for b in got[1:]}
    for contract, units in case.records["balanceOf"].items():  # type: ignore[union-attr]
        assert tokens[contract] == int(units, 16)
    listed = {t.contract for t in asset_registry().tokens if t.chain is Chain.BSC}
    assert set(tokens) == listed
    assert post.calls[1]["params"] == [case.own[0], hex(at)]
    assert case.expect.movements == []


def test_bsc_deposit_read_by_hash():
    case = EVM["bsc_withdrawal_receipt_bep20"]
    post = Rpc(
        {
            "eth_getTransactionByHash": case.records["transaction"],
            "eth_getTransactionReceipt": case.records["receipt"],
            "eth_getBlockByNumber": {"number": "0x2faf0e4", "timestamp": "0x69a8c3c0"},
        }
    )
    tx = _rpc(post).deposit(str(case.exchange_leg["tx_hash"]), BSC_ME)  # type: ignore[index]
    assert tx is not None and tx.block == 0x2FAF0E4 and tx.fee is None
    assert [(t.units, t.index, t.asset_key.contract) for t in tx.transfers] == [
        (m.units, m.index, m.contract) for m in case.expect.movements
    ]
    assert [c["method"] for c in post.calls] == [
        "eth_getTransactionByHash",
        "eth_blockNumber",
        "eth_getTransactionReceipt",
        "eth_getBlockByNumber",
    ]


def test_bsc_deposit_not_final_or_unknown():
    case = EVM["bsc_withdrawal_receipt_native"]
    pending = dict(case.records["transaction"]) | {"blockNumber": hex(BSC_TIP - 3)}  # type: ignore[arg-type]
    assert _rpc(Rpc({"eth_getTransactionByHash": pending})).deposit(_h(1), BSC_ME) is None
    assert _rpc(Rpc({"eth_getTransactionByHash": None})).deposit(_h(1), BSC_ME) is None


@pytest.mark.parametrize(
    ("raw", "code", "status"),
    [
        ((429, b""), E.WALLET_SOURCE_THROTTLED, 429),
        ((401, b""), E.WALLET_SOURCE_REFUSED, 401),
        ((403, b""), E.WALLET_SOURCE_REFUSED, 403),
        (
            (200, b'{"jsonrpc":"2.0","id":1,"error":{"code":-32005,"message":"limit exceeded"}}'),
            E.WALLET_SOURCE_THROTTLED,
            None,
        ),
        (
            (200, b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"header not found"}}'),
            evm_http.SOURCE_FAILED,
            None,
        ),
        (ConnectionError("refused by " + RPC_URL), evm_http.SOURCE_FAILED, None),
    ],
)
def test_rpc_failures_map_to_codes_without_the_url(raw, code, status):
    post = Rpc(raw={"eth_blockNumber": raw})
    with pytest.raises(ChainSourceError) as caught:
        _rpc(post).balances(BSC_ME)
    assert (caught.value.code, caught.value.status) == (code, status)
    assert "INVENTEDPATHKEY" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_rpc_paced():
    sleeps: list[float] = []
    post = Rpc()
    _rpc(post, sleeps).balances(BSC_ME)
    assert sleeps == [0.4] * (len(post.calls) - 1)
