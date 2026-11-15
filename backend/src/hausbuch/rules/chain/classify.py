"""One transaction to its movement group (docs/phases/phase-7.md D183, D185, D187, §6).

`classify` gives the group's kind by deterministic shape rules and the decoders of
rules/chain/decoders (never a model): one movement per asset leg and one for the fee, spam by
D187 (an unlisted token credited without a fee paid), a swap's legs priced by D188 (4)
downstream. Built per chain family in steps 2 (Solana), 3 (EVM) and 4 (Bitcoin). Fixtures
tests/fixtures/phase7/swap_group.yaml, spam.yaml, solana_tx.yaml, evm_tx.yaml, btc_utxo.yaml.
"""

from collections.abc import Collection, Sequence
from uuid import UUID

from hausbuch.schemas.chain import ChainTx, DecoderRow, MovementGroupDraft


def classify(
    account_id: UUID,
    tx: ChainTx,
    own: Collection[str],
    decoders: Sequence[DecoderRow],
) -> MovementGroupDraft:
    raise NotImplementedError("phase 7 steps 2 to 4")
