"""Σ movements against the chain's balance (docs/phases/phase-7.md D186 (2), (3), §9).

`reconcile` compares, per address and asset, the confirmed and proposed movements (in
positive, out and fee negative, in base units) with the source's balance at the sync's block:
equal → reconciled; else the difference per asset and `wallet.unreconciled`. A balance-only
address compares its balance with Σ linked deposits (`wallet.coins_left` on a shortfall). No
tolerance. Built in steps 2 to 4. Fixture tests/fixtures/phase7/wallet_reconcile.yaml.
"""

from collections.abc import Mapping

from hausbuch.schemas.chain import AssetKey


def reconcile(
    movements: Mapping[AssetKey, int], balances: Mapping[AssetKey, int]
) -> dict[AssetKey, int]:
    """The difference (balance − Σ movements) per asset that does not agree; {} = reconciled."""
    raise NotImplementedError("phase 7 steps 2 to 4")
