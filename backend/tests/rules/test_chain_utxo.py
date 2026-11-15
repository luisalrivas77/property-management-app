"""Phase 7 step 4: Bitcoin per UTXO (docs/phases/phase-7.md D185, §6 (12)), replayed from
btc_utxo.yaml through services/chain/bitcoin_esplora.to_chain_tx, rules/chain/normalise.
utxo_net and rules/chain/utxo (the reading, the movement group, the change card's answers)."""

from decimal import Decimal
from uuid import UUID, uuid4

import fixture_models_phase7 as phase7
import pytest

from hausbuch.rules import errors as E
from hausbuch.rules.chain import normalise, utxo
from hausbuch.schemas.chain import Chain, ChainTx, GroupKind
from hausbuch.schemas.household import MovementKind, MovementSource
from hausbuch.services.chain.bitcoin_esplora import to_chain_tx

BTC_FIXTURE = phase7.load("btc_utxo")
RAW = {c["id"]: c for c in phase7.raw("btc_utxo")["cases"]}
ACCOUNT = UUID("00000000-0000-0000-0000-0000000000a1")
ASSET = UUID("00000000-0000-0000-0000-0000000000b7")


def _tx(case_id: str) -> ChainTx:
    return to_chain_tx(RAW[case_id]["tx"])


def _ids(addresses) -> dict[str, UUID]:
    return {a: uuid4() for a in addresses}


def _legs(reading: utxo.UtxoReading) -> list[dict]:
    return [
        {
            "kind": leg.kind,
            "role": leg.role,
            "address": leg.address,
            "sats": leg.sats,
            "index": leg.index,
        }
        for leg in reading.legs
    ]


@pytest.mark.rule
@pytest.mark.parametrize("case", BTC_FIXTURE.cases, ids=lambda c: c.id)
def test_btc_utxo_case(case):
    assert case.pending is None
    tx = _tx(case.id)
    own = [*case.own, *case.answered_change]
    reading = utxo.read_utxo(tx, own, case.answered_payee)
    assert reading.kind is case.expect.group
    assert _legs(reading) == [m.model_dump() for m in case.expect.movements]
    assert [c.address for c in reading.candidates] == case.expect.candidates
    assert reading.card == case.expect.card

    # The movement group: one movement per leg, signed on the account, on its own address.
    ids = _ids(case.own)
    if case.answered_change:
        (change,) = case.answered_change
        change_id = uuid4()
        group = utxo.answer_change(ACCOUNT, tx, ids, ASSET, change, change_id)
        ids[change] = change_id
    elif case.answered_payee:
        (payee,) = case.answered_payee
        group = utxo.answer_payee(ACCOUNT, tx, ids, ASSET, payee)
    else:
        group = utxo.classify_utxo(ACCOUNT, tx, ids, ASSET)
    assert group.chain is Chain.BITCOIN and group.kind is case.expect.group
    assert group.tx_hash == tx.hash and group.occurred_at == tx.occurred_at
    sign = {"in": 1, "out": -1, "fee": -1}
    assert [
        (m.kind, m.quantity, m.address_id, m.index, m.source, m.tx_hash, m.asset_id)
        for m in group.movements
    ] == [
        (
            e.kind,
            Decimal(sign[e.role] * e.sats).scaleb(-8),
            ids[e.address],
            e.index,
            MovementSource.CHAIN,
            tx.hash,
            ASSET,
        )
        for e in case.expect.movements
    ]
    if case.expect.card == E.WALLET_CHANGE_CANDIDATE:
        assert group.question == (
            "Ist eine dieser Adressen Ihr Wechselgeld? bc1q…atuf (0,02 BTC), bc1q…4ngw (0,0299 BTC)"
        )
        assert "bc1qfrg03s5v" not in group.question  # never the plaintext
    else:
        assert group.question is None


@pytest.mark.rule
def test_utxo_net_through_normalise():
    own = RAW["spend_registered_change"]["own"]
    assert normalise.utxo_net(_tx("spend_registered_change"), own) == (
        1_200_000,
        5_000,
        ["bc1qfrg03s5v98wyvu393dcfdeun6pvwthnt2gatuf"],
    )
    # §6 (12): the net before the card is answered counts both unregistered outputs as sent.
    example = _tx("spend_unregistered_output_card")
    assert normalise.utxo_net(example, RAW["spend_unregistered_output_card"]["own"]) == (
        4_990_000,
        10_000,
        [
            "bc1qfrg03s5v98wyvu393dcfdeun6pvwthnt2gatuf",
            "bc1qn8duhwxyj87x0d2efhvv44p5r0f48jr6t94ngw",
        ],
    )
    # A receive: nothing left an own address, the sender paid the fee.
    assert normalise.utxo_net(_tx("receive"), RAW["receive"]["own"]) == (0, 0, [])


@pytest.mark.rule
def test_the_same_tx_reads_the_same_whichever_own_address_surfaced_it():
    """A sync asks per address, so a spend from two own addresses is read twice; the group must
    not depend on which address asked (the movement hashes dedup it, D182 (2))."""
    record = RAW["spend_from_two_own_addresses"]["tx"]
    own = RAW["spend_from_two_own_addresses"]["own"]
    first, *others = [utxo.read_utxo(to_chain_tx(record, address), own) for address in own]
    assert first.legs and all(other == first for other in others)


@pytest.mark.rule
def test_three_outputs_need_two_answers():
    """A spend with three unregistered outputs: one answer leaves two, so the card stays; the
    second settles it, and the last one left is the payee."""
    own = RAW["spend_unregistered_output_card"]["own"]
    payer = own[0]
    record = {
        "txid": "9" * 64,
        "vin": [
            {
                "txid": "8" * 64,
                "vout": 0,
                "prevout": {"scriptpubkey_address": payer, "value": 6_000_000},
            }
        ],
        "vout": [
            {"scriptpubkey_address": "bc1qpayee", "value": 1_000_000},
            {"scriptpubkey_address": "bc1qother", "value": 2_000_000},
            {"scriptpubkey_address": "bc1qchange", "value": 2_990_000},
        ],
        "fee": 10_000,
        "status": {"confirmed": True, "block_height": 900700, "block_time": 1777000000},
    }
    tx = to_chain_tx(record)
    first = utxo.read_utxo(tx, own)
    assert first.card == E.WALLET_CHANGE_CANDIDATE
    assert [(c.address, c.sats, c.vout) for c in first.candidates] == [
        ("bc1qpayee", 1_000_000, 0),
        ("bc1qother", 2_000_000, 1),
        ("bc1qchange", 2_990_000, 2),
    ]
    one = utxo.read_utxo(tx, [*own, "bc1qchange"])
    assert one.card == E.WALLET_CHANGE_CANDIDATE and len(one.candidates) == 2
    ids = _ids(own)
    group = utxo.answer_payee(ACCOUNT, tx, ids, ASSET, "bc1qother", answered_payee=[])
    assert group.kind is GroupKind.UNKNOWN
    done = utxo.answer_change(
        ACCOUNT, tx, ids, ASSET, "bc1qchange", uuid4(), answered_payee=["bc1qother"]
    )
    assert done.kind is GroupKind.TRANSFER
    assert [(m.kind, m.quantity, m.index) for m in done.movements] == [
        (MovementKind.UNCATEGORIZED, Decimal("-0.03"), "net"),
        (MovementKind.FEE, Decimal("-0.0001"), "fee"),
    ]


@pytest.mark.rule
def test_a_consolidation_moves_only_the_fee():
    own = ["bc1qa", "bc1qb"]
    record = {
        "txid": "7" * 64,
        "vin": [
            {
                "txid": "6" * 64,
                "vout": 0,
                "prevout": {"scriptpubkey_address": "bc1qa", "value": 500},
            }
        ],
        "vout": [{"scriptpubkey_address": "bc1qb", "value": 400}],
        "fee": 100,
        "status": {"confirmed": True, "block_height": 1, "block_time": 1777000000},
    }
    reading = utxo.read_utxo(to_chain_tx(record), own)
    assert reading.kind is GroupKind.TRANSFER
    assert [(leg.role, leg.sats, leg.index) for leg in reading.legs] == [("fee", 100, "fee")]


@pytest.mark.rule
def test_coinbase_and_op_return():
    """A coinbase input has no address and no value (a receive); an output without an
    address (OP_RETURN) is never a change candidate and counts as sent."""
    coinbase = {
        "txid": "5" * 64,
        "vin": [{"txid": "0" * 64, "vout": 4294967295, "prevout": None, "is_coinbase": True}],
        "vout": [{"scriptpubkey_address": "bc1qa", "value": 312_500_000}],
        "fee": 0,
        "status": {"confirmed": True, "block_height": 2, "block_time": 1777000000},
    }
    reading = utxo.read_utxo(to_chain_tx(coinbase), ["bc1qa"])
    assert [(leg.role, leg.sats, leg.index) for leg in reading.legs] == [("in", 312_500_000, "0")]
    op_return = {
        "txid": "4" * 64,
        "vin": [
            {
                "txid": "3" * 64,
                "vout": 0,
                "prevout": {"scriptpubkey_address": "bc1qa", "value": 1000},
            }
        ],
        "vout": [
            {"scriptpubkey_type": "op_return", "value": 0},
            {"scriptpubkey_address": "bc1qpayee", "value": 900},
        ],
        "fee": 100,
        "status": {"confirmed": True, "block_height": 3, "block_time": 1777000000},
    }
    tx = to_chain_tx(op_return)
    assert utxo.utxo_net(tx, ["bc1qa"]) == (900, 100, ["bc1qpayee"])
    assert utxo.read_utxo(tx, ["bc1qa"]).card is None


@pytest.mark.rule
@pytest.mark.parametrize("case", BTC_FIXTURE.balances, ids=lambda c: c.id)
def test_btc_balance(case):
    assert case.pending is None
    stats = case.chain_stats
    assert utxo.address_balance(stats.funded_txo_sum, stats.spent_txo_sum) == case.expect_sats


@pytest.mark.rule
def test_bitcoin_two_addresses_sum_per_account():
    """wallet_reconcile.yaml `bitcoin_two_addresses`: the account's Bitcoin is the sum over its
    addresses (D185 (3)), and only that sum equals Σ movements. Replayed through
    rules/chain/reconcile once task „core“ lands it (the case keeps its pending marker)."""
    (case,) = [c for c in phase7.load("wallet_reconcile").cases if c.id == "bitcoin_two_addresses"]
    parts = case.balance["BTC"]
    total = utxo.account_balance(dict(enumerate(parts)))
    assert total == sum(m.units for m in case.movements) == 2_795_000
    assert case.expect.reconciled is True


@pytest.mark.rule
def test_balance_at_backs_out_the_last_blocks():
    """Esplora's chain_stats count every confirmed block; the sync's movements stop at
    tip − 6 (D182 (4)), so a receive in the last six blocks is backed out of the balance."""
    receive = _tx("receive")  # block 900001, 1_000_000 to the address
    spend = _tx("spend_registered_change")  # block 900102, 3_000_000 from it
    address = "bc1q62vj3efj43p06q74rjqe2kmvfs4yya99s7rtxz"
    at_tip = 5_000_000
    assert utxo.balance_at(at_tip, [receive, spend], address, 900102) == at_tip
    assert utxo.balance_at(at_tip, [receive, spend], address, 900101) == at_tip + 3_000_000
    assert utxo.balance_at(at_tip, [receive, spend], address, 900000) == 7_000_000


@pytest.mark.rule
def test_btc_text():
    assert utxo.btc_text(2_990_000) == "0,0299 BTC"
    assert utxo.btc_text(100_000_000) == "1 BTC"
    assert utxo.btc_text(1) == "0,00000001 BTC"
