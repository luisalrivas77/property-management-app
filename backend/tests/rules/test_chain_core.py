"""Phase 7 step 2 (core): the movement hash, the decoder precedence, the reconciliation and the
group routing replay their fixture cases (docs/phases/phase-7.md D182 (2), D186, D191)."""

from datetime import UTC, datetime

import pytest
from fixture_models_phase2 import load as load_phase2
from fixture_models_phase7 import load

from hausbuch.rules.chain import movement_hash
from hausbuch.rules.chain.classify import (
    ChainGroup,
    ChainLeg,
    ClassifyContext,
    classifier_for,
    classify,
    register_family,
)
from hausbuch.rules.chain.decoders import decoder_for, decoder_lookup
from hausbuch.rules.chain.reconcile import reconcile, sum_balance_parts, sum_movements
from hausbuch.rules.chain.spam import verdict
from hausbuch.rules.errors import (
    WALLET_SOURCE_REFUSED,
    WALLET_SOURCE_THROTTLED,
    WALLET_UNRECONCILED,
)
from hausbuch.rules.review import route_group
from hausbuch.schemas.chain import (
    AssetKey,
    Chain,
    ChainFamily,
    ChainSourceKind,
    ChainTx,
    DecoderKind,
    DecoderRow,
    GroupKind,
)
from hausbuch.schemas.household import MovementKind
from hausbuch.services.chain.base import (
    WALLET_SOURCE_FAILED,
    RequestBudget,
    error_from,
    host_of,
    register_source,
    source_for,
)
from hausbuch.services.prices.http import SourceError
from hausbuch.settings import Settings

HASHES = load("movement_hash")
DECODERS = load("decoders")
RECONCILE = load("wallet_reconcile")
ROUTING = load_phase2("review_routing")

_STEP2 = "phase 7 step 2"
_SETTINGS = Settings(
    database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
    iban_hmac_key=bytes(range(32)),
    session_key=bytes(range(1, 33)),
    rp_id="testserver",
    origin="https://testserver",
    blob_store="local",
    blob_root="/tmp/unused",
)


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


# The balance-only cases (BSC) are the EVM task's, replayed by tests/rules/test_chain_evm.py
# with evm.reconcile_balance_only since they lost their `pending` on main.
RECONCILE_STEP2 = [c for c in RECONCILE.cases if c.pending is None and not c.balance_only]


def test_the_step2_reconcile_case_is_replayed():
    assert "proposed_group_counted" in {c.id for c in RECONCILE_STEP2}


@pytest.mark.parametrize("case", RECONCILE_STEP2, ids=lambda c: c.id)
def test_reconcile(case):
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


# --- D187 helpers (rules/chain/spam), replayed over every spam.yaml case -------------------
# The cases stay pending for the family steps that call these helpers from their classifier;
# the helper itself is family-neutral and must already agree with each.

SPAM = load("spam")


@pytest.mark.parametrize("case", SPAM.cases, ids=lambda c: c.id)
def test_spam_verdict(case):
    key = AssetKey(chain=case.token.chain, contract=case.token.contract)
    got = verdict(
        key,
        case.token.symbol,
        held=case.held,
        fee_paid=case.fee_paid,
        bought=case.bought,
        action=case.action,
    )
    want = case.expect
    assert (got.group, got.movement, got.hidden, got.card, got.label) == (
        want.group,
        want.movement,
        want.hidden,
        want.card,
        want.label,
    )
    assert got.price_source_needed == want.price_source_needed


# --- the family dispatcher (rules/chain/classify) ------------------------------------------


def test_classify_dispatches_by_family_and_restores():
    tx = ChainTx(hash="h1", block=7, occurred_at=datetime(2026, 1, 2, tzinfo=UTC), status="ok")
    seen = []

    def fake(tx, ctx):
        seen.append((tx.hash, ctx.chain))
        fee = ChainLeg(AssetKey(chain=ctx.chain), 5000, "fee", MovementKind.FEE, "fee")
        return ChainGroup(tx.hash, tx.occurred_at, tx.block, GroupKind.TRANSFER, [fee])

    register_family(ChainFamily.SOLANA, fake)
    try:
        group = classify(tx, ClassifyContext(chain=Chain.SOLANA, address="own"))
    finally:
        register_family(ChainFamily.SOLANA, None)
    assert seen == [("h1", Chain.SOLANA)]
    assert group.fee_only and group.legs[0].signed == -5000
    with pytest.raises(NotImplementedError):  # the family module has no classify_tx yet
        classifier_for(ChainFamily.UTXO)


def test_a_leg_is_positive_and_only_the_fee_leg_is_a_fee():
    sol = AssetKey(chain=Chain.SOLANA)
    with pytest.raises(ValueError):
        ChainLeg(sol, 0, "in", MovementKind.INTERNAL_TRANSFER_IN, "0")
    with pytest.raises(ValueError):
        ChainLeg(sol, 1, "fee", MovementKind.INTERNAL_TRANSFER_OUT, "fee")


# --- the source registry (services/chain/base) ---------------------------------------------


def test_provider_errors_map_to_wallet_codes_without_the_url():
    assert error_from(SourceError("HTTPError", 429)).code == WALLET_SOURCE_THROTTLED
    assert error_from(SourceError("HTTPError", 401)).code == WALLET_SOURCE_REFUSED
    assert error_from(SourceError("HTTPError", 403)).code == WALLET_SOURCE_REFUSED
    failed = error_from(SourceError("URLError"))
    assert failed.code == WALLET_SOURCE_FAILED and "http" not in str(failed)


def test_budget_and_host():
    budget = RequestBudget(2)
    assert budget.take() and budget.take() and not budget.take() and budget.exhausted
    assert host_of("https://Mainnet.Example-RPC.com/v1/k?api-key=x") == "mainnet.example-rpc.com"


def test_registered_factory_builds_the_chain_source():
    built = []

    def factory(row, settings, budget):
        built.append((row.code, budget.limit))
        return "a source"

    register_source(ChainSourceKind.SOLANA_RPC, factory)
    try:
        assert source_for(Chain.SOLANA, _SETTINGS) == "a source"
    finally:
        register_source(ChainSourceKind.SOLANA_RPC, None)
    assert built == [(Chain.SOLANA, 2400)]  # the default 4 requests a second × 600
