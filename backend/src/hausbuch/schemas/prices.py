"""Prices: ECB rates, the NAV rows an issuer file adds, and the Anlagen Übersicht tab
(docs/phases/phase-6.md D167, D174, §12, §13; phase 6 step 5b).

The price table's own shapes (PriceSourceKind, PriceDraft, PricePoint, NavPoint, the
PriceSource and IssuerNavAdapter protocols) stay in schemas/household.py, where step 0 wrote
them; they are re-exported here so price code imports one module.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from .chain import Chain, ContractId, TokenSymbol, WalletWaiting
from .common import Money, Schema
from .household import (
    AssetKind,
    CurrencyCode,
    DepotDocumentStatus,
    DepotPosition,
    NavPoint,
    PriceDraft,
    PriceEur,
    PricePoint,
    PriceSourceKind,
    Quantity,
)
from .net_worth import NetWorthLine

__all__ = [
    "AssetPriceRow",
    "AssetRow",
    "AssetsOverview",
    "EtfAccountToday",
    "EtfPositionToday",
    "EtfToday",
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
    # spot (phase 10 D291): the moment the price was read, for „Stand <Uhrzeit>“.
    read_at: datetime | None = None


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
    # A depot's cash balance and money on its way to it, the net worth's own account lines
    # (D292), so the account's total equals its lines in the net worth.
    cash: list[NetWorthLine] = Field(default_factory=list)
    total_eur: Money  # value_eur + Σ cash


class AssetsOverview(Schema):
    """GET /assets/overview: every depot and exchange account with its positions on `on`
    (today by default), their total and the split by class (Depot, Krypto)."""

    on: date
    accounts: list[OverviewAccount] = Field(default_factory=list)
    total_eur: Money
    by_kind: dict[str, Money] = Field(default_factory=dict)  # "etf", "crypto", "cash", "stock"
    stale_positions: int = Field(default=0, ge=0)
    unvalued_positions: int = Field(default=0, ge=0)
    # Wallets whose read movements wait for a person: not in `accounts` yet (16 Nov 2026).
    waiting: list[WalletWaiting] = Field(default_factory=list)


# --- The Anlagen ETF tab at the newest daily price (phase 10 D280) ----------------------


class EtfPositionToday(DepotPosition):
    """One fund on the ETF tab (phase 10 D280): the row shows `units` and `value_today_eur`,
    both copied from the Übersicht's OverviewPosition of the same account, asset and day
    (`rules.valuation`, D167), with the price's source and day in `price`; never a sum or a
    product of its own. `from_report` with `price.on` is „Kurs aus dem Bericht vom …“
    (D174 (4)); a fund past its last reconciled report has no value and `not_valued_reason`
    valuation.not_valued (D165 (3), „siehe Zu prüfen“). `gain_today_eur` is
    `value_today_eur` − `cost_eur` while both are shown. The inherited report figures
    (`report_*`, `value_eur`, `gain_eur`, `reconciled`) are the panel's check only. A fund
    the Übersicht does not hold on `on` (0 units then) has no value."""

    price: PriceLabel | None = None
    value_today_eur: Money | None = None
    gain_today_eur: Money | None = None
    stale: bool = False
    not_valued_reason: str | None = None
    from_report: bool = False


class EtfAccountToday(Schema):
    account_id: UUID
    label: str
    positions: list[EtfPositionToday] = Field(default_factory=list)
    value_eur: Money  # Σ of the valued positions: the Übersicht's value of this account
    # The depot's cash balance and the money on its way to it, as the net worth counts them
    # (net_worth.depot_cash, 23 Nov 2026), and the portfolio's total: funds + those lines.
    cash: list[NetWorthLine] = Field(default_factory=list)
    total_eur: Money


class EtfToday(Schema):
    """GET /assets/etf?on=: every depot account with its funds valued on `on` (today by
    default) as the Übersicht and the net worth value them (D280), and the depot's
    documents. `total_eur` is Σ of the accounts' values."""

    on: date
    accounts: list[EtfAccountToday] = Field(default_factory=list)
    total_eur: Money
    documents: list[DepotDocumentStatus] = Field(default_factory=list)


class AssetRow(Schema):
    """GET /assets: an asset with how it is priced (D167 (1), D174 (2)). A chain asset
    (phase 7 D180 amended 15 Nov 2026) also carries its chain, a token's contract, the
    symbol-level asset it is priced as and its spam flag; its symbol is a label
    (TokenSymbol: wstETH, USD₮0), so it reads without failing."""

    id: UUID
    kind: AssetKind
    name: str
    symbol: TokenSymbol | None = None
    chain: Chain | None = None
    contract_address: ContractId | None = None
    priced_as_asset_id: UUID | None = None
    spam: bool = False
    isin: str | None = None
    currency: CurrencyCode
    coingecko_id: str | None = None
    kraken_pair: str | None = None
    issuer: str | None = None  # the registry's issuer adapter for an ETF
    # D264 (8): „Lang & Schwarz“ for a fund with no issuer source priced by the exchange
    price_venue: str | None = None
    has_source: bool  # a fetched source can price it; False: „Kursquelle wählen“
    latest_price: PriceLabel | None = None
    updated_at: datetime


class TypedPriceBody(Schema):
    """POST /assets/{id}/prices: a price typed by a person („Kurs eintragen“, source user;
    it wins on its own day, D167 amended 13 Nov 2026)."""

    on: date
    price_eur: PriceEur


class AssetPriceRow(Schema):
    id: UUID
    asset_id: UUID
    on: date
    price_eur: PriceEur
    source: PriceSourceKind
    fetched_at: datetime
