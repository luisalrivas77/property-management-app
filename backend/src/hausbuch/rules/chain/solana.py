"""Solana: a `getTransaction` record to its deltas and its movement group (docs/phases/phase-7.md
D181 (d), D183, D186, D187, §6); built in step 2.

`solana_deltas` is the body of `rules/chain/normalise.solana_deltas`; `classify_solana` turns
one transaction's deltas, program calls and decoders into a `MovementGroupDraft`. Fixtures
tests/fixtures/phase7/solana_tx.yaml, swap_group.yaml, spam.yaml (Solana cases).
"""

from collections.abc import Collection, Mapping

from pydantic import JsonValue

from hausbuch.schemas.chain import SolanaDelta


def solana_deltas(tx: Mapping[str, JsonValue], own: Collection[str]) -> list[SolanaDelta]:
    raise NotImplementedError("phase 7 step 2")
