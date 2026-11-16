"""Σ movements against the chain's balance (docs/phases/phase-7.md D186 (2), (3), §9).

`reconcile` compares, per address and asset, the confirmed and proposed movements (in
positive, out and fee negative, in base units) with the source's balance at the sync's block:
equal → reconciled; else the difference per asset and `wallet.unreconciled`. A balance-only
address compares its balance with Σ linked deposits (`wallet.coins_left` on a shortfall). No
tolerance. Built in steps 2 to 4. Fixture tests/fixtures/phase7/wallet_reconcile.yaml.
"""

from collections.abc import Hashable, Iterable, Mapping

from hausbuch.schemas.chain import AssetKey


def reconcile[K: Hashable](movements: Mapping[K, int], balances: Mapping[K, int]) -> dict[K, int]:
    """The difference (balance − Σ movements) per asset that does not agree; {} = reconciled.
    An asset on one side only counts as 0 on the other. Keys are any hashable (an AssetKey
    in the sync, the fixture's asset names in the tests)."""
    diff: dict[K, int] = {}
    for key in list(balances) + [k for k in movements if k not in balances]:
        d = balances.get(key, 0) - movements.get(key, 0)
        if d:
            diff[key] = d
    return diff


def sum_movements[K: Hashable](signed: Iterable[tuple[K, int]]) -> dict[K, int]:
    """Σ signed base units per key (in positive, out and fee negative). The caller leaves spam
    out (D186 (2)) and counts confirmed and proposed movements alike."""
    sums: dict[K, int] = {}
    for key, units in signed:
        sums[key] = sums.get(key, 0) + units
    return sums


def sum_balance_parts[K: Hashable](parts: Iterable[tuple[K, int]]) -> dict[K, int]:
    """The source's balance per asset as the sum of its parts (Solana: the main account and its
    token accounts; Bitcoin: one sum per address)."""
    return sum_movements(parts)


__all__ = ["AssetKey", "reconcile", "sum_balance_parts", "sum_movements"]
