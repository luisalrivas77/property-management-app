"""Prices and valuations (docs/phases/phase-6.md D167, D174, §9; fixtures
tests/fixtures/phase6/valuation.yaml and issuer_nav.yaml). Built in phase 6 steps 5 and 6.

- `price_on(prices, asset, on)`: the price an asset is valued at on a day, date first: the
  latest day on or before `on` that has a price, and among that day's rows the source
  highest in SOURCE_PRECEDENCE (D167 revised: a trade beats a CoinGecko price on the same
  day, a NAV beats a report value, but yesterday's NAV beats last quarter's trade price).
  Older than STALE_AFTER_DAYS -> stale. A foreign currency figure is converted by the ECB
  rate of its day (a weekend NAV by Friday's rate).
- `value(position, price, reconciled_through, on)`: units x price, rounded to the cent; a
  position past its reconciled date is not valued (valuation.not_valued); cash at 1.0.
"""

from collections.abc import Sequence
from datetime import date

from hausbuch.schemas.household import Position, PricePoint, PriceSourceKind, Valuation

STEP = "phase 6 steps 5 and 6 (prices and valuations)"

# One day's rows, best first (D167 (2) revised, D174 (3)). ecb_fx prices a currency, never
# a security, so it is last.
SOURCE_PRECEDENCE: tuple[PriceSourceKind, ...] = (
    PriceSourceKind.TRADE,
    PriceSourceKind.ISSUER_NAV,
    PriceSourceKind.STATEMENT,
    PriceSourceKind.KRAKEN_DAILY,
    PriceSourceKind.COINGECKO_DAILY,
    PriceSourceKind.USER,
    PriceSourceKind.ECB_FX,
)
STALE_AFTER_DAYS = 7
SOURCE_FAILED_AFTER_DAYS = 7  # D174: seven failed days raise price.source_failed

if set(SOURCE_PRECEDENCE) != set(PriceSourceKind):  # pragma: no cover
    raise RuntimeError("every price source needs a place in the precedence")


def price_on(prices: Sequence[PricePoint], on: date) -> PricePoint | None:
    raise NotImplementedError(STEP)


def value(position: Position, price: PricePoint | None, on: date) -> Valuation:
    raise NotImplementedError(STEP)
