"""Phase 7 D261 (2): a plain send names its one external counterparty (in memory only), and a
send to an address the workspace learned as an exchange's deposit address is classified as an
`exchange_transfer` with an `internal_transfer_out` leg. Every address and hash invented, of
the right format."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from hausbuch.rules.chain.classify import (
    ChainGroup,
    ChainLeg,
    ClassifyContext,
    classify,
    learned_deposit,
    plain_send,
)
from hausbuch.rules.chain.solana import recipient
from hausbuch.schemas.chain import AssetKey, Chain, ChainTx, GroupKind, RawTransfer
from hausbuch.schemas.household import MovementKind
from hausbuch.services.chain.bitcoin_esplora import to_chain_tx

WHEN = datetime(2026, 5, 4, 10, tzinfo=UTC)
ETH = AssetKey(chain=Chain.ETHEREUM)
OWN_EVM = "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
EXCHANGE_EVM = "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359"
SECOND_EVM = "0xdbf03b407c01e7cd3cbea99509d93f8dddc8c6fb"


def _evm(*transfers: RawTransfer, fee=21_000 * 10**9) -> ChainTx:
    return ChainTx(
        hash="0x" + hashlib.sha256(repr(transfers).encode()).hexdigest(),
        block=19_000_000,
        occurred_at=WHEN,
        status="ok",
        fee=(ETH, fee),
        transfers=list(transfers),
    )


def _evm_ctx(**overrides) -> ClassifyContext:
    return ClassifyContext(
        chain=Chain.ETHEREUM,
        address=OWN_EVM,
        own_addresses=frozenset({OWN_EVM}),
        own_address_ids={OWN_EVM: uuid4()},
        native_asset_id=uuid4(),
        **overrides,
    )


def _send(to: str, units=10**18, index="0") -> RawTransfer:
    return RawTransfer(asset_key=ETH, from_addr=OWN_EVM, to_addr=to, units=units, index=index)


def test_an_evm_plain_send_names_its_recipient_and_stays_a_transfer_when_nothing_is_learned():
    group = classify(_evm(_send(EXCHANGE_EVM)), _evm_ctx())
    assert group.kind is GroupKind.TRANSFER
    (out,) = [g for g in group.legs if g.role == "out"]
    assert (out.kind, out.counterparty) == (MovementKind.UNCATEGORIZED, EXCHANGE_EVM)
    assert all(g.counterparty is None for g in group.legs if g.role != "out")
    assert EXCHANGE_EVM not in repr(out)  # in memory only, never in a repr or a log


def test_an_evm_send_to_a_learned_deposit_address_is_an_exchange_transfer():
    group = classify(_evm(_send(EXCHANGE_EVM)), _evm_ctx(exchange_deposit=EXCHANGE_EVM.__eq__))
    assert group.kind is GroupKind.EXCHANGE_TRANSFER
    kinds = sorted((g.role, g.kind) for g in group.legs)
    assert kinds == [("fee", MovementKind.FEE), ("out", MovementKind.INTERNAL_TRANSFER_OUT)]


def test_an_evm_send_to_another_address_is_untouched_by_a_learned_one():
    group = classify(_evm(_send(SECOND_EVM)), _evm_ctx(exchange_deposit=EXCHANGE_EVM.__eq__))
    assert group.kind is GroupKind.TRANSFER


def test_a_receipt_names_no_counterparty():
    receipt = RawTransfer(
        asset_key=ETH, from_addr=EXCHANGE_EVM, to_addr=OWN_EVM, units=5, index="0"
    )
    group = classify(_evm(receipt, fee=0), _evm_ctx(exchange_deposit=EXCHANGE_EVM.__eq__))
    assert group.kind is not GroupKind.EXCHANGE_TRANSFER
    assert all(g.counterparty is None for g in group.legs)


# --- learned_deposit on a group, whatever its family ----------------------------------------


def _group(*legs: ChainLeg, kind=GroupKind.TRANSFER, question=None) -> ChainGroup:
    return ChainGroup(
        tx_hash="h", occurred_at=WHEN, block=1, kind=kind, legs=list(legs), question=question
    )


OUT = ChainLeg(ETH, 10, "out", MovementKind.UNCATEGORIZED, "0", counterparty="x")
FEE = ChainLeg(ETH, 1, "fee", MovementKind.FEE, "fee")
ALWAYS = ClassifyContext(chain=Chain.ETHEREUM, address="a", exchange_deposit=lambda _: True)


def test_only_a_plain_send_with_a_counterparty_is_relabelled():
    assert plain_send(_group(OUT, FEE)) is OUT
    unnamed = ChainLeg(ETH, 10, "out", MovementKind.UNCATEGORIZED, "0")
    two_outs = ChainLeg(ETH, 3, "out", MovementKind.UNCATEGORIZED, "1", counterparty="y")
    receipt = ChainLeg(ETH, 3, "in", MovementKind.UNCATEGORIZED, "1")
    for group in (
        _group(unnamed, FEE),
        _group(OUT, two_outs, FEE),
        _group(OUT, receipt),
        _group(OUT, FEE, question="?"),
        _group(OUT, FEE, kind=GroupKind.SWAP),
    ):
        assert plain_send(group) is None
        assert learned_deposit(group, ALWAYS) is group


def test_no_learned_address_leaves_the_group_alone():
    group = _group(OUT, FEE)
    assert learned_deposit(group, ClassifyContext(chain=Chain.ETHEREUM, address="a")) is group


# --- Bitcoin -----------------------------------------------------------------------------------

OWN_BTC = "bc1q62vj3efj43p06q74rjqe2kmvfs4yya99s7rtxz"
CHANGE_BTC = "bc1q0phhhvur8dmwqv48kmf6d7ck3s3zkfjksuzqrl"
EXCHANGE_BTC = "bc1qfrg03s5v98wyvu393dcfdeun6pvwthnt2gatuf"
OTHER_BTC = "bc1qn8duhwxyj87x0d2efhvv44p5r0f48jr6t94ngw"


def _btc(ins, outs, fee=1_000):
    txid = hashlib.sha256(repr((ins, outs)).encode()).hexdigest()
    record = {
        "txid": txid,
        "vin": [
            {"txid": f"{i:064x}", "vout": 0, "prevout": {"scriptpubkey_address": a, "value": v}}
            for i, (a, v) in enumerate(ins)
        ],
        "vout": [{"scriptpubkey_address": a, "value": v} for a, v in outs],
        "fee": fee,
        "status": {"confirmed": True, "block_height": 800_000, "block_time": 1_772_000_000},
    }
    return to_chain_tx(record, OWN_BTC)


def _btc_ctx(**overrides) -> ClassifyContext:
    own = (OWN_BTC, CHANGE_BTC)
    return ClassifyContext(
        chain=Chain.BITCOIN,
        address=OWN_BTC,
        own_addresses=frozenset(own),
        own_address_ids={a: uuid4() for a in own},
        native_asset_id=uuid4(),
        **overrides,
    )


def test_a_bitcoin_spend_with_own_change_names_its_one_payee_and_is_learned():
    tx = _btc([(OWN_BTC, 50_000)], [(EXCHANGE_BTC, 30_000), (CHANGE_BTC, 19_000)])
    group = classify(tx, _btc_ctx(exchange_deposit=EXCHANGE_BTC.__eq__))
    assert group.kind is GroupKind.EXCHANGE_TRANSFER
    (out,) = [g for g in group.legs if g.role == "out"]
    assert (out.kind, out.counterparty) == (MovementKind.INTERNAL_TRANSFER_OUT, EXCHANGE_BTC)


def test_a_bitcoin_spend_paying_two_outputs_names_no_one():
    tx = _btc([(OWN_BTC, 50_000)], [(EXCHANGE_BTC, 30_000), (OTHER_BTC, 19_000)], fee=1_000)
    group = classify(tx, _btc_ctx(answered_payee=lambda a: a in (EXCHANGE_BTC, OTHER_BTC)))
    assert all(g.counterparty is None for g in group.legs)
    assert group.kind is GroupKind.TRANSFER


# --- Solana: the recipient from the record's balances ---------------------------------------

A = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"
F = "ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J"
G = "Dd2nAL9RJk7UUMMSAWszC9oJTi3jGoPGpjStUZ3ZH8ZU"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL = AssetKey(chain=Chain.SOLANA)
USDC_KEY = AssetKey(chain=Chain.SOLANA, contract=USDC)


def _sol(keys, pre, post, pre_tokens=(), post_tokens=()):
    return {
        "transaction": {"message": {"accountKeys": list(keys)}},
        "meta": {
            "preBalances": list(pre),
            "postBalances": list(post),
            "preTokenBalances": list(pre_tokens),
            "postTokenBalances": list(post_tokens),
        },
    }


def _token(index, owner, amount):
    row = {"accountIndex": index, "mint": USDC, "uiTokenAmount": {"amount": str(amount)}}
    return row | ({"owner": owner} if owner else {})


def test_solana_sol_send_names_the_one_credited_key():
    record = _sol([A, F, "11111111111111111111111111111111"], [10_000, 0, 1], [4_995, 5_000, 1])
    assert recipient(record, A, SOL, 5_000) == F
    assert recipient(record, A, SOL, 4_000) is None  # the credit is not the send's units


def test_solana_sol_send_to_two_keys_names_no_one():
    record = _sol([A, F, G], [10_000, 0, 0], [4_995, 2_500, 2_500])
    assert recipient(record, A, SOL, 2_500) is None


def test_solana_token_send_names_the_owner_of_the_credited_token_account():
    record = _sol(
        [A, "tokA", "tokF"],
        [10_000, 2_039_280, 0],
        [5_000, 2_039_280, 2_039_280],
        pre_tokens=[_token(1, A, 900)],
        post_tokens=[_token(1, A, 400), _token(2, F, 500)],
    )
    assert recipient(record, A, USDC_KEY, 500) == F


def test_solana_token_row_without_an_owner_names_no_one():
    record = _sol(
        [A, "tokA", "tokF"],
        [10_000, 0, 0],
        [5_000, 0, 0],
        pre_tokens=[_token(1, A, 900)],
        post_tokens=[_token(1, A, 400), _token(2, None, 500)],
    )
    assert recipient(record, A, USDC_KEY, 500) is None
