"""rules/chain/match_transfer against chain_transfers.yaml (docs/phases/phase-7.md D184, §7),
plus the cases the fixture cannot hold: a wallet's out-leg with a hash against an exchange
deposit without one, a third party's credit and a superseded leg on the amount path, the
bridge both ways and without a pair, the dust tolerance, the wrap's one transaction, the
workspace's own pairs (D191) and the hash's priority."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fixture_models_phase7 import OpenLeg, TransferCase, load

from hausbuch.rules import errors as E
from hausbuch.rules.chain.match_transfer import ChainLeg, match
from hausbuch.rules.chain.registry import registry
from hausbuch.schemas.chain import BridgePair, Chain, WrapPair
from hausbuch.schemas.household import TransferLinkKind

CASES = load("chain_transfers").cases
STEP5 = [c for c in CASES if c.pending is None]


def _leg(row: OpenLeg, *, contract: str | None = None) -> ChainLeg:
    return ChainLeg(
        ref=row.name,
        account=row.account,
        chain=row.chain,
        asset=row.asset,
        contract=contract,
        units=row.units,
        fee=row.fee,
        at=row.at,
        tx_hash=row.tx_hash,
        network=row.network,
        own=row.own,
        superseded=row.superseded,
    )


def _candidate(case: TransferCase, row: OpenLeg) -> ChainLeg:
    # The fixture writes the priced_as symbol only; its `kind: wrap` says the receiving leg is
    # the chain's wrapped token, whose contract the registry gives.
    contract = None
    if case.kind is TransferLinkKind.WRAP and row.chain is not None:
        (wrap,) = registry().chain(row.chain).wraps
        contract = wrap.wrapped
    return _leg(row, contract=contract)


def test_only_the_rebooking_case_waits_for_step_6():
    assert len(STEP5) == 13
    assert [c.id for c in CASES if c.pending] == ["link_ended_by_rebooking"]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_fixture_case(case: TransferCase):
    got = match(_leg(case.leg), [_candidate(case, c) for c in case.candidates])
    want = case.expect
    assert got.result == want.result
    assert got.card == want.card
    if want.result == "linked":
        assert got.link is want.link
        assert got.counterpart is not None and got.counterpart.ref == want.with_leg
        assert got.lots_move is want.lots_move
    else:
        assert got.counterpart is None and got.lots_move is None
    if want.result == "ambiguous":
        assert {c.ref for c in got.candidates} == {c.name for c in case.candidates}


T0 = datetime(2026, 3, 9, 18, 0, tzinfo=UTC)
SOL_HASH = (
    "5EfKsYTdUGotNe7CybcAaG4NscuXP89dLAhJtZbDQ8nJcFpV37kyAuENBawzA7zDHkcCdYQrikMBCLpsX3rD86rf"
)
EVM_HASH = "0x" + "ab" * 32


def _sol(ref: str, account: str, units: str, at: datetime, **kw) -> ChainLeg:
    return ChainLeg(
        ref=ref, account=account, chain=Chain.SOLANA, asset="SOL", units=Decimal(units), at=at, **kw
    )


def _exchange(ref: str, units: str, at: datetime, **kw) -> ChainLeg:
    return ChainLeg(ref=ref, account="binance", asset="SOL", units=Decimal(units), at=at, **kw)


def test_wallet_out_with_hash_meets_an_exchange_deposit_without_one():
    # D184 (1)(b): a real wallet leg always has a hash, the exchange's Deposit row has none.
    leg = _sol("out", "wallet_a", "2", T0, tx_hash=SOL_HASH)
    got = match(leg, [_exchange("dep", "2", T0 + timedelta(minutes=3))])
    assert got.result == "linked" and got.link is TransferLinkKind.CHAIN
    assert got.counterpart.ref == "dep" and got.lots_move is True


def test_a_hash_wins_over_an_amount_fit():
    leg = _exchange("out", "2", T0, fee=Decimal("0.01"), tx_hash=SOL_HASH, network="SOL")
    by_hash = _sol("in", "wallet_a", "1.99", T0 + timedelta(minutes=1), tx_hash=SOL_HASH)
    by_amount = ChainLeg(ref="bison", account="bison", asset="SOL", units=Decimal("1.99"), at=T0)
    got = match(leg, [by_amount, by_hash])
    assert got.result == "linked" and got.counterpart.ref == "in"


def test_another_hash_is_never_the_counterpart_even_at_the_right_amount():
    leg = _sol("out", "wallet_a", "1", T0, tx_hash=SOL_HASH)
    other = _sol("in", "tangem", "1", T0, tx_hash=SOL_HASH[::-1])
    assert match(leg, [other]).result == "unmatched"


def test_evm_hashes_compare_without_case():
    leg = ChainLeg(
        ref="out",
        account="binance",
        asset="ETH",
        units=Decimal("0.3"),
        at=T0,
        fee=Decimal("0.0005"),
        tx_hash=EVM_HASH.upper().replace("0X", "0x"),
        network="ETH",
    )
    got_in = ChainLeg(
        ref="in",
        account="wallet_a",
        chain=Chain.ETHEREUM,
        asset="ETH",
        units=Decimal("0.2995"),
        at=T0,
        tx_hash=EVM_HASH,
    )
    assert match(leg, [got_in]).result == "linked"


@pytest.mark.parametrize(("own", "result"), [(False, "unmatched"), (True, "linked")])
def test_a_third_partys_credit_without_hash_is_never_the_counterpart(own, result):
    leg = ChainLeg(
        ref="out",
        account="bison",
        asset="BTC",
        units=Decimal("0.01"),
        at=T0,
        fee=Decimal("0.0002"),
    )
    credit = ChainLeg(
        ref="in",
        account="tangem",
        chain=Chain.BITCOIN,
        asset="BTC",
        units=Decimal("0.0098"),
        at=T0 + timedelta(hours=1),
        own=own,
    )
    got = match(leg, [credit])
    assert got.result == result
    if result == "unmatched":
        assert got.card == E.TRANSFER_UNMATCHED_LEG


def test_a_superseded_leg_on_the_amount_path_is_never_the_counterpart():
    leg = _exchange("out", "2", T0, fee=Decimal("0.01"), network="SOL")
    old = _sol("in", "wallet_a", "1.99", T0, superseded=True)
    assert match(leg, [old]).result == "unmatched"


def test_a_credit_before_the_withdrawal_is_outside_the_window():
    leg = _exchange("out", "2", T0, fee=Decimal("0.01"), network="SOL")
    early = _sol("in", "wallet_a", "1.99", T0 - timedelta(seconds=1))
    assert match(leg, [early]).result == "unmatched"


def test_the_leg_itself_is_no_candidate():
    leg = _sol("out", "wallet_a", "1", T0, tx_hash=SOL_HASH)
    assert match(leg, [leg]).result == "unmatched"


def test_an_unknown_network_is_named_for_the_card():
    leg = _exchange("out", "2", T0, fee=Decimal("0.01"), network="SOLANA_NEW")
    got = match(leg, [_sol("in", "wallet_a", "1.99", T0)])
    assert got.result == "unmatched" and got.unknown_network == "SOLANA_NEW"
    assert match(_exchange("x", "2", T0, network="SOL"), []).unknown_network is None


def test_the_network_names_the_chain_of_the_counterpart():
    leg = ChainLeg(
        ref="out", account="binance", asset="ETH", units=Decimal("1"), at=T0, network="ARBITRUM"
    )
    on_eth = ChainLeg(
        ref="eth", account="wallet_a", chain=Chain.ETHEREUM, asset="ETH", units=Decimal("1"), at=T0
    )
    on_arb = ChainLeg(
        ref="arb", account="wallet_a", chain=Chain.ARBITRUM, asset="ETH", units=Decimal("1"), at=T0
    )
    got = match(leg, [on_eth, on_arb])
    assert got.result == "linked" and got.counterpart.ref == "arb"


@pytest.mark.parametrize(
    ("units", "decimals", "result"),
    [
        ("1.989999999", 9, "linked"),  # one lamport short: the chain's dust
        ("1.990000001", 9, "linked"),
        ("1.989999998", 9, "unmatched"),
        ("1.989999999", None, "unmatched"),  # no decimals given: exact
    ],
)
def test_the_dust_of_one_smallest_unit(units, decimals, result):
    leg = _exchange("out", "2", T0, fee=Decimal("0.01"), network="SOL")
    got_in = _sol("in", "wallet_a", units, T0, decimals=decimals)
    assert match(leg, [got_in]).result == result


def _eth(ref: str, chain: Chain, units: str, at: datetime, **kw) -> ChainLeg:
    return ChainLeg(
        ref=ref, account="wallet_a", chain=chain, asset="ETH", units=Decimal(units), at=at, **kw
    )


def test_a_bridge_reads_both_ways_and_names_its_fee():
    there = match(
        _eth("out", Chain.ETHEREUM, "0.5", T0), [_eth("in", Chain.ARBITRUM, "0.4985", T0)]
    )
    back = match(_eth("out", Chain.ARBITRUM, "0.5", T0), [_eth("in", Chain.ETHEREUM, "0.4985", T0)])
    for got in (there, back):
        assert got.result == "linked" and got.link is TransferLinkKind.BRIDGE
        assert got.fee_units == Decimal("0.0015") and got.lots_move is False


@pytest.mark.parametrize(
    ("units", "delay", "result"),
    [
        ("0.495", timedelta(hours=24), "linked"),  # exactly 1 %, at the window's end
        ("0.4949", timedelta(minutes=20), "unmatched"),  # above the share
        ("0.5001", timedelta(minutes=20), "unmatched"),  # more than was sent
        ("0.4985", timedelta(hours=24, seconds=1), "unmatched"),  # outside 24 hours
    ],
)
def test_the_bridge_window_and_share(units, delay, result):
    got = match(
        _eth("out", Chain.ETHEREUM, "0.5", T0), [_eth("in", Chain.ARBITRUM, units, T0 + delay)]
    )
    assert got.result == result


def test_two_chains_without_a_pair_are_no_bridge():
    leg = _eth("out", Chain.ETHEREUM, "0.5", T0)
    on_bsc = _eth("in", Chain.BSC, "0.4985", T0, contract="0x" + "11" * 20)
    assert match(leg, [on_bsc]).result == "unmatched"


def test_a_workspace_bridge_pair_links_a_token_and_wins_over_the_file():
    usdc = "0x" + "a0" * 20
    usdc_e = "0x" + "ff" * 20
    leg = ChainLeg(
        ref="out",
        account="wallet_a",
        chain=Chain.ETHEREUM,
        asset="USDC",
        contract=usdc,
        units=Decimal("100"),
        at=T0,
    )
    got_in = ChainLeg(
        ref="in",
        account="wallet_a",
        chain=Chain.ARBITRUM,
        asset="USDC",
        contract=usdc_e,
        units=Decimal("98"),
        at=T0 + timedelta(minutes=15),
    )
    assert match(leg, [got_in]).result == "unmatched"
    pair = BridgePair(
        from_chain=Chain.ARBITRUM,
        from_contract=usdc_e.upper().replace("0X", "0x"),
        to_chain=Chain.ETHEREUM,
        to_contract=usdc,
        max_fee_share=Decimal("0.0200"),
    )
    got = match(leg, [got_in], extra_bridges=[pair])
    assert got.result == "linked" and got.link is TransferLinkKind.BRIDGE

    # The native ETH pair at 0.5 % instead of the file's 1 %: 0.3 % still fits, 0.8 % not.
    strict = BridgePair(
        from_chain=Chain.ETHEREUM, to_chain=Chain.ARBITRUM, max_fee_share=Decimal("0.0050")
    )
    out = _eth("out", Chain.ETHEREUM, "0.5", T0)
    assert (
        match(out, [_eth("in", Chain.ARBITRUM, "0.4985", T0)], extra_bridges=[strict]).result
        == "linked"
    )
    assert (
        match(out, [_eth("in", Chain.ARBITRUM, "0.496", T0)], extra_bridges=[strict]).result
        == "unmatched"
    )
    assert match(out, [_eth("in", Chain.ARBITRUM, "0.496", T0)]).result == "linked"


WETH = registry().chain(Chain.ETHEREUM).wraps[0].wrapped


def test_an_unwrap_is_a_wrap_too():
    leg = _eth("weth_out", Chain.ETHEREUM, "1", T0, contract=WETH.upper().replace("0X", "0x"))
    got = match(leg, [_eth("eth_in", Chain.ETHEREUM, "1", T0 + timedelta(seconds=30))])
    assert got.result == "linked" and got.link is TransferLinkKind.WRAP
    assert got.lots_move is False


def test_a_wrap_within_one_transaction_or_one_minute():
    leg = _eth("out", Chain.ETHEREUM, "1", T0, tx_hash=EVM_HASH)
    late_same_tx = _eth(
        "in", Chain.ETHEREUM, "1", T0 + timedelta(minutes=2), contract=WETH, tx_hash=EVM_HASH
    )
    late_other = _eth("in", Chain.ETHEREUM, "1", T0 + timedelta(minutes=2), contract=WETH)
    assert match(leg, [late_same_tx]).link is TransferLinkKind.WRAP
    assert match(leg, [late_other]).result == "unmatched"


def test_a_token_that_is_no_registry_wrap_is_no_counterpart():
    leg = _eth("out", Chain.ETHEREUM, "1", T0)
    other = _eth("in", Chain.ETHEREUM, "1", T0, contract="0x" + "22" * 20)
    assert match(leg, [other]).result == "unmatched"


def test_a_workspace_wrap_pair():
    mint = "So1aWrapped" + "1" * 30
    leg = _sol("out", "wallet_a", "1", T0)
    wrapped = _sol("in", "wallet_a", "1", T0, contract=mint)
    assert match(leg, [wrapped]).result == "unmatched"
    pair = (Chain.SOLANA, WrapPair(native="SOL", wrapped=mint))
    assert match(leg, [wrapped], extra_wraps=[pair]).link is TransferLinkKind.WRAP


def test_ambiguous_over_two_kinds_names_no_kind():
    leg = _eth("out", Chain.ETHEREUM, "1", T0)
    to_tangem = ChainLeg(
        ref="tangem",
        account="tangem",
        chain=Chain.ETHEREUM,
        asset="ETH",
        units=Decimal("1"),
        at=T0 + timedelta(minutes=5),
    )
    bridged = _eth("arb", Chain.ARBITRUM, "0.999", T0 + timedelta(minutes=5))
    got = match(leg, [to_tangem, bridged])
    assert got.result == "ambiguous" and got.card == E.TRANSFER_AMBIGUOUS
    assert got.link is None and {c.ref for c in got.candidates} == {"tangem", "arb"}


def test_a_wrap_in_one_transaction_still_needs_its_units():
    leg = _eth("out", Chain.ETHEREUM, "1", T0, tx_hash=EVM_HASH)
    half = _eth("in", Chain.ETHEREUM, "0.5", T0, contract=WETH, tx_hash=EVM_HASH)
    assert match(leg, [half]).result == "unmatched"
