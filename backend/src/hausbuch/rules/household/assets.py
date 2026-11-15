"""The asset registry, loaded from `assets.yaml` (docs/phases/phase-6.md D167 (3), D174 (2);
the token rows since docs/phases/phase-7.md D180 (4)).

Rule data like the tax rule sets (principle 5): which price source prices an asset is read
from the file, never branched in code. The services that use it are step 5's.
"""

from functools import cache
from importlib.resources import files
from typing import Self

import yaml
from pydantic import Field, model_validator

from hausbuch.schemas.chain import Chain, TokenRegistryRow
from hausbuch.schemas.common import Schema
from hausbuch.schemas.household import AssetSymbol, CurrencyCode, Isin


class CoinEntry(Schema):
    symbol: AssetSymbol
    name: str = Field(min_length=1)
    coingecko_id: str = Field(min_length=1)
    kraken_pair: str | None = Field(default=None, min_length=1)


class FundEntry(Schema):
    isin: Isin
    name: str = Field(min_length=1)
    issuer: str = Field(pattern=r"^[a-z][a-z0-9_]*$")  # services/prices/issuer_nav/<issuer>.py
    fund_ref: str | None = Field(default=None, min_length=1)  # None: no adapter yet
    nav_currency: CurrencyCode


class AssetRegistry(Schema):
    coins: list[CoinEntry]
    funds: list[FundEntry]
    tokens: list[TokenRegistryRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        symbols = [c.symbol for c in self.coins]
        isins = [f.isin for f in self.funds]
        if len(symbols) != len(set(symbols)) or len(isins) != len(set(isins)):
            raise ValueError("assets.yaml: each symbol and each ISIN once")
        return self

    @model_validator(mode="after")
    def _tokens(self) -> Self:
        """D180: a token once per chain and contract, and once per chain and symbol (so the
        symbol-level lookup of D180 (2)'s partial index cannot meet two rows); priced as a
        listed coin (its `wraps`, else its own symbol), with that coin's CoinGecko id."""
        keys = [(t.chain, t.contract) for t in self.tokens]
        symbols = [(t.chain, t.symbol) for t in self.tokens]
        if len(keys) != len(set(keys)) or len(symbols) != len(set(symbols)):
            raise ValueError("assets.yaml: each token once per chain, by contract and by symbol")
        for token in self.tokens:
            coin = self.coin(token.wraps or token.symbol)
            if coin is None:
                raise ValueError(f"assets.yaml: {token.symbol} on {token.chain} prices as no coin")
            if token.coingecko_id is not None and token.coingecko_id != coin.coingecko_id:
                raise ValueError(f"assets.yaml: {token.symbol} on {token.chain} names another id")
        return self

    def coin(self, symbol: str) -> CoinEntry | None:
        return next((c for c in self.coins if c.symbol == symbol), None)

    def fund(self, isin: str) -> FundEntry | None:
        return next((f for f in self.funds if f.isin == isin), None)

    def token(self, chain: Chain, contract: str) -> TokenRegistryRow | None:
        """The registry token at this contract (exact, as the file writes it: EIP-55 for EVM,
        the base58 mint for Solana; step 1 normalises an address before it asks)."""
        return next((t for t in self.tokens if t.chain == chain and t.contract == contract), None)

    def priced_as(self, token: TokenRegistryRow) -> CoinEntry:
        """The coin whose daily price prices the token (D180 (3))."""
        coin = self.coin(token.wraps or token.symbol)
        assert coin is not None  # _tokens
        return coin


@cache
def registry() -> AssetRegistry:
    """The validated registry. Cached: the file is immutable at runtime."""
    text = (files("hausbuch.rules.household") / "assets.yaml").read_text(encoding="utf-8")
    return AssetRegistry.model_validate(yaml.safe_load(text))
