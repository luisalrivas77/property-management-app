"""Phase 7 step 4 wiring: `rules/chain/utxo.classify_tx`, the Bitcoin family classifier the
sync calls through rules/chain/classify (docs/phases/phase-7.md D185). Esplora-shaped records
read by `bitcoin_esplora.to_chain_tx`; every address and txid invented (btc_utxo.yaml's)."""

import hashlib
from uuid import uuid4

from hausbuch.rules import errors
from hausbuch.rules.chain.classify import ClassifyContext, classifier_for, classify
from hausbuch.rules.chain.utxo import BTC, FOREIGN_INPUTS_QUESTION, classify_tx
from hausbuch.schemas.chain import Chain, ChainFamily, GroupKind
from hausbuch.schemas.household import MovementKind
from hausbuch.services.chain.bitcoin_esplora import to_chain_tx

OWN = "bc1q62vj3efj43p06q74rjqe2kmvfs4yya99s7rtxz"
SECOND = "bc1q0phhhvur8dmwqv48kmf6d7ck3s3zkfjksuzqrl"
CHANGE = "bc1qsdqk2xmw06qgn2589yjzthjlk368wfpq4pgz2k"
PAYEE = "bc1qfrg03s5v98wyvu393dcfdeun6pvwthnt2gatuf"
STRANGER = "bc1qn8duhwxyj87x0d2efhvv44p5r0f48jr6t94ngw"


def _txid(n: int) -> str:
    return hashlib.sha256(f"invented classify tx {n}".encode()).hexdigest()


def _tx(n, ins, outs, fee, address=OWN):
    record = {
        "txid": _txid(n),
        "vin": [
            {"txid": _txid(1000 + i), "vout": 0, "prevout": {"scriptpubkey_address": a, "value": v}}
            for i, (a, v) in enumerate(ins)
        ],
        "vout": [{"scriptpubkey_address": a, "value": v} for a, v in outs],
        "fee": fee,
        "status": {"confirmed": True, "block_height": 800_000 + n, "block_time": 1_772_000_000},
    }
    return to_chain_tx(record, address)


def _ctx(own=(OWN,), others=(), answered=None, address=OWN):
    ids = {a: uuid4() for a in own}
    return ClassifyContext(
        chain=Chain.BITCOIN,
        address=address,
        own_addresses=frozenset(own) | frozenset(others),
        own_address_ids=ids,
        native_asset_id=uuid4(),
        answered_payee=answered,
    )


def test_the_dispatcher_finds_it():
    assert classifier_for(ChainFamily.UTXO) is classify_tx


def test_a_receive_is_one_in_leg_per_own_output():
    tx = _tx(1, [(STRANGER, 60_000)], [(OWN, 50_000), (SECOND, 9_000)], 1_000)
    group = classify(tx, _ctx(own=(OWN, SECOND)))
    assert group.kind is GroupKind.TRANSFER
    assert [(g.address, g.role, g.units, g.index) for g in group.legs] == [
        (OWN, "in", 50_000, "0"),
        (SECOND, "in", 9_000, "1"),
    ]
    assert all(g.asset_key == BTC for g in group.legs)


def test_a_spend_with_change_to_the_second_own_address():
    tx = _tx(2, [(OWN, 50_000)], [(PAYEE, 30_000), (SECOND, 19_000)], 1_000, address=SECOND)
    group = classify(tx, _ctx(own=(OWN, SECOND), address=SECOND))
    assert group.kind is GroupKind.TRANSFER and group.question is None
    assert [(g.address, g.role, g.kind, g.units, g.index) for g in group.legs] == [
        (OWN, "out", MovementKind.UNCATEGORIZED, 30_000, "net"),
        (OWN, "fee", MovementKind.FEE, 1_000, "fee"),
    ]


def test_a_spend_with_change_to_an_unknown_address_is_a_card_without_legs():
    tx = _tx(3, [(OWN, 50_000)], [(PAYEE, 30_000), (CHANGE, 19_000)], 1_000)
    group = classify(tx, _ctx())
    assert group.kind is GroupKind.UNKNOWN and group.legs == []
    assert group.unregistered_output
    assert group.question == (
        "Ist eine dieser Adressen Ihr Wechselgeld? bc1q…atuf (0,0003 BTC), bc1q…gz2k (0,00019 BTC)"
    )
    assert PAYEE not in group.question and CHANGE not in group.question


def test_a_no_answer_settles_its_address_only_and_two_settle_the_spend():
    tx = _tx(4, [(OWN, 50_000)], [(PAYEE, 30_000), (CHANGE, 19_000)], 1_000)
    one = classify(tx, _ctx(answered=lambda a: a == PAYEE))
    assert one.kind is GroupKind.UNKNOWN and one.question.endswith("bc1q…gz2k (0,00019 BTC)")
    assert "atuf" not in one.question
    both = classify(tx, _ctx(answered=lambda a: a in {PAYEE, CHANGE}))
    assert both.kind is GroupKind.TRANSFER and both.question is None
    assert [(g.role, g.units) for g in both.legs] == [("out", 49_000), ("fee", 1_000)]


def test_an_address_registered_later_wins_over_a_no():
    tx = _tx(5, [(OWN, 50_000)], [(PAYEE, 30_000), (CHANGE, 19_000)], 1_000)
    group = classify(tx, _ctx(own=(OWN, CHANGE), answered=lambda a: True))
    assert [(g.role, g.units) for g in group.legs] == [("out", 30_000), ("fee", 1_000)]


def test_another_wallets_address_is_never_asked_as_change():
    tx = _tx(6, [(OWN, 50_000)], [(PAYEE, 30_000), (STRANGER, 19_000)], 1_000)
    group = classify(tx, _ctx(others=(STRANGER,)))
    # the other wallet's address is settled (never this account's change); the other
    # unregistered output may still be the change, so only it is asked about
    assert group.kind is GroupKind.UNKNOWN and group.unregistered_output
    assert group.question == "Ist diese Adresse Ihr Wechselgeld? bc1q…atuf (0,0003 BTC)"
    sent = classify(tx, _ctx(others=(STRANGER,), answered=lambda a: a == PAYEE))
    assert [(g.role, g.units) for g in sent.legs] == [("out", 49_000), ("fee", 1_000)]


def test_foreign_inputs_and_untouched_transactions():
    coinjoin = _tx(7, [(OWN, 50_000), (STRANGER, 50_000)], [(PAYEE, 99_000)], 1_000)
    group = classify(coinjoin, _ctx())
    assert group.kind is GroupKind.UNKNOWN and group.legs == []
    assert group.question == FOREIGN_INPUTS_QUESTION and not group.unregistered_output
    assert errors.GROUP_UNKNOWN  # routed by rules/review as unknown, never auto
    elsewhere = _tx(8, [(STRANGER, 50_000)], [(PAYEE, 49_000)], 1_000)
    assert classify(elsewhere, _ctx()) is None
