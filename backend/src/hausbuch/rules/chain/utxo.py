"""Bitcoin: the UTXO accounting of D185 (the net amount out of the own addresses, its fee,
the outputs to unregistered addresses and the change-address question) and the movement group
(docs/phases/phase-7.md D185, §6 (12)); built in step 4. `utxo_net` is the body of
`rules/chain/normalise.utxo_net`. Fixture tests/fixtures/phase7/btc_utxo.yaml.
"""

from collections.abc import Collection

from hausbuch.schemas.chain import ChainTx


def utxo_net(tx: ChainTx, own: Collection[str]) -> tuple[int, int, list[str]]:
    raise NotImplementedError("phase 7 step 4")
