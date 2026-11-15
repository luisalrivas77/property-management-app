"""Phase 7 step 3: EVM records to movements (docs/phases/phase-7.md D181 (b), (c), D183, D186
(2), D187). Replays evm_tx.yaml's step 3 cases through `normalise` and `classify_evm` (the
receipt cases through `deposit_from_receipt`), spam.yaml's step 3 cases through
`spam_verdict`, and wallet_reconcile.yaml's balance-only cases through
`reconcile_balance_only`.

Not replayed here: `exchange_withdrawal_by_hash` and `bsc_withdrawal_without_hash` (step 5);
`bsc_balance_only` and `key_unset` (the sources, tests/services/test_chain_evm_sources.py);
the link a receipt case expects (step 5, its marker narrowed to it); wallet_reconcile.yaml's
EVM cases with movements, which replay through rules/chain/reconcile.reconcile (task "core")
and skip while it is not built.
"""

from datetime import UTC, datetime
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from uuid import uuid4

import pytest
from fixture_models_phase7 import load

from hausbuch.rules import errors as E
from hausbuch.rules.chain import evm
from hausbuch.rules.chain.decoders import public_decoders
from hausbuch.schemas.chain import AssetKey, Chain, GroupKind
from hausbuch.schemas.household import MovementKind, MovementSource

EVM = {c.id: c for c in load("evm_tx").cases}
SPAM = {c.id: c for c in load("spam").cases}
RECONCILE = {c.id: c for c in load("wallet_reconcile").cases}

ETHERSCAN_CASES = [
    "native_receive",
    "native_send_with_fee",
    "failed_fee_only",
    "erc20_in",
    "erc20_out",
    "internal_in",
    "spam_token",
    "wrap_weth",
    "fee_below_numeric_scale",
]
RECEIPT_CASES = [
    "bsc_withdrawal_receipt_native",
    "bsc_withdrawal_receipt_bep20",
    "bsc_withdrawal_receipt_other_address",
]
BALANCE_ONLY_EVM = [
    "bsc_linked_deposits_equal",
    "bsc_balance_below_deposits",
    "bsc_balance_above_deposits",
]
SPAM_CASES = [
    "unknown_token_no_fee",
    "unknown_token_promoted",
    "symbol_lookalike_usdc",
    "fee_paid_never_spam",
    "held_token_not_spam",
    "registry_token_airdropped",
    "symbol_lookalike_homoglyph",
]
RECONCILE_MOVEMENTS = ["evm_equal", "evm_missing_fee", "spam_token_not_asked"]
RECONCILE_BALANCE_ONLY = ["bsc_balance_only_equal", "bsc_balance_only_below_deposits"]


def _legs(group: evm.EvmGroup) -> list[tuple]:
    return [(g.kind, g.role, g.units, g.index, g.asset_key.contract) for g in group.legs]


def _expected(case) -> list[tuple]:
    return [(m.kind, m.role, m.units, m.index, m.contract) for m in case.expect.movements]


def _exchange_hashes(case) -> set[str]:
    leg = case.exchange_leg or {}
    return {str(leg["tx_hash"])} if leg.get("tx_hash") else set()


@pytest.mark.parametrize("case_id", ETHERSCAN_CASES)
def test_etherscan_records(case_id):
    case = EVM[case_id]
    me = case.own[0]
    txs = evm.normalise(case.chain, case.records, me)  # type: ignore[arg-type]
    assert len(txs) == 1
    (tx,) = txs
    if case.expect.fee_wei is not None:
        assert tx.fee == (AssetKey(chain=case.chain, contract=None), case.expect.fee_wei)
    group = evm.classify_evm(case.chain, tx, me, own=case.own)
    assert group.kind is case.expect.group
    assert _legs(group) == _expected(case)
    assert group.card == case.expect.card
    if case.expect.link is not None:
        assert group.link is case.expect.link


@pytest.mark.parametrize("case_id", RECEIPT_CASES)
def test_bsc_deposit_by_receipt(case_id):
    case = EVM[case_id]
    me = case.own[0]
    at = datetime(2026, 3, 1, tzinfo=UTC)
    tx = evm.deposit_from_receipt(
        case.chain,
        case.records["transaction"],  # type: ignore[arg-type]
        case.records["receipt"],  # type: ignore[arg-type]
        at,
        me,
    )
    if not case.expect.movements:
        assert tx is None
        return
    assert tx is not None and tx.fee is None
    group = evm.classify_evm(case.chain, tx, me, exchange_hashes=_exchange_hashes(case))
    assert group.kind is case.expect.group
    assert _legs(group) == _expected(case)


@pytest.mark.parametrize("case_id", BALANCE_ONLY_EVM)
def test_bsc_balance_against_linked_deposits(case_id):
    case = EVM[case_id]
    balance = int(str(case.records["eth_getBalance"]), 16)
    deposits = int(str(case.records["linked_deposits"]))
    got = evm.reconcile_balance_only({"BNB": balance}, {"BNB": deposits})
    assert got.reconciled is case.expect.reconciled
    assert got.card == case.expect.card


@pytest.mark.parametrize("case_id", RECONCILE_BALANCE_ONLY)
def test_reconcile_balance_only_cases(case_id):
    case = RECONCILE[case_id]
    assert case.balance_only
    have = {a: sum(parts) for a, parts in case.balance.items()}
    got = evm.reconcile_balance_only(have, case.linked_deposits)
    assert got.reconciled is case.expect.reconciled
    assert got.difference == case.expect.difference
    assert got.card == case.expect.card


@pytest.mark.parametrize("case_id", RECONCILE_MOVEMENTS)
def test_reconcile_evm_movements(case_id):
    from hausbuch.rules.chain import reconcile

    case = RECONCILE[case_id]
    sums: dict[AssetKey, int] = {}
    keys: dict[str, AssetKey] = {}
    for m in case.movements:
        if m.spam:
            continue  # D187 (1): a spam token's movement is not counted
        key = keys.setdefault(m.asset, AssetKey(chain=case.chain, contract=f"0x{len(keys):040x}"))
        sums[key] = sums.get(key, 0) + m.units
    balances = {
        keys.setdefault(a, AssetKey(chain=case.chain, contract=f"0x{len(keys):040x}")): sum(p)
        for a, p in case.balance.items()
    }
    try:
        diff = reconcile.reconcile(sums, balances)
    except NotImplementedError:
        pytest.skip("rules/chain/reconcile.reconcile is task core's, not built yet")
    names = {k: a for a, k in keys.items()}
    assert {names[k]: d for k, d in diff.items()} == case.expect.difference
    assert (not diff) is case.expect.reconciled


@pytest.mark.parametrize("case_id", SPAM_CASES)
def test_spam_verdict(case_id):
    case = SPAM[case_id]
    got = evm.spam_verdict(
        case.token.chain,
        case.token.contract,
        case.token.symbol,
        held=case.held,
        fee_paid=case.fee_paid,
        bought=case.bought,
        action=case.action,
    )
    assert got.model_dump() == case.expect.model_dump()


# --- beyond the fixtures --------------------------------------------------------------------

ME = "0x627f3684df28ba04ebecf523c815218a0f4346f6"
OTHER_OWN = "0x1111111111111111111111111111111111111111"
FOREIGN = "0x96d82db02c1fdeb860fcb6c58f11625348b9b831"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
NFT = "0x2222222222222222222222222222222222222222"
H = "0x" + "ab" * 32


def _row(**kw):
    base = {"hash": H, "blockNumber": "100", "timeStamp": "1773000000", "isError": "0"}
    return base | kw


def test_transfer_between_own_addresses_is_internal():
    rows = {"txlist": [_row(**{"from": ME, "to": OTHER_OWN}, value="5", gasUsed="1", gasPrice="2")]}
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME, own=[ME, OTHER_OWN])
    assert group.kind is GroupKind.TRANSFER and group.card is None
    assert [(g.kind, g.role) for g in group.legs] == [
        (MovementKind.INTERNAL_TRANSFER_OUT, "out"),
        (MovementKind.FEE, "fee"),
    ]


def test_nft_and_1155_records_are_spam_legs():
    rows = {
        "tokennfttx": [_row(**{"from": FOREIGN, "to": ME}, contractAddress=NFT, tokenID="7")],
        "token1155tx": [
            _row(**{"from": FOREIGN, "to": ME}, contractAddress=NFT, tokenID="9", tokenValue="3")
        ],
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert group.kind is GroupKind.SPAM
    assert [(g.kind, g.units) for g in group.legs] == [
        (MovementKind.SPAM, 1),
        (MovementKind.SPAM, 3),
    ]
    assert all(g.index.startswith("nft:") for g in group.legs)
    assert evm.signed_units(group) == {}


def test_a_token_row_without_log_index_gets_a_stable_index():
    row = _row(**{"from": FOREIGN, "to": ME}, contractAddress=USDC, value="10")
    twice = {"tokentx": [row, dict(row)]}
    (tx,) = evm.normalise(Chain.ETHEREUM, twice, ME)
    first, second = (t.index for t in tx.transfers)
    assert first.startswith("log:") and second == f"{first}:2"
    (again,) = evm.normalise(Chain.ETHEREUM, twice, ME)
    assert [t.index for t in again.transfers] == [first, second]


def test_a_failed_internal_call_moves_nothing():
    rows = {
        "txlistinternal": [_row(**{"from": FOREIGN, "to": ME}, value="9", traceId="0", isError="1")]
    }
    assert evm.normalise(Chain.ARBITRUM, rows, ME) == []  # no fee, no unit moved: no group


def test_a_zero_value_token_row_is_no_group():
    # an address-poisoning transfer: a lookalike sender moves 0 units in the address's name
    rows = {"tokentx": [_row(**{"from": ME, "to": FOREIGN}, contractAddress=USDC, value="0")]}
    assert evm.normalise(Chain.ETHEREUM, rows, ME) == []


def test_a_third_partys_failed_call_to_the_address_is_no_group():
    rows = {"txlist": [_row(**{"from": FOREIGN, "to": ME}, value="5", isError="1")]}
    assert evm.normalise(Chain.ETHEREUM, rows, ME) == []


def test_a_send_to_itself_is_its_fee_only():
    rows = {"txlist": [_row(**{"from": ME, "to": ME}, value="5", gasUsed="2", gasPrice="3")]}
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert group.kind is GroupKind.TRANSFER and group.fee_only and group.card is None
    assert [(g.kind, g.units, g.index) for g in group.legs] == [(MovementKind.FEE, 6, "fee")]


def test_a_minted_registry_token_is_an_airdrop():
    rows = {
        "tokentx": [_row(**{"from": evm.ZERO_ADDRESS, "to": ME}, contractAddress=USDC, value="5")]
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert group.kind is GroupKind.AIRDROP
    assert group.legs[0].kind is MovementKind.AIRDROP


def test_an_approval_is_a_fee_only_contract_call():
    rows = {
        "txlist": [
            _row(
                **{"from": ME, "to": USDC},
                value="0",
                gasUsed="3",
                gasPrice="5",
                input="0x095ea7b3" + "0" * 128,
            )
        ]
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert group.kind is GroupKind.CONTRACT_CALL and group.fee_only
    assert [g.kind for g in group.legs] == [MovementKind.FEE]
    assert tx.program_calls == [(USDC, "0x095ea7b3")]


def test_unwrap_by_withdraw():
    wad = 10**18
    data = evm.WITHDRAW_SELECTOR + f"{wad:064x}"
    rows = {
        "txlist": [
            _row(**{"from": ME, "to": WETH}, value="0", gasUsed="2", gasPrice="3", input=data)
        ],
        "txlistinternal": [_row(**{"from": WETH, "to": ME}, value=str(wad), traceId="0")],
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME, decoders=public_decoders())
    assert group.kind is GroupKind.WRAP
    assert _legs(group) == [
        (MovementKind.INTERNAL_TRANSFER_OUT, "out", wad, "withdraw", WETH),
        (MovementKind.INTERNAL_TRANSFER_IN, "in", wad, "internal:0", None),
        (MovementKind.FEE, "fee", 6, "fee", None),
    ]


def test_a_signed_swap_by_shape():
    rows = {
        "txlist": [
            _row(
                **{"from": ME, "to": FOREIGN},
                value="7",
                gasUsed="1",
                gasPrice="1",
                input="0x12345678",
            )
        ],
        "tokentx": [
            _row(**{"from": FOREIGN, "to": ME}, contractAddress=USDC, value="9", logIndex="4")
        ],
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert group.kind is GroupKind.SWAP and group.decoder is None
    assert [g.kind for g in group.legs] == [
        MovementKind.DISPOSAL,
        MovementKind.ACQUISITION,
        MovementKind.FEE,
    ]


def test_to_draft_signs_and_scales():
    case = EVM["erc20_out"]
    (tx,) = evm.normalise(case.chain, case.records, case.own[0])  # type: ignore[arg-type]
    group = evm.classify_evm(case.chain, tx, case.own[0])
    usdc, eth = uuid4(), uuid4()
    assets = {
        AssetKey(chain=Chain.ETHEREUM, contract=USDC): (usdc, 6),
        AssetKey(chain=Chain.ETHEREUM, contract=None): (eth, 18),
    }
    draft = evm.to_draft(group, uuid4(), uuid4(), assets)
    got = [(m.asset_id, m.quantity, m.index, m.source) for m in draft.movements]
    assert got == [
        (usdc, Decimal("-100"), "40", MovementSource.CHAIN),
        (eth, Decimal("-0.0006"), "fee", MovementSource.CHAIN),
    ]
    assert [str(m.quantity) for m in draft.movements] == ["-100", "-0.0006"]  # never 1E+2


def test_receipt_ignores_a_four_topic_transfer():
    me = "0xed052f45caae52c75f9087ae2440d534765570e1"
    pad = "0x" + "0" * 24
    receipt = {
        "status": "0x1",
        "logs": [
            {
                "address": NFT,
                "logIndex": "0x1",
                "topics": [
                    evm.TRANSFER_TOPIC,
                    pad + FOREIGN[2:],
                    pad + me[2:],
                    "0x" + "0" * 63 + "1",
                ],
                "data": "0x",
            }
        ],
    }
    transaction = {"hash": H, "from": FOREIGN, "to": NFT, "value": "0x0", "blockNumber": "0x1"}
    assert evm.deposit_from_receipt(Chain.BSC, transaction, receipt, datetime.now(UTC), me) is None


def test_balance_only_card_codes_exist():
    assert E.WALLET_COINS_LEFT == "wallet.coins_left"
    assert E.TRANSFER_UNMATCHED_LEG == "transfer.unmatched_leg"


def test_not_an_evm_chain():
    with pytest.raises(ValueError):
        evm.normalise(Chain.SOLANA, {}, ME)


# --- review fixes (PR #371) -----------------------------------------------------------------

UNLISTED = "0x3333333333333333333333333333333333333333"  # in no registry, held by no one
JUNK = "0x4444444444444444444444444444444444444444"  # another unlisted token
PAD = "0x" + "0" * 24


def test_an_exchange_hash_wins_over_spam_on_etherscan():
    # D183 (3)(b) before D187 (1): an unlisted, unheld token the exchange sent is the deposit
    rows = {
        "tokentx": [
            _row(**{"from": FOREIGN, "to": ME}, contractAddress=UNLISTED, value="5", logIndex="2")
        ],
        "tokennfttx": [_row(**{"from": FOREIGN, "to": ME}, contractAddress=NFT, tokenID="1")],
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME, exchange_hashes={H.upper()})
    assert group.kind is GroupKind.EXCHANGE_TRANSFER and group.card is None
    assert [(g.kind, g.role, g.asset_key.contract) for g in group.legs] == [
        (MovementKind.INTERNAL_TRANSFER_IN, "in", UNLISTED),
        (MovementKind.SPAM, "in", NFT),  # the other junk in the same transaction stays spam
    ]
    # without the hash the same token is spam
    alone = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert alone.kind is GroupKind.SPAM


def test_an_exchange_hash_wins_over_spam_on_the_bsc_receipt():
    receipt = {
        "status": "0x1",
        "logs": [
            {
                "address": UNLISTED,
                "logIndex": "0x3",
                "topics": [evm.TRANSFER_TOPIC, PAD + FOREIGN[2:], PAD + ME[2:]],
                "data": "0x" + f"{7:064x}",
            }
        ],
    }
    transaction = {"hash": H, "from": FOREIGN, "to": UNLISTED, "value": "0x0", "blockNumber": "0x1"}
    tx = evm.deposit_from_receipt(Chain.BSC, transaction, receipt, datetime.now(UTC), ME)
    assert tx is not None
    group = evm.classify_evm(Chain.BSC, tx, ME, exchange_hashes={H})
    assert group.kind is GroupKind.EXCHANGE_TRANSFER
    assert [(g.kind, g.units, g.index) for g in group.legs] == [
        (MovementKind.INTERNAL_TRANSFER_IN, 7, "3")
    ]
    # without the hash the same token is spam
    assert evm.classify_evm(Chain.BSC, tx, ME).kind is GroupKind.SPAM


def test_an_unlisted_token_from_another_own_address_is_a_transfer():
    rows = {
        "tokentx": [
            _row(
                **{"from": OTHER_OWN, "to": ME}, contractAddress=UNLISTED, value="9", logIndex="1"
            ),
            _row(**{"from": FOREIGN, "to": ME}, contractAddress=JUNK, value="4", logIndex="2"),
        ],
        "tokennfttx": [_row(**{"from": FOREIGN, "to": ME}, contractAddress=NFT, tokenID="4")],
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME, own=[ME, OTHER_OWN])
    assert group.kind is GroupKind.TRANSFER and group.card is None
    # a third party's junk in the same transaction stays spam: the NFT and the unlisted token
    assert sorted((g.kind, g.asset_key.contract) for g in group.legs) == sorted(
        [
            (MovementKind.INTERNAL_TRANSFER_IN, UNLISTED),
            (MovementKind.SPAM, NFT),
            (MovementKind.SPAM, JUNK),
        ]
    )
    # seen alone, without the other own address, it is spam
    assert evm.classify_evm(Chain.ETHEREUM, tx, ME).kind is GroupKind.SPAM


def test_a_fake_token_sent_out_in_an_unsigned_transaction_is_spam():
    # address poisoning: a fake token's contract emits Transfer(victim → lookalike); the
    # address never signed it, so the out leg is spam and asks nothing
    rows = {
        "tokentx": [
            _row(**{"from": ME, "to": FOREIGN}, contractAddress=UNLISTED, value="5", logIndex="1")
        ]
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    assert tx.fee is None
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert group.kind is GroupKind.SPAM and group.card is None
    assert [(g.kind, g.role) for g in group.legs] == [(MovementKind.SPAM, "out")]
    assert evm.signed_units(group) == {}


def test_a_signed_send_of_an_unlisted_token_is_no_spam():
    rows = {
        "txlist": [_row(**{"from": ME, "to": UNLISTED}, value="0", gasUsed="1", gasPrice="1")],
        "tokentx": [
            _row(**{"from": ME, "to": FOREIGN}, contractAddress=UNLISTED, value="5", logIndex="1")
        ],
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME)
    assert group.kind is GroupKind.TRANSFER and group.card == E.TRANSFER_UNMATCHED_LEG


def test_an_unlisted_token_out_of_a_held_asset_is_no_spam():
    rows = {
        "tokentx": [
            _row(**{"from": ME, "to": FOREIGN}, contractAddress=UNLISTED, value="5", logIndex="1")
        ]
    }
    (tx,) = evm.normalise(Chain.ETHEREUM, rows, ME)
    held = [AssetKey(chain=Chain.ETHEREUM, contract=UNLISTED)]
    group = evm.classify_evm(Chain.ETHEREUM, tx, ME, held=held)
    assert group.kind is GroupKind.TRANSFER and group.card == E.TRANSFER_UNMATCHED_LEG


@pytest.mark.parametrize("data", ["0x", "", None])
def test_a_receipt_transfer_with_empty_data_is_skipped(data):
    receipt = {
        "status": "0x1",
        "logs": [
            {
                "address": USDC,
                "logIndex": "0x1",
                "topics": [evm.TRANSFER_TOPIC, PAD + FOREIGN[2:], PAD + ME[2:]],
                "data": data,
            }
        ],
    }
    transaction = {"hash": H, "from": FOREIGN, "to": USDC, "value": "0x0", "blockNumber": "0x1"}
    assert evm.deposit_from_receipt(Chain.BSC, transaction, receipt, datetime.now(UTC), ME) is None


@pytest.mark.parametrize("digits", [29, 38, 78])
@pytest.mark.parametrize("decimals", [0, 6, 18])
def test_quantity_is_exact_beyond_the_context_precision(digits, decimals):
    units = int("9" * (digits - 1) + "7")
    quantity = evm._quantity(units, decimals)
    with localcontext() as ctx:
        ctx.prec = 200
        assert quantity * Decimal(10) ** decimals == units
    assert quantity.as_tuple().exponent <= 0


def test_quantity_of_the_largest_uint256_is_exact():
    units = 2**256 - 1
    with localcontext() as ctx:
        ctx.traps[InvalidOperation] = True
        ctx.traps[Inexact] = True
        quantity = evm._quantity(units, 18)
    assert quantity.as_tuple().digits == tuple(int(d) for d in str(units))
    assert quantity.as_tuple().exponent == -18


def test_quantity_drops_trailing_zeros_only_down_to_the_unit():
    assert str(evm._quantity(10**20, 18)) == "100"
    assert str(evm._quantity(1500, 3)) == "1.5"
    assert str(evm._quantity(600, 0)) == "600"
    assert str(evm._quantity(-6 * 10**14, 18)) == "-0.0006"
