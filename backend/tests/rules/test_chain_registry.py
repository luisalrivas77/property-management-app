"""Phase 7 step 0: the chain registry, the token rows and the public decoders load and refuse
what the spec forbids (docs/phases/phase-7.md D180, D183 (4), D184, D191)."""

import re
from importlib.resources import files

import pytest
from pydantic import ValidationError

from hausbuch.rules.chain import decoders
from hausbuch.rules.chain.registry import ChainRegistry, registry
from hausbuch.rules.household.assets import AssetRegistry
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import Chain, ChainFamily, DecoderKind


def test_the_five_chains_load_with_their_fields():
    chains = registry()
    assert {c.code for c in chains.chains} == set(Chain)
    ids = {c.code: c.chain_id for c in chains.chains}
    assert ids == {
        Chain.BITCOIN: None,
        Chain.ETHEREUM: 1,
        Chain.ARBITRUM: 42161,
        Chain.BSC: 56,
        Chain.SOLANA: None,
    }
    natives = {c.code: (c.native_symbol, c.native_decimals) for c in chains.chains}
    assert natives[Chain.ARBITRUM] == ("ETH", 18)
    assert natives[Chain.BITCOIN] == ("BTC", 8)
    assert natives[Chain.SOLANA] == ("SOL", 9)
    assert chains.chain(Chain.BITCOIN).family is ChainFamily.UTXO
    assert chains.chain(Chain.BITCOIN).confirmations_final == 6
    assert all(c.confirmations_final == 12 for c in chains.chains if c.family is ChainFamily.EVM)


@pytest.mark.parametrize(
    ("name", "chain"),
    [
        ("SOL", Chain.SOLANA),
        ("BSC", Chain.BSC),
        ("ETH", Chain.ETHEREUM),
        ("ARBITRUM", Chain.ARBITRUM),
        ("BTC", Chain.BITCOIN),
        ("TRX", None),
        ("eth", None),
    ],
)
def test_binance_network_names(name, chain):
    assert registry().network(name) is chain


def test_the_eth_bridge_reads_both_ways():
    there = registry().bridge(Chain.ETHEREUM, Chain.ARBITRUM)
    back = registry().bridge(Chain.ARBITRUM, Chain.ETHEREUM)
    assert there == back and len(there) == 1
    assert registry().bridge(Chain.ETHEREUM, Chain.BSC) == []


def test_every_wrap_is_a_registry_token_of_its_native_coin():
    assets = asset_registry()
    for row in registry().chains:
        for wrap in row.wraps:
            token = assets.token(row.code, wrap.wrapped)
            assert token is not None and token.wraps == row.native_symbol


def test_tokens_price_as_listed_coins():
    assets = asset_registry()
    assert len(assets.tokens) == 12
    for token in assets.tokens:
        coin = assets.priced_as(token)
        assert coin.symbol == (token.wraps or token.symbol)


def test_every_evm_address_in_the_registry_files_is_lower_case():
    """D180 amended 15 Nov 2026: one form per EVM address, the database's CHECK's. Read from
    the files' text, since the loader lower-cases what it reads."""
    texts = [
        (files(package) / name).read_text(encoding="utf-8")
        for package, name in (
            ("hausbuch.rules.household", "assets.yaml"),
            ("hausbuch.rules.chain", "chains.yaml"),
            ("hausbuch.rules.chain", "decoders.yaml"),
        )
    ]
    evm = [a for text in texts for a in re.findall(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])", text)]
    assert len(evm) >= 15 and all(a == a.lower() for a in evm)


def test_an_evm_token_is_found_whatever_its_case_a_solana_mint_only_as_written():
    assets = asset_registry()
    usdc = assets.token(Chain.ETHEREUM, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")  # EIP-55
    assert usdc is not None and usdc.symbol == "USDC"
    assert assets.token(Chain.ETHEREUM, usdc.contract.upper().replace("0X", "0x")) == usdc
    mint = next(t for t in assets.tokens if t.chain is Chain.SOLANA)
    assert assets.token(Chain.SOLANA, mint.contract) == mint
    assert assets.token(Chain.SOLANA, mint.contract.lower()) is None
    assert assets.token(Chain.ARBITRUM, usdc.contract) is None  # another chain, another token


def test_a_registry_row_written_in_eip55_is_loaded_lower_case():
    eip55 = {**USDC, "contract": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"}
    loaded = AssetRegistry.model_validate(_assets([eip55]))
    assert loaded.tokens[0].contract == USDC["contract"]


@pytest.mark.parametrize("symbol", ["wstETH", "USD₮0", "x" * 32])
def test_a_token_symbol_is_a_label_of_up_to_32_characters(symbol):
    """D180 (2) amended 15 Nov 2026 (TokenSymbol); a coin's symbol keeps AssetSymbol."""
    AssetRegistry.model_validate(_assets([{**USDC, "symbol": symbol, "wraps": "USDC"}]))


@pytest.mark.parametrize("symbol", ["", "x" * 33, "US\nDC", "US\x07DC"])
def test_a_token_symbol_outside_its_label_is_refused(symbol):
    with pytest.raises(ValidationError):
        AssetRegistry.model_validate(_assets([{**USDC, "symbol": symbol, "wraps": "USDC"}]))


def _assets(extra_tokens: list[dict]) -> dict:
    return {
        "coins": [
            {"symbol": "ETH", "name": "Ether", "coingecko_id": "ethereum"},
            {"symbol": "USDC", "name": "USD Coin", "coingecko_id": "usd-coin"},
        ],
        "funds": [],
        "tokens": extra_tokens,
    }


USDC = {
    "chain": "ethereum",
    "contract": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "symbol": "USDC",
    "decimals": 6,
    "coingecko_id": "usd-coin",
}


def test_a_token_twice_on_one_chain_is_refused():
    with pytest.raises(ValidationError, match="each token once"):
        AssetRegistry.model_validate(_assets([USDC, USDC]))
    lookalike = {**USDC, "contract": "0x9f00000000000000000000000000000000000021"}
    with pytest.raises(ValidationError, match="each token once"):
        AssetRegistry.model_validate(_assets([USDC, lookalike]))


def test_a_token_pricing_as_no_coin_or_another_id_is_refused():
    with pytest.raises(ValidationError, match="prices as no coin"):
        AssetRegistry.model_validate(
            _assets([{**USDC, "symbol": "DAI", "coingecko_id": None, "wraps": "DAI"}])
        )
    with pytest.raises(ValidationError, match="names another id"):
        AssetRegistry.model_validate(_assets([{**USDC, "coingecko_id": "tether"}]))


def test_a_token_without_a_price_route_is_refused():
    with pytest.raises(ValidationError, match="coingecko_id or wraps"):
        AssetRegistry.model_validate(_assets([{**USDC, "coingecko_id": None}]))


def test_a_registry_missing_a_chain_is_refused():
    rows = [c.model_dump(mode="json") for c in registry().chains if c.code is not Chain.BSC]
    with pytest.raises(ValidationError, match="every chain"):
        ChainRegistry.model_validate({"chains": rows})


def test_a_network_name_on_two_chains_is_refused():
    rows = [c.model_dump(mode="json") for c in registry().chains]
    for row in rows:
        if row["code"] == "arbitrum":
            row["exchange_networks"] = {"ARBITRUM": "arbitrum", "ETH": "arbitrum"}
    with pytest.raises(ValidationError, match="one chain"):
        ChainRegistry.model_validate({"chains": rows})


def test_the_public_decoders_are_the_wraps_and_the_stake_program():
    rows = decoders.public_decoders()
    assert {r.kind for r in rows} == {DecoderKind.WRAP, DecoderKind.STAKE}
    wraps = {(r.chain, r.program) for r in rows if r.kind is DecoderKind.WRAP}
    for row in registry().chains:
        if row.code is not Chain.SOLANA:
            assert {(row.code, w.wrapped) for w in row.wraps} <= wraps
    (stake,) = [r for r in rows if r.kind is DecoderKind.STAKE]
    assert stake.chain is Chain.SOLANA
    assert stake.program == "Stake11111111111111111111111111111111111111"


def test_a_decoder_row_with_an_amount_is_refused():
    text = (
        "decoders:\n"
        "  - {chain: solana, program: Stake11111111111111111111111111111111111111,"
        " kind: stake, amount: '10'}\n"
    )
    with pytest.raises(ValidationError, match="amount"):
        decoders.parse(text)


def test_a_bitcoin_decoder_is_refused():
    text = (
        "decoders:\n"
        "  - {chain: bitcoin, program: bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq, kind: swap}\n"
    )
    with pytest.raises(ValidationError, match="Bitcoin"):
        decoders.parse(text)


def test_a_wrap_row_that_is_no_registry_wrap_is_refused():
    text = (
        "decoders:\n"
        "  - {chain: ethereum, program: '0x9f00000000000000000000000000000000000021',"
        " kind: wrap, legs: wrap}\n"
    )
    with pytest.raises(ValidationError, match="registry wrap"):
        decoders.parse(text)
