"""Protocol decoders as data, loaded from `decoders.yaml` (docs/phases/phase-7.md D183 (4), D191).

A row names a program (and a method) and gives the group's kind and how its legs read; never
an amount, which DecoderRow's extra="forbid" refuses on load. The workspace's own rows
(`chain_decoder`) override a public row in `decoder_for`, built in step 2.
"""

from collections.abc import Sequence
from functools import cache
from importlib.resources import files
from typing import Literal, Self

import yaml
from pydantic import model_validator

from hausbuch.rules.chain.registry import registry
from hausbuch.schemas.chain import CHAIN_FAMILY, Chain, ChainFamily, DecoderKind, DecoderRow
from hausbuch.schemas.common import Schema


class DecoderFile(Schema):
    decoders: list[DecoderRow]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        keys = [(d.chain, d.program, d.method) for d in self.decoders]
        if len(keys) != len(set(keys)):
            raise ValueError("decoders.yaml: one row per chain, program and method")
        chains = registry()
        for row in self.decoders:
            if row.chain is Chain.BITCOIN:
                raise ValueError("decoders.yaml: Bitcoin has no programs to decode (D185)")
            if row.kind is DecoderKind.WRAP and row.chain is not Chain.SOLANA:
                wrapped = {w.wrapped for w in chains.chain(row.chain).wraps}
                if row.program not in wrapped:
                    raise ValueError(f"decoders.yaml: {row.name}: a wrap row is a registry wrap")
        return self


def parse(text: str) -> DecoderFile:
    """The loader's own parse, so a test can feed it a file (a row with an amount refused)."""
    return DecoderFile.model_validate(yaml.safe_load(text))


@cache
def public_decoders() -> tuple[DecoderRow, ...]:
    """The validated public rows. Cached: the file is immutable at runtime."""
    text = (files("hausbuch.rules.chain") / "decoders.yaml").read_text(encoding="utf-8")
    return tuple(parse(text).decoders)


DecoderSource = Literal["workspace", "public"]


def decoder_lookup(
    chain: Chain,
    program: str,
    method: str | None,
    workspace_rows: Sequence[DecoderRow] = (),
    public_rows: Sequence[DecoderRow] | None = None,
) -> tuple[DecoderRow, DecoderSource] | None:
    """`decoder_for` with where the row came from. `public_rows` defaults to decoders.yaml; a
    test passes its own."""
    public = public_decoders() if public_rows is None else public_rows
    if CHAIN_FAMILY[chain] is ChainFamily.EVM:
        program = program.lower()  # D180 amended (b): EVM addresses are stored lower-case
    methods = [None] if method is None else [method, None]
    for wanted in methods:
        for source, rows in (("workspace", workspace_rows), ("public", public)):
            for row in rows:
                if row.chain is chain and row.program == program and row.method == wanted:
                    return row, source
    return None


def decoder_for(
    chain: Chain,
    program: str,
    method: str | None,
    workspace_rows: Sequence[DecoderRow] = (),
    public_rows: Sequence[DecoderRow] | None = None,
) -> DecoderRow | None:
    """D191: the row for one program call, a workspace row winning over a public one with the
    same chain, program and method, a method-specific row over a method-less one (a
    method-less row covers any method of its program). Fixture
    tests/fixtures/phase7/decoders.yaml."""
    found = decoder_lookup(chain, program, method, workspace_rows, public_rows)
    return None if found is None else found[0]
