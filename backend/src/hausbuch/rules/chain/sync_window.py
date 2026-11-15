"""The range a chain sync reads (docs/phases/phase-7.md D182 (1), (4)); built in step 3 (EVM)
or step 4 (Bitcoin), whichever lands first: Solana (step 2) has no block window.

`window(chain, cursor_block, tip)`: a first sync (no cursor) reads from block 0 to
`tip − confirmations_final`; a later one from `cursor − confirmations_final` (the overlap,
deduplicated by movement hash) to the same bound; None when the bound lies before the start
(a tip that moved back). Solana reads at the `finalized` commitment by signature, so its
window is the cursor signature alone. Fixture tests/fixtures/phase7/sync_window_chain.yaml.
"""

from hausbuch.schemas.chain import Chain


def window(chain: Chain, cursor_block: int | None, tip: int) -> tuple[int, int] | None:
    raise NotImplementedError("phase 7 step 3 or 4")
