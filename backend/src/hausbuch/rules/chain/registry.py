"""The chain registry, loaded from `chains.yaml` (docs/phases/phase-7.md D180 (1), D184).

Rule data (principle 5): which chains exist, their native coin, their source kind, Binance's
network names and the canonical bridges and wraps are read from the file, never branched in
code. Checked against the token registry of `rules/household/assets.yaml` on load.
"""

from functools import cache
from importlib.resources import files
from typing import Self

import yaml
from pydantic import model_validator

from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import BridgePair, Chain, ChainRegistryRow
from hausbuch.schemas.common import Schema


class ChainRegistry(Schema):
    chains: list[ChainRegistryRow]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        codes = [c.code for c in self.chains]
        if sorted(codes) != sorted(Chain):
            raise ValueError("chains.yaml: every chain of the Chain enum once")
        names = [n for c in self.chains for n in c.exchange_networks]
        if len(names) != len(set(names)):
            raise ValueError("chains.yaml: an exchange network name names one chain")
        assets = asset_registry()
        for row in self.chains:
            if assets.coin(row.native_symbol) is None:
                raise ValueError(f"chains.yaml: {row.code}'s native coin is no listed coin")
            if any(chain is not row.code for chain in row.exchange_networks.values()):
                raise ValueError(f"chains.yaml: {row.code} lists another chain's network")
            for pair in row.bridge_pairs:
                if pair.from_chain is not row.code:
                    raise ValueError(f"chains.yaml: a bridge of {row.code} starts on it")
            for wrap in row.wraps:
                token = assets.token(row.code, wrap.wrapped)
                if token is None or token.wraps != wrap.native or wrap.native != row.native_symbol:
                    raise ValueError(f"chains.yaml: {row.code}'s wrap is no registry token")
        return self

    def chain(self, code: Chain) -> ChainRegistryRow:
        return next(c for c in self.chains if c.code is code)

    def network(self, name: str) -> Chain | None:
        """D184 (1)(a): Binance's network name to the chain, or None (no match, the card names
        the network)."""
        return next((c.code for c in self.chains if name in c.exchange_networks), None)

    def bridge(self, a: Chain, b: Chain) -> list[BridgePair]:
        """The canonical bridge pairs between two chains, in either direction."""
        return [
            p for c in self.chains for p in c.bridge_pairs if {p.from_chain, p.to_chain} == {a, b}
        ]


@cache
def registry() -> ChainRegistry:
    """The validated registry. Cached: the file is immutable at runtime."""
    text = (files("hausbuch.rules.chain") / "chains.yaml").read_text(encoding="utf-8")
    return ChainRegistry.model_validate(yaml.safe_load(text))
