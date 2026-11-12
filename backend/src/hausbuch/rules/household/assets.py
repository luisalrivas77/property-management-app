"""The asset registry, loaded from `assets.yaml` (docs/phases/phase-6.md D167 (3), D174 (2)).

Rule data like the tax rule sets (principle 5): which price source prices an asset is read
from the file, never branched in code. The services that use it are step 5's.
"""

from functools import cache
from importlib.resources import files
from typing import Self

import yaml
from pydantic import Field, model_validator

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

    @model_validator(mode="after")
    def _unique(self) -> Self:
        symbols = [c.symbol for c in self.coins]
        isins = [f.isin for f in self.funds]
        if len(symbols) != len(set(symbols)) or len(isins) != len(set(isins)):
            raise ValueError("assets.yaml: each symbol and each ISIN once")
        return self

    def coin(self, symbol: str) -> CoinEntry | None:
        return next((c for c in self.coins if c.symbol == symbol), None)

    def fund(self, isin: str) -> FundEntry | None:
        return next((f for f in self.funds if f.isin == isin), None)


@cache
def registry() -> AssetRegistry:
    """The validated registry. Cached: the file is immutable at runtime."""
    text = (files("hausbuch.rules.household") / "assets.yaml").read_text(encoding="utf-8")
    return AssetRegistry.model_validate(yaml.safe_load(text))
