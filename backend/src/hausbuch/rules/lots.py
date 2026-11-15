"""Lots and the holding period (docs/phases/phase-7.md D187, D188, §8); built in step 6.

Pure, on read, no table (phase 3 D59's precedent):
- `lots(movements, links, prices, on)`: FIFO per account and asset over confirmed movements
  and links; a lot from an acquisition, a reward, an airdrop or a linked transfer in (the
  moved lot keeps its day and cost, D184 (4)); an unpriced cost stays None, never 0 €.
- `consume(lots, units)`: which lots a disposal, an outgoing transfer or a fee takes, the
  oldest `acquired_on` first, then the oldest `occurred_at`.
- `tax_free_from(acquired_on)`: §23 Abs. 1 S. 1 Nr. 2 EStG with §108 Abs. 1 AO and §§ 187
  Abs. 1, 188 Abs. 2 and 3 BGB: the day after the anniversary (the last day of the month when
  the anniversary does not exist).
- `berlin_day(occurred_at)`: the Europe/Berlin calendar day of a UTC timestamp (D188 (2)).

Fixtures tests/fixtures/phase7/lots.yaml, holding_period.yaml.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from hausbuch.schemas.chain import Lot, LotUse
from hausbuch.schemas.household import AssetMovement, PricePoint, TransferLink


def lots(
    movements: Sequence[AssetMovement],
    links: Sequence[TransferLink],
    prices: Sequence[PricePoint],
    on: date,
) -> list[Lot]:
    raise NotImplementedError("phase 7 step 6")


def consume(open_lots: Sequence[Lot], units: Decimal) -> list[LotUse]:
    raise NotImplementedError("phase 7 step 6")


def tax_free_from(acquired_on: date) -> date:
    raise NotImplementedError("phase 7 step 6")


def berlin_day(occurred_at: datetime) -> date:
    raise NotImplementedError("phase 7 step 6")
