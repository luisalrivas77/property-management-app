"""The Anlagen › Krypto tab and the year-end holdings export (docs/phases/phase-7.md D188 (5),
D190, §13, §14; step 6).

Every euro figure here is computed by `rules.holdings` from the lots of `rules.lots` and the
day's price (principle 1); every lot carries its id and the movement that created it
(principle 2). A figure that would contain an unpriced lot or an unpriced coin is not a
number: it is None, or the total leaves it out and says how many (D188 (4)). The figures are
informational until phase 8 applies the Freigrenze and the loss rules.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .chain import Lot
from .common import Money, Schema, Sha256Hex
from .household import OwnershipShare, PriceEur, Quantity


class LotPriceNeed(Schema):
    """„Kurs eintragen“ for this coin and day prices the lot (a typed `user` price, D167)."""

    asset_id: UUID
    symbol: str | None = None
    on: date


class CryptoLot(Lot):
    """An open lot as the tab shows it: tax-free today or from `tax_free_from`, its value at
    the coin's price, and what resolves a missing cost. A lot of source `unknown` (an in-leg
    no transfer explains, a correction) has no price need: its cost is unknown for another
    reason, and the tab names that instead."""

    tax_free: bool
    value_eur: Money | None = None
    price_needs: list[LotPriceNeed] = Field(default_factory=list)


class CryptoCoin(Schema):
    """One coin on one account (FIFO is per account and coin, D188 (1))."""

    account_id: UUID
    asset_id: UUID
    symbol: str | None = None
    name: str = ""
    units: Quantity
    price_eur: PriceEur | None = None
    price_on: date | None = None
    value_eur: Money | None = None  # None: no price
    cost_eur: Money | None = None  # None: a lot without a cost
    cost_priced_eur: Money  # Σ of the lots with a cost
    unpriced_lots: int = Field(ge=0)
    unrealized_eur: Money | None = None  # value − cost, only when both are known
    tax_free_units: Quantity
    tax_free_value_eur: Money | None = None
    next_tax_free_from: date | None = None
    next_tax_free_units: Quantity | None = None
    reconciled: bool = True
    lots: list[CryptoLot] = Field(default_factory=list)


class CryptoAccount(Schema):
    account_id: UUID
    label: str
    kind: str
    provider: str | None = None
    ownership: list[OwnershipShare] = Field(default_factory=list)
    value_eur: Money  # Σ of its valued coins
    unvalued_coins: int = Field(default=0, ge=0)


class TranchePart(Schema):
    account_id: UUID
    asset_id: UUID
    symbol: str | None = None
    units: Quantity
    value_eur: Money | None = None  # None: the coin has no price


class NextTranche(Schema):
    """„Nächste Tranche steuerfrei“: the earliest future `tax_free_from` over every open lot,
    with the units of every coin that becomes free that day (D188 (5))."""

    on: date
    parts: list[TranchePart] = Field(default_factory=list)
    value_eur: Money | None = None  # None when one of its coins has no price


class CryptoHoldings(Schema):
    """GET /holdings/crypto: the Krypto tab on `on` (today by default)."""

    on: date
    total_eur: Money  # Σ of the valued coins
    unvalued_coins: int = Field(default=0, ge=0)
    unrealized_eur: Money  # Σ over the coins whose value and cost are both known
    unrealized_excluded: int = Field(default=0, ge=0)  # coins left out of it
    tax_free_today_eur: Money  # Σ value of the lots free today, of the priced coins
    tax_free_today_excluded: int = Field(default=0, ge=0)  # free lots of unpriced coins
    next_tranche: NextTranche | None = None
    accounts: list[CryptoAccount] = Field(default_factory=list)
    coins: list[CryptoCoin] = Field(default_factory=list)
    # Movements that took more units than any lot held (an incomplete history): their units
    # have no lot, never a cost (D188 (4)).
    unfilled_movements: int = Field(default=0, ge=0)


class HoldingsExportCreate(Schema):
    """POST /exports/holdings: the statement of 31 Dec of `year` for every wallet and exchange
    account with a confirmed movement on or before it (D190)."""

    year: int = Field(ge=2009, le=2100)


class HoldingsExport(Schema):
    """A `holdings_export` row (immutable, D190) without its statement, with signed download
    URLs valid a few minutes (fetch the list right before downloading)."""

    id: UUID
    account_id: UUID
    account_label: str
    as_of: date
    pdf_sha256: Sha256Hex
    csv_sha256: Sha256Hex
    created_at: datetime
    pdf_url: str | None = None
    csv_url: str | None = None


HoldingsFileFormat = Literal["pdf", "csv"]
