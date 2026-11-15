"""Phase 7 step 2: the Solana JSON-RPC source (docs/phases/phase-7.md D181 (d), D182, D192,
Q-O93) against recorded answers through a fake transport: paging back to the first signature,
the request shapes, the balance parts, rewards, a 429, a 401/403, a JSON-RPC error, a missing
transaction, the budget, the pace, and the URL that never leaves the source.

Every key, signature and amount is invented (the records of tests/fixtures/phase7/
solana_tx.yaml); the answers keep the RPC's real shapes."""

import json
import urllib.error
from copy import deepcopy
from datetime import UTC, datetime

import fixture_models_phase7 as phase7
import pytest
from pydantic import SecretStr

from hausbuch.rules import errors as E
from hausbuch.rules.chain import solana
from hausbuch.schemas.chain import AssetKey, Chain, ChainSourceKind
from hausbuch.services.chain.base import ChainSourceError
from hausbuch.services.chain.solana_rpc import (
    HistoryUnavailable,
    SolanaRpc,
    SolanaRpcError,
)

SECRET = "k3yInv3nt3dF0rTh1sT3st"
URL = f"https://rpc.example.invalid/v1/{SECRET}?api-key={SECRET}"
A = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"
F = "ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
CASES = {c.id: c for c in phase7.load("solana_tx").cases}


class FakeRpc:
    """Answers by method from queues of (status, result-or-error); records every request."""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {k: list(v) for k, v in answers.items()}
        self.requests: list[dict] = []
        self.urls: list[str] = []

    def __call__(self, url, headers, body, timeout, max_bytes):
        request = json.loads(body)
        self.requests.append(request)
        self.urls.append(url)
        assert headers["Content-Type"] == "application/json"
        status, payload = self.answers[request["method"]].pop(0)
        if isinstance(payload, Exception):
            raise payload
        if status != 200:
            return status, b""
        if isinstance(payload, dict) and "error" in payload:
            return 200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **payload}).encode()
        return 200, json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": payload}).encode()

    def calls(self, method: str) -> list[list]:
        return [r["params"] for r in self.requests if r["method"] == method]


def _rpc(answers: dict[str, list], **kwargs) -> tuple[SolanaRpc, FakeRpc, list[float]]:
    fake = FakeRpc(answers)
    slept: list[float] = []
    rpc = SolanaRpc(url=SecretStr(URL), post=fake, sleep=slept.append, **kwargs)
    return rpc, fake, slept


def _sig(case_id: str, err=None) -> dict:
    tx = CASES[case_id].tx
    return {
        "signature": tx["transaction"]["signatures"][0],
        "slot": tx["slot"],
        "err": err,
        "memo": None,
        "blockTime": tx["blockTime"],
        "confirmationStatus": "finalized",
    }


def _no_secret(exc: BaseException, rpc: SolanaRpc) -> None:
    for text in (str(exc), repr(exc), repr(rpc), str(exc.__cause__), str(exc.__context__)):
        assert SECRET not in text and "example.invalid" not in text


# --- signatures, paging and transactions ----------------------------------------------------

ORDER = ["native_receive", "native_send_with_fee", "spl_out", "failed_transaction"]


def test_signatures_page_back_to_the_first_and_come_oldest_first():
    err = {"InstructionError": [0, {"Custom": 6001}]}
    newest_first = [_sig(i, err if i == "failed_transaction" else None) for i in reversed(ORDER)]
    rpc, fake, _ = _rpc(
        {"getSignaturesForAddress": [(200, newest_first[:2]), (200, newest_first[2:]), (200, [])]},
        signature_page=2,
    )
    sigs = rpc.signatures(A)
    assert [s.signature for s in sigs] == [_sig(i)["signature"] for i in ORDER]
    calls = fake.calls("getSignaturesForAddress")
    assert [c[0] for c in calls] == [A, A, A]
    assert calls[0][1] == {"limit": 2, "commitment": "finalized"}
    assert calls[1][1]["before"] == newest_first[1]["signature"]
    assert calls[2][1]["before"] == newest_first[3]["signature"]
    assert sigs[-1].failed and not sigs[0].failed


def test_a_short_page_is_not_the_end_only_an_empty_one_is():
    """A provider may answer fewer than the limit while older signatures remain."""
    newest_first = [_sig(i) for i in reversed(ORDER)]
    rpc, fake, _ = _rpc(
        {
            "getSignaturesForAddress": [
                (200, newest_first[:1]),
                (200, newest_first[1:3]),
                (200, newest_first[3:]),
                (200, []),
            ]
        },
        signature_page=3,
    )
    sigs = rpc.signatures(A)
    assert [s.signature for s in sigs] == [_sig(i)["signature"] for i in ORDER]
    calls = fake.calls("getSignaturesForAddress")
    assert len(calls) == 4
    assert [c[1].get("before") for c in calls] == [
        None,
        newest_first[0]["signature"],
        newest_first[2]["signature"],
        newest_first[3]["signature"],
    ]


def test_signatures_until_the_cursor():
    rpc, fake, _ = _rpc({"getSignaturesForAddress": [(200, [_sig("spl_out")]), (200, [])]})
    cursor = _sig("native_send_with_fee")["signature"]
    assert [s.signature for s in rpc.signatures(A, cursor)] == [_sig("spl_out")["signature"]]
    assert fake.calls("getSignaturesForAddress")[0][1]["until"] == cursor


def test_transactions_are_read_jsonparsed_version_0_finalized_in_pages():
    records = {i: CASES[i].tx for i in ORDER[:3]}
    rpc, fake, _ = _rpc(
        {
            "getSignaturesForAddress": [(200, [_sig(i) for i in reversed(ORDER[:3])]), (200, [])],
            "getTransaction": [(200, records[i]) for i in ORDER[:3]],
        },
        transaction_page=2,
    )
    first = rpc.transactions(A, None)
    assert [t.hash for t in first.items] == [_sig(i)["signature"] for i in ORDER[:2]]
    assert first.next_cursor == _sig("native_send_with_fee")["signature"]
    second = rpc.transactions(A, first.next_cursor)
    assert [t.hash for t in second.items] == [_sig("spl_out")["signature"]]
    assert second.next_cursor is None
    # The rest of the listing was kept: one listing (its page and the empty page that ends
    # it) for both pages.
    assert len(fake.calls("getSignaturesForAddress")) == 2
    for params in fake.calls("getTransaction"):
        assert params[1] == {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "finalized",
        }
    receive, send = first.items
    assert receive.fee is None and receive.status == "ok"
    assert receive.occurred_at == datetime.fromtimestamp(1772960400, UTC)
    assert send.fee == (AssetKey(chain=Chain.SOLANA), 5000)
    assert [(t.asset_key.contract, t.units, t.from_addr) for t in send.transfers] == [
        (None, 1000000000, A)
    ]
    assert send.program_calls == [(solana.SYSTEM_PROGRAM, "transfer")]
    assert send.raw == CASES["native_send_with_fee"].tx
    assert rpc.kind is ChainSourceKind.SOLANA_RPC


def test_a_failed_transaction_keeps_its_fee():
    rpc, _, _ = _rpc({"getTransaction": [(200, CASES["failed_transaction"].tx)]})
    tx = SolanaRpc.chain_tx(rpc.transaction("x"), {A})
    assert tx.status == "failed" and tx.fee == (AssetKey(chain=Chain.SOLANA), 5000)


def test_a_listed_signature_without_its_transaction_stops_the_sync():
    rpc, _, _ = _rpc(
        {
            "getSignaturesForAddress": [(200, [_sig("native_receive")]), (200, [])],
            "getTransaction": [(200, None)],
        }
    )
    with pytest.raises(HistoryUnavailable) as caught:
        rpc.transactions(A, None)
    assert caught.value.slot == CASES["native_receive"].tx["slot"]


def test_a_ledger_error_names_the_slot_too():
    rpc, _, _ = _rpc(
        {"getTransaction": [(200, {"error": {"code": -32009, "message": f"slot {URL}"}})]}
    )
    with pytest.raises(HistoryUnavailable) as caught:
        rpc.transaction("x", 123)
    assert (caught.value.slot, caught.value.code) == (123, -32009)
    _no_secret(caught.value, rpc)


def test_a_cleaned_up_block_is_a_ledger_error_with_the_slot():
    """-32001 (block cleaned up) on getTransaction and on getBlockTime."""
    error = (200, {"error": {"code": -32001, "message": f"cleaned up {URL}"}})
    rpc, _, _ = _rpc({"getTransaction": [error], "getBlockTime": [error]})
    with pytest.raises(HistoryUnavailable) as caught:
        rpc.transaction("x", 456)
    assert (caught.value.slot, caught.value.code) == (456, -32001)
    _no_secret(caught.value, rpc)
    with pytest.raises(HistoryUnavailable) as caught:
        rpc.block_time(789)
    assert (caught.value.slot, caught.value.code) == (789, -32001)
    _no_secret(caught.value, rpc)


def test_transactions_read_with_every_own_account_and_the_token_accounts():
    """A record whose token rows carry no owner reads through transactions() once the own
    token accounts are passed; without them it is unreadable, never a dropped leg."""
    record = deepcopy(CASES["spl_out"].tx)
    for key in ("preTokenBalances", "postTokenBalances"):
        for row in record["meta"][key]:
            row.pop("owner")
    own_account = "Dd2nAL9RJk7UUMMSAWszC9oJTi3jGoPGpjStUZ3ZH8ZU"
    answers = {
        "getSignaturesForAddress": [(200, [_sig("spl_out")]), (200, [])],
        "getTransaction": [(200, record)],
    }
    rpc, _, _ = _rpc(answers)
    page = rpc.transactions(A, None, own_token_accounts={own_account})
    (tx,) = page.items
    assert [(t.asset_key.contract, t.units, t.from_addr) for t in tx.transfers] == [
        (USDC, 20000000, own_account)
    ]
    rpc, _, _ = _rpc(answers)
    # As every source's error: a ChainSourceError with the code, its cause naming the
    # signature only.
    with pytest.raises(ChainSourceError) as raised:
        rpc.transactions(A, None)
    assert raised.value.code == E.WALLET_RECORD_UNREADABLE
    assert _sig("spl_out")["signature"] in str(raised.value.__cause__)
    assert own_account not in str(raised.value.__cause__) and A not in str(raised.value)


def test_a_payment_between_two_own_addresses_is_no_transfer_with_every_own_address():
    """transactions() with `own`: A's send to F is no transfer out when F is A's too."""
    answers = {
        "getSignaturesForAddress": [(200, [_sig("native_send_with_fee")]), (200, [])],
        "getTransaction": [(200, CASES["native_send_with_fee"].tx)],
    }
    rpc, _, _ = _rpc(answers)
    (alone,) = rpc.transactions(A, None).items
    assert [(t.units, t.from_addr) for t in alone.transfers] == [(1000000000, A)]
    rpc, _, _ = _rpc(answers)
    (both,) = rpc.transactions(A, None, own={F}).items
    assert both.transfers == [] and both.fee == (AssetKey(chain=Chain.SOLANA), 5000)


# --- failures that may be logged -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (429, E.WALLET_SOURCE_THROTTLED),
        (401, E.WALLET_SOURCE_REFUSED),
        (403, E.WALLET_SOURCE_REFUSED),
    ],
)
def test_http_refusals_map_to_the_wallet_codes(status, code):
    rpc, _, _ = _rpc({"getBalance": [(status, None)]})
    with pytest.raises(ChainSourceError) as caught:
        rpc.lamports(A)
    assert (caught.value.code, caught.value.status) == (code, status)
    _no_secret(caught.value, rpc)


def test_a_json_rpc_error_carries_its_number_never_the_message():
    message = f"Invalid param: {URL}"
    rpc, _, _ = _rpc({"getBalance": [(200, {"error": {"code": -32602, "message": message}})]})
    with pytest.raises(SolanaRpcError) as caught:
        rpc.lamports(A)
    assert (caught.value.kind, caught.value.code) == ("rpc", -32602)
    assert "Invalid" not in str(caught.value)
    _no_secret(caught.value, rpc)


def test_a_json_rpc_429_is_throttled():
    rpc, _, _ = _rpc({"getBalance": [(200, {"error": {"code": 429, "message": "slow down"}})]})
    with pytest.raises(ChainSourceError) as caught:
        rpc.lamports(A)
    assert caught.value.code == E.WALLET_SOURCE_THROTTLED


def test_a_transport_failure_names_its_class_only():
    failure = urllib.error.URLError(f"cannot reach {URL}")
    rpc, _, _ = _rpc({"getBalance": [(200, failure)]})
    with pytest.raises(SolanaRpcError) as caught:
        rpc.lamports(A)
    assert caught.value.kind == "URLError"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    _no_secret(caught.value, rpc)


@pytest.mark.parametrize("status", [500, 302])
def test_another_status_is_an_http_error(status):
    rpc, _, _ = _rpc({"getBalance": [(status, None)]})
    with pytest.raises(SolanaRpcError) as caught:
        rpc.lamports(A)
    assert (caught.value.kind, caught.value.code) == ("HTTPError", status)


def test_the_url_is_neither_in_the_repr_nor_the_host():
    rpc, _, _ = _rpc({})
    assert SECRET not in repr(rpc) and "example.invalid" not in repr(rpc)
    assert rpc.source_host == "rpc.example.invalid"


def test_the_pace_and_the_budget():
    rpc, fake, slept = _rpc(
        {"getBalance": [(200, {"context": {"slot": 1}, "value": 5})] * 3}, rps=4, budget=2
    )
    rpc.lamports(A)
    rpc.lamports(A)
    assert slept == [0.25]  # between requests, never before the first
    with pytest.raises(ChainSourceError) as caught:
        rpc.lamports(A)
    assert caught.value.code == E.WALLET_SYNC_DEFERRED
    assert len(fake.requests) == 2 and rpc.requests == 2
    assert set(fake.urls) == {URL}


# --- balances and rewards ------------------------------------------------------------------


def _token_account(pubkey, mint, units, lamports, program=solana.TOKEN_PROGRAM):
    return {
        "pubkey": pubkey,
        "account": {
            "lamports": lamports,
            "owner": program,
            "executable": False,
            "rentEpoch": 18446744073709551615,
            "space": 165,
            "data": {
                "program": "spl-token",
                "parsed": {
                    "type": "account",
                    "info": {
                        "mint": mint,
                        "owner": A,
                        "isNative": False,
                        "state": "initialized",
                        "tokenAmount": {"amount": str(units), "decimals": 6},
                    },
                },
                "space": 165,
            },
        },
    }


def test_balance_parts_count_rent_wsol_and_stake():
    stake = "Hqw2dGuE2dcLueqA4UFgPw1Uciu9C7q41pxeUWcxX9fF"
    wsol = solana.native_mint()
    rpc, fake, _ = _rpc(
        {
            "getBalance": [
                (200, {"context": {"slot": 900}, "value": 1000000000}),
                (200, {"context": {"slot": 902}, "value": 10001534000}),
            ],
            "getTokenAccountsByOwner": [
                (
                    200,
                    {
                        "context": {"slot": 901},
                        "value": [
                            _token_account(
                                "Dd2nAL9RJk7UUMMSAWszC9oJTi3jGoPGpjStUZ3ZH8ZU",
                                USDC,
                                15000000,
                                2000000,
                            ),
                            _token_account(
                                "AtcK14U2KY2hvegt1GqcTEXSeWDBjQUPsnukPCacuyej",
                                USDC,
                                10000000,
                                2000000,
                            ),
                            _token_account(
                                "Bm1xoaBWvXQFAZhEy8tUJYTGqbXrfz4cDAg3VuuvcPCS", wsol, 500, 2039780
                            ),
                        ],
                    },
                ),
                (
                    200,
                    {
                        "context": {"slot": 899},
                        "value": [
                            _token_account(
                                "HWJnoLWz9HDdvCKDPaS9RYoXC4FZueQVXyCk3ryXbVq6",
                                "AA84V3cuyat3DZfiLtTCQfx3qhvKLHjJxcBnfbgLyyYw",
                                0,
                                2074080,
                                solana.TOKEN_2022_PROGRAM,
                            )
                        ],
                    },
                ),
            ],
        }
    )
    parts, slot = rpc.balance_parts(A, [stake])
    sol = AssetKey(chain=Chain.SOLANA)
    assert parts[sol] == [1000000000, 2000000, 2000000, 2039280, 2074080, 10001534000]
    assert parts[AssetKey(chain=Chain.SOLANA, contract=USDC)] == [15000000, 10000000]
    assert parts[AssetKey(chain=Chain.SOLANA, contract=wsol)] == [500]
    assert slot == 899
    programs = [p[1]["programId"] for p in fake.calls("getTokenAccountsByOwner")]
    assert programs == [solana.TOKEN_PROGRAM, solana.TOKEN_2022_PROGRAM]
    assert fake.calls("getTokenAccountsByOwner")[0][2] == {
        "encoding": "jsonParsed",
        "commitment": "finalized",
    }


def test_balances_sum_the_parts_and_drop_an_empty_mint():
    rpc, _, _ = _rpc(
        {
            "getBalance": [(200, {"context": {"slot": 7}, "value": 0})],
            "getTokenAccountsByOwner": [
                (
                    200,
                    {"context": {"slot": 7}, "value": [_token_account("x" * 32, USDC, 0, 2039280)]},
                ),
                (200, {"context": {"slot": 7}, "value": []}),
            ],
        }
    )
    balances = rpc.balances(A)
    assert [(b.asset_key.contract, b.units, b.at_block) for b in balances] == [(None, 2039280, 7)]


def test_inflation_rewards_and_the_epoch_end():
    case = phase7.load("solana_tx").inflation_rewards[0]
    end = int(case.epoch_end.timestamp())
    rpc, fake, _ = _rpc(
        {
            "getEpochInfo": [
                (
                    200,
                    {
                        "epoch": 821,
                        "slotIndex": 5,
                        "slotsInEpoch": 432000,
                        "absoluteSlot": 354240005,
                    },
                )
            ],
            "getInflationReward": [(200, [case.result, None])],
            "getBlockTime": [(200, end)],
        }
    )
    assert rpc.epoch() == 821
    first, second = rpc.inflation_rewards([case.stake_account, "y" * 32], 820)
    assert second is None
    assert fake.calls("getInflationReward")[0] == [
        [case.stake_account, "y" * 32],
        {"epoch": 820, "commitment": "finalized"},
    ]
    at = rpc.block_time(first["effectiveSlot"])
    reward = solana.inflation_reward(case.stake_account, first, at)
    assert reward is not None
    assert (reward.units, reward.day) == (case.expect.units, case.expect.on)
