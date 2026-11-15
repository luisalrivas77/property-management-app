"""A swap group's value and its question (docs/phases/phase-7.md D183 (5), D188 (4), §6 (3), (4));
built in step 2.

- `value_swap`: Rn. 58 of the BMF letter of 6 Mar 2025: the received asset's price × its units
  first, else the given asset's, to the cent (ROUND_HALF_UP). That one figure is the
  acquisition's cost and the disposal's proceeds. Neither priced → None, never 0 €.
- `price_question`: the question of a swap with a leg no price covers, the received leg named
  first („Kurs fehlt für USDC am 12.03.2026“, „Kurs fehlt für USDC und SOL am 12.03.2026“).
- `swap_question`: a swap on a program no decoder row reads asks what it was first; a listed
  one asks only for a missing price.

The prices are the group's Berlin day's (rules/valuation.price_on gives them); the routing
(confirmed or proposed) is rules/review.route_group's. Fixture
tests/fixtures/phase7/swap_group.yaml.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class SwapLeg:
    asset: str  # the priced_as symbol
    units: Decimal
    role: Literal["given", "received"]


def value_swap(
    legs: Sequence[SwapLeg], prices: Mapping[str, Decimal]
) -> tuple[Decimal | None, str | None]:
    """The swap's EUR value and the asset that priced it."""
    for role in ("received", "given"):
        for leg in legs:
            if leg.role == role and leg.asset in prices:
                return (leg.units * prices[leg.asset]).quantize(CENT, ROUND_HALF_UP), leg.asset
    return None, None


def missing_prices(legs: Sequence[SwapLeg], prices: Mapping[str, Decimal]) -> list[str]:
    """The assets without the day's price, the received leg first."""
    missing: list[str] = []
    for role in ("received", "given"):
        for leg in legs:
            if leg.role == role and leg.asset not in prices and leg.asset not in missing:
                missing.append(leg.asset)
    return missing


def price_question(missing: Sequence[str], day: date) -> str | None:
    if not missing:
        return None
    names = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + f" und {missing[-1]}"
    return f"Kurs fehlt für {names} am {day:%d.%m.%Y}"


def swap_question(
    program_question: str | None,
    legs: Sequence[SwapLeg],
    prices: Mapping[str, Decimal],
    day: date,
) -> str | None:
    """The one question a swap's card asks: what it was, while no decoder reads its program;
    then the missing price."""
    return program_question or price_question(missing_prices(legs, prices), day)
