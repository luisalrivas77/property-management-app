"""A provider's records to the chain-neutral shapes (docs/phases/phase-7.md D181, D185, §5).

- `solana_deltas`: a `getTransaction` (jsonParsed, version 0) to one SolanaDelta per asset of
  the own address, from the pre/post balances (D181 (d)); rent moves are flagged, never
  movements. Step 2.
- `utxo_net`: a Bitcoin transaction to the net amount out of the own addresses, its fee and
  the outputs to unregistered addresses (D185 (1), (2)). Step 4.

Fixtures tests/fixtures/phase7/solana_tx.yaml and btc_utxo.yaml.
"""

from collections.abc import Collection, Mapping

from pydantic import JsonValue

from hausbuch.schemas.chain import ChainTx, SolanaDelta


def solana_deltas(tx: Mapping[str, JsonValue], own: Collection[str]) -> list[SolanaDelta]:
    raise NotImplementedError("phase 7 step 2")


def utxo_net(tx: ChainTx, own: Collection[str]) -> tuple[int, int, list[str]]:
    raise NotImplementedError("phase 7 step 4")
