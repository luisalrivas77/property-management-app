"""Phase 7 step 2 (core): the movement hash, the decoder precedence, the reconciliation and the
group routing replay their fixture cases (docs/phases/phase-7.md D182 (2), D186, D191)."""

import pytest
from fixture_models_phase2 import load as load_phase2
from fixture_models_phase7 import load

from hausbuch.rules.chain import movement_hash
from hausbuch.rules.chain.decoders import decoder_for, decoder_lookup
from hausbuch.rules.chain.reconcile import reconcile, sum_balance_parts, sum_movements
from hausbuch.rules.errors import WALLET_UNRECONCILED
from hausbuch.rules.review import route_group
from hausbuch.schemas.chain import AssetKey, Chain, DecoderKind, DecoderRow

HASHES = load("movement_hash")
DECODERS = load("decoders")
RECONCILE = load("wallet_reconcile")
ROUTING = load_phase2("review_routing")

_STEP2 = "phase 7 step 2"


@pytest.mark.parametrize("case", HASHES.cases, ids=lambda c: c.id)
def test_movement_hash(case):
    assert case.pending is None
    got = movement_hash(case.chain, case.tx_hash, case.address_hmac, case.index, case.role)
    assert got == case.hash


def test_movement_hash_refuses_an_unknown_role():
    with pytest.raises(ValueError):
        movement_hash(Chain.SOLANA, "x", "a" * 64, "fee", "gift")


@pytest.mark.parametrize("case", DECODERS.override_cases, ids=lambda c: c.id)
def test_decoder_precedence(case):
    assert case.pending is None
    found = decoder_lookup(
        case.call.chain, case.call.program, case.call.method, case.workspace, case.public
    )
    if case.expect.kind is None:
        assert found is None
    else:
        row, source = found
        assert (row.kind, source) == (case.expect.kind, case.expect.source)
    row = decoder_for(
        case.call.chain, case.call.program, case.call.method, case.workspace, case.public
    )
    assert (row.kind if row else None) == case.expect.kind


def test_decoder_for_reads_the_public_file_by_default():
    row = decoder_for(Chain.SOLANA, "Stake11111111111111111111111111111111111111", None)
    assert row is not None and row.kind is DecoderKind.STAKE


def test_decoder_for_lower_cases_an_evm_program():
    public = [
        DecoderRow(
            chain=Chain.ETHEREUM,
            program="0xbac1b60215808a2bce58af79feb1efcaebbcdb3a",
            kind=DecoderKind.SWAP,
            legs="swap",
        )
    ]
    row = decoder_for(
        Chain.ETHEREUM, "0xBAC1B60215808A2BCE58AF79FEB1EFCAEBBCDB3A", "0x7ff36ab5", (), public
    )
    assert row is not None and row.kind is DecoderKind.SWAP


RECONCILE_STEP2 = [c for c in RECONCILE.cases if c.pending is None]


def test_the_step2_reconcile_case_is_replayed():
    assert "proposed_group_counted" in {c.id for c in RECONCILE_STEP2}


@pytest.mark.parametrize("case", RECONCILE_STEP2, ids=lambda c: c.id)
def test_reconcile(case):
    assert not case.balance_only  # the balance-only rule is step 3's
    movements = sum_movements((m.asset, m.units) for m in case.movements if not m.spam)
    balances = sum_balance_parts((a, u) for a, parts in case.balance.items() for u in parts)
    diff = reconcile(movements, balances)
    assert (not diff) == case.expect.reconciled
    assert diff == case.expect.difference
    if diff:
        assert case.expect.card == WALLET_UNRECONCILED


def test_reconcile_by_asset_key():
    sol = AssetKey(chain=Chain.SOLANA)
    usdc = AssetKey(chain=Chain.SOLANA, contract="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    assert reconcile({sol: 10, usdc: 5}, {sol: 10, usdc: 5}) == {}
    # an asset held but never moved, and one moved but no longer held
    assert reconcile({sol: 10}, {sol: 10, usdc: 3}) == {usdc: 3}
    assert reconcile({sol: 10, usdc: 3}, {sol: 10}) == {usdc: -3}


@pytest.mark.parametrize("case", ROUTING.chain_cases, ids=lambda c: c.id)
def test_route_group(case):
    assert case.pending is None
    assert route_group(case.item) == case.expect


def test_no_core_case_left_pending():
    for cases in (HASHES.cases, DECODERS.override_cases, ROUTING.chain_cases):
        assert not [c.id for c in cases if c.pending and c.pending.startswith(_STEP2)]
