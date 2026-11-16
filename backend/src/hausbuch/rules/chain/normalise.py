"""A provider's records to the chain-neutral shapes (docs/phases/phase-7.md D181, D185, §5).

- `solana_deltas`: a `getTransaction` (jsonParsed, version 0) to one SolanaDelta per asset of
  the own address, from the pre/post balances (D181 (d)); rent moves are flagged, never
  movements. Step 2.
- `utxo_net`: a Bitcoin transaction to the net amount out of the own addresses, its fee and
  the outputs to unregistered addresses (D185 (1), (2)). Step 4.

Each delegates to its family module (rules/chain/solana.py, utxo.py), so steps 2 and 4 own
separate files. Fixtures tests/fixtures/phase7/solana_tx.yaml and btc_utxo.yaml.
"""

from collections.abc import Collection, Mapping

from pydantic import JsonValue

from hausbuch.rules.chain import solana, utxo
from hausbuch.schemas.chain import ChainTx, SolanaDelta


def solana_deltas(
    tx: Mapping[str, JsonValue],
    own: Collection[str],
    own_token_accounts: Collection[str] | None = None,
) -> list[SolanaDelta]:
    """`own_token_accounts` resolves a token balance row without an `owner` (solana.py)."""
    return solana.solana_deltas(tx, own, own_token_accounts)


def utxo_net(tx: ChainTx, own: Collection[str]) -> tuple[int, int, list[str]]:
    return utxo.utxo_net(tx, own)
