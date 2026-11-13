"""Prices: ECB rates, the NAV rows an issuer file adds, and the Anlagen Übersicht tab
(docs/phases/phase-6.md D167, D174, §12, §13; phase 6 step 5b).

The price table's own shapes (PriceSourceKind, PriceDraft, PricePoint, NavPoint, the
PriceSource and IssuerNavAdapter protocols) stay in schemas/household.py, where step 0 wrote
them; they are re-exported here so price code imports one module.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from .common import Money, Schema
from .household import (
    AssetKind,
    CurrencyCode,
    NavPoint,
    PriceDraft,
    PriceEur,
    PricePoint,
    PriceSourceKind,
    Quantity,
)

__all__ = [
    "AssetPriceRow",
    "AssetRow",
    "AssetsOverview",
    "FxRate",
    "NavPoint",
    "NavRow",
    "OverviewAccount",
    "OverviewPosition",
    "PriceDraft",
    "PricePoint",
    "PriceSourceKind",
    "TypedPriceBody",
]


class FxRate(Schema):
    """One ECB reference rate: units of the currency per euro on an ECB day (D167 (3))."""

    on: date
    per_eur: PriceEur


class NavRow(Schema):
    """One price row an issuer's NAV adds (D174 (3)): the EUR price, and the native NAV and
    currency kept in `raw`, with the ECB day used for a NAV not quoted in EUR."""

    on: date
    price_eur: PriceEur
    nav: PriceEur
    currency: CurrencyCode
    rate_on: date | None = None


# --- The Anlagen Übersicht tab (§13, mockup 09) -----------------------------------------


class PriceLabel(Schema):
    """Where a shown price comes from, in the screen's words (D174 (4))."""

    source: PriceSourceKind
    on: date
    price_eur: PriceEur
    issuer: str | None = None  # issuer_nav: the adapter's key (spdr, ishares, ...)


class OverviewPosition(Schema):
    """One asset on one account: units from the confirmed movements to `on`, the price
    `rules.valuation.price_on` chose, and the value; `not_valued_reason` says why there is
    none (valuation.not_valued past the last reconciled date, price.no_source). `from_report`
    marks a price taken from a depot report (source statement) on a fund whose issuer has no
    adapter: „Kurs aus dem letzten Bericht“ (D174 (4))."""

    asset_id: UUID
    kind: AssetKind
    name: str
    symbol: str | None = None
    isin: str | None = None
    units: Quantity
    price: PriceLabel | None = None
    value_eur: Money | None = None
    stale: bool = False
    not_valued_reason: str | None = None
    from_report: bool = False
    reconciled_through: date | None = None


class OverviewAccount(Schema):
    account_id: UUID
    label: str
    kind: str
    positions: list[OverviewPosition] = Field(default_factory=list)
    value_eur: Money  # Σ of the valued positions


class AssetsOverview(Schema):
    """GET /assets/overview: every depot and exchange account with its positions on `on`
    (today by default), their total and the split by class (Depot, Krypto)."""

    on: date
    accounts: list[OverviewAccount] = Field(default_factory=list)
    total_eur: Money
    by_kind: dict[str, Money] = Field(default_factory=dict)  # "etf", "crypto", "cash", "stock"
    stale_positions: int = Field(default=0, ge=0)
    unvalued_positions: int = Field(default=0, ge=0)


class AssetRow(Schema):
    """GET /assets: an asset with how it is priced (D167 (1), D174 (2))."""

    id: UUID
    kind: AssetKind
    name: str
    symbol: str | None = None
    isin: str | None = None
    currency: CurrencyCode
    coingecko_id: str | None = None
    kraken_pair: str | None = None
    issuer: str | None = None  # the registry's issuer adapter for an ETF
    has_source: bool  # a fetched source can price it; False: „Kursquelle wählen“
    latest_price: PriceLabel | None = None
    updated_at: datetime


class TypedPriceBody(Schema):
    """POST /assets/{id}/prices: a price typed by a person („Kurs eintragen“, source user,
    the lowest precedence, D174 (4))."""

    on: date
    price_eur: PriceEur


class AssetPriceRow(Schema):
    id: UUID
    asset_id: UUID
    on: date
    price_eur: PriceEur
    source: PriceSourceKind
    fetched_at: datetime
