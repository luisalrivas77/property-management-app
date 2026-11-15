"""The range a chain sync reads (docs/phases/phase-7.md D182 (1), (4)); built in step 3 (EVM),
used by step 4 (Bitcoin) unchanged: Solana (step 2) has no block window.

`window(chain, cursor_block, tip)`: a first sync (no cursor) reads from block 0 to
`tip − confirmations_final`; a later one from `cursor − confirmations_final` (the overlap,
deduplicated by movement hash) to the same bound; None when the bound lies before the start
(a tip that moved back). Solana reads at the `finalized` commitment by signature, so its
window is the cursor signature alone and `window` refuses it. `new_hashes` is the overlap's
deduplication: the movement hashes read that were not stored before, in reading order.
Fixture tests/fixtures/phase7/sync_window_chain.yaml.
"""

from collections.abc import Collection, Iterable

from hausbuch.rules.chain.registry import registry
from hausbuch.schemas.chain import Chain, ChainFamily


def window(chain: Chain, cursor_block: int | None, tip: int) -> tuple[int, int] | None:
    row = registry().chain(chain)
    if row.family is ChainFamily.SOLANA:
        raise ValueError("Solana reads by signature at `finalized`: no block window")
    final = row.confirmations_final
    start = 0 if cursor_block is None else max(cursor_block - final, 0)
    end = tip - final
    return (start, end) if end >= start else None


def new_hashes(read: Iterable[str], seen: Collection[str]) -> list[str]:
    """D182 (2), (4): the hashes of `read` not in `seen`, each once, in reading order."""
    out: list[str] = []
    done = set(seen)
    for h in read:
        if h not in done:
            done.add(h)
            out.append(h)
    return out
