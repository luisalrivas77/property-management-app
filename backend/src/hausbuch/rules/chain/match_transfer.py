"""Transfers between own accounts on a chain (docs/phases/phase-7.md D184, §7); built in step 5.

`match` links an open leg to its counterpart: `chain` by the transaction hash, else by asset,
`quantity − fee_quantity`, the exchange's network name (rules/chain/registry.network) and 48
hours; `bridge` by the registry's pairs within 24 hours and the pair's fee share; `wrap` by
the registry's wraps within one transaction or one minute. One candidate → linked; several
→ `transfer.ambiguous`; none → `transfer.unmatched_leg`. Fixture
tests/fixtures/phase7/chain_transfers.yaml.
"""

from collections.abc import Sequence

from hausbuch.schemas.household import AssetMovement, TransferLinkKind


def match(
    leg: AssetMovement, candidates: Sequence[AssetMovement]
) -> tuple[TransferLinkKind, AssetMovement] | str:
    raise NotImplementedError("phase 7 step 5")
