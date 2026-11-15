"""Phase 7: the chains' pure rules (docs/phases/phase-7.md).

- `chains.yaml` and `registry`: the chain registry (D180 (1)), its Binance network names
  (D184 (1)(a)), canonical bridges and wraps; the tokens live in
  `rules/household/assets.yaml` (D180 (4)).
- `decoders.yaml` and `decoders`: protocol decoders as data (D183 (4), D191).
- `address`: validation and the mask (D179, §4), step 1.
- `sync_window`: the block range a sync reads (D182 (4)), step 2.
- `normalise`, `classify`: a transaction to its movement group (D181 (d), D183, D185, D187),
  steps 2 to 4.
- `match_transfer`: chain, bridge and wrap links (D184), step 5.
- `reconcile`: Σ movements against the chain's balance (D186 (2)), steps 2 to 4.
- `movement_hash` (here): what identifies a chain movement (D182 (2)), step 2.

Step 0 writes the registries and their loaders; every rule is a stub until its step.
"""

from hausbuch.schemas.chain import Chain


def movement_hash(chain: Chain, tx_hash: str, address_hmac: str, index: str, role: str) -> str:
    """D182 (2): SHA-256 over chain, tx hash, the own address's HMAC, index and role, joined by
    "\\x1f" like rules/exchange.movement_hash. Fixture tests/fixtures/phase7/movement_hash.yaml;
    built in step 2."""
    raise NotImplementedError("phase 7 step 2")
