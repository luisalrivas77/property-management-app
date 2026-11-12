"""Household accounts, assets, movements, transfers, prices and net worth
(docs/phases/phase-6.md §10, §11; migration 0025).

Phase 6 step 0 writes the shapes; the steps that build each rule fill them (step 1:
ownership, taxonomy; step 2: transfers; step 4: the depot; step 5: the exchange and prices;
step 6: valuations and net worth). Money stays Decimal: EUR figures are numeric(14,2), units
numeric(28,10), prices numeric(20,8), each refusing a JSON number like every decimal type of
schemas/common.py.

The account sets (AccountKind, AccountPurpose) live in enums.py and the transfer leg in
allocation.py (MatchContext carries it); both are re-exported here so phase 6 code imports
one module.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Protocol, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .allocation import AllocationCategory, TransferLeg, TransferLegSource
from .common import (
    _STRING_ONLY,
    Money,
    MutableRecord,
    NonNegativeMoney,
    Record,
    Schema,
    _string_schema,
)
from .enums import (
    BANK_ACCOUNT_KINDS,
    AccountKind,
    AccountPurpose,
    ImportStatus,
    ReturnInputKind,
)

__all__ = [
    "BANK_ACCOUNT_KINDS",
    "AccountKind",
    "AccountPurpose",
    "TransferLeg",
    "TransferLegSource",
]

# --- Value types ----------------------------------------------------------------------

# numeric(28,10): units of an asset (an ETF's shares to 6 places, a coin's to 8 or more).
# Signed: a disposal or an out-transfer is negative on the account.
Quantity = Annotated[
    Decimal,
    Field(max_digits=28, decimal_places=10),
    _STRING_ONLY,
    _string_schema(10, signed=True),
]
NonNegativeQuantity = Annotated[
    Decimal,
    Field(ge=0, max_digits=28, decimal_places=10),
    _STRING_ONLY,
    _string_schema(10),
]
# numeric(20,8): a price in EUR per unit (a coin's price may need 8 places).
PriceEur = Annotated[
    Decimal,
    Field(gt=0, max_digits=20, decimal_places=8),
    _STRING_ONLY,
    _string_schema(8),
]
# An ISIN: country, nine alphanumerics, one check digit (the check digit is not verified).
Isin = Annotated[str, Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")]
AssetSymbol = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,12}$")]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
# A month's savings rate (saved / income) to 4 places, as a string like every figure; signed
# and not capped at 1: a month may save more than it earned, from earlier money.
SavingsRate = Annotated[
    Decimal,
    Field(max_digits=9, decimal_places=4),
    _STRING_ONLY,
    _string_schema(4, signed=True),
]


# --- Ownership (D157) -----------------------------------------------------------------


class OwnershipShare(Schema):
    owner_id: UUID
    share_numerator: int = Field(gt=0)
    share_denominator: int = Field(gt=0)
    # Why this split, kept with the record (6 Nov 2026: "joint 50/50 because the coins were
    # paid from the joint account"); the household's own words, never in an event payload.
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _proper(self) -> Self:
        if self.share_numerator > self.share_denominator:
            raise ValueError("a share is at most 1")
        return self

    @property
    def share(self) -> Fraction:
        return Fraction(self.share_numerator, self.share_denominator)


class SetOwnershipBody(Schema):
    """POST /accounts/{id}/ownership: the full current set. Σ = 1 and each owner once are
    checked here; that each owner exists is the service's (ownership.owner_missing)."""

    shares: list[OwnershipShare] = Field(min_length=1)
    effective_from: date | None = None  # None = today, set by the service

    @model_validator(mode="after")
    def _sum_one(self) -> Self:
        owners = [s.owner_id for s in self.shares]
        if len(owners) != len(set(owners)):
            raise ValueError("ownership.sum_not_one: each owner once")
        if sum((s.share for s in self.shares), Fraction(0)) != 1:
            raise ValueError("ownership.sum_not_one: the shares must sum to exactly 1")
        return self


class AccountOwnership(Record):
    """account_ownership row (append-only; a change is a new set under a new
    effective_from, an old row's superseded_by names the row that replaced it)."""

    account_id: UUID
    owner_id: UUID
    share_numerator: int = Field(gt=0)
    share_denominator: int = Field(gt=0)
    reason: str | None = None
    effective_from: date
    superseded_by: UUID | None = None


# --- Assets (D167) --------------------------------------------------------------------


class AssetKind(StrEnum):
    """asset.kind (migration 0025). EUR is an asset of kind cash, so an exchange's EUR
    balance is a position like any other."""

    CASH = "cash"
    ETF = "etf"
    STOCK = "stock"
    CRYPTO = "crypto"


class AssetCreate(Schema):
    kind: AssetKind
    symbol: AssetSymbol | None = None
    isin: Isin | None = None
    name: str = Field(min_length=1, max_length=200)
    currency: CurrencyCode = "EUR"
    coingecko_id: str | None = Field(default=None, min_length=1, max_length=100)
    kraken_pair: str | None = Field(default=None, min_length=1, max_length=20)
    decimals: int = Field(default=8, ge=0, le=10)

    @model_validator(mode="after")
    def _identified(self) -> Self:
        if self.symbol is None and self.isin is None:
            raise ValueError("an asset has a symbol or an ISIN")
        if self.kind in (AssetKind.ETF, AssetKind.STOCK) and self.isin is None:
            raise ValueError("a security is identified by its ISIN")
        return self


class Asset(AssetCreate, MutableRecord):
    pass


class AssetPatch(Schema):
    """PATCH /assets/{id}: the name and the price-source ids only (D167)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    coingecko_id: str | None = Field(default=None, min_length=1, max_length=100)
    kraken_pair: str | None = Field(default=None, min_length=1, max_length=20)


# --- Asset movements (D165, D166, D175) -----------------------------------------------


class MovementKind(StrEnum):
    """asset_movement.kind (migration 0025, §8). Phase 7 widens the CHECK with the chain
    kinds of the design's §2.2."""

    ACQUISITION = "acquisition"
    DISPOSAL = "disposal"
    INTERNAL_TRANSFER_IN = "internal_transfer_in"
    INTERNAL_TRANSFER_OUT = "internal_transfer_out"
    FEE = "fee"
    STAKING_REWARD = "staking_reward"
    CASHBACK_REWARD = "cashback_reward"
    DISTRIBUTION = "distribution"
    CAPITAL_INCOME_TAX = "capital_income_tax"
    UNCATEGORIZED = "uncategorized"


class MovementSource(StrEnum):
    """asset_movement.source: where the row was read from."""

    CSV = "csv"  # an exchange export (D166)
    TRADE_CONFIRMATION = "trade_confirmation"  # D165 (2)
    DISTRIBUTION_NOTE = "distribution_note"  # D165 (2)
    REPORT = "report"  # a report's Umsätze list, no confirmation stored (D165 (3))
    USER = "user"  # typed by hand


class MovementStatus(StrEnum):
    """asset_movement.status: only proposed -> confirmed moves (the trigger of 0025); a
    correction is a new row and the old one's superseded_by."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


class AssetMovementDraft(Schema):
    """One movement as a reader, the CSV import or the user proposes it (D175: the
    withdrawal's network, transaction hash and fee in the asset's units kept for phase 7)."""

    account_id: UUID
    asset_id: UUID
    kind: MovementKind
    quantity: Quantity  # signed on the account: in positive, out negative
    eur_amount: Money | None = None  # the EUR leg, signed like the account's cash
    fee_eur: NonNegativeMoney | None = None
    fee_quantity: NonNegativeQuantity | None = None
    tax_withheld_eur: NonNegativeMoney | None = None  # a distribution's taxes (phase 8)
    occurred_at: datetime
    external_ref: str | None = Field(default=None, min_length=1, max_length=200)
    network: str | None = Field(default=None, min_length=1, max_length=50)
    tx_hash: str | None = Field(default=None, min_length=1, max_length=200)
    source: MovementSource
    document_id: UUID | None = None
    transaction_id: UUID | None = None  # the cash leg on the depot's own statement

    @model_validator(mode="after")
    def _sourced(self) -> Self:
        documented = {
            MovementSource.TRADE_CONFIRMATION,
            MovementSource.DISTRIBUTION_NOTE,
            MovementSource.REPORT,
        }
        if self.source in documented and self.document_id is None:
            raise ValueError(f"a movement read from a {self.source} names its document")
        return self


class AssetMovement(AssetMovementDraft, Record):
    movement_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: MovementStatus
    superseded_by: UUID | None = None


class MovementLine(Schema):
    """The exchange CSV parser's output, format-independent (the StatementLine analogue,
    D166). One row per trade is assumed (Q-O59); step 5 settles it on the real export."""

    occurred_at: datetime
    type: str = Field(min_length=1)  # the export's own type text (Buy, Sell, Deposit, ...)
    asset: AssetSymbol
    quantity: Quantity
    eur_amount: Money | None = None
    fee_eur: NonNegativeMoney | None = None
    fee_quantity: NonNegativeQuantity | None = None
    external_ref: str | None = Field(default=None, min_length=1)  # the Transaction ID
    network: str | None = None
    tx_hash: str | None = None
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class MovementImportFormat(StrEnum):
    """movement_import.format (migration 0025)."""

    BISON_CSV = "bison_csv"


class MovementImport(MutableRecord):
    """movement_import row: the statement_import of an exchange export (D166). status reuses
    ImportStatus's four values (imported = waiting for the typed balances)."""

    account_id: UUID
    document_id: UUID
    format: MovementImportFormat
    rows_total: int = Field(ge=0)
    rows_new: int = Field(ge=0)
    rows_skipped: int = Field(ge=0)
    first_at: datetime | None = None
    last_at: datetime | None = None
    status: ImportStatus
    error_code: str | None = None


class HoldingBalance(Schema):
    asset_id: UUID
    units: NonNegativeQuantity


class DeclareHoldingsBody(Schema):
    """POST /movement-imports/{id}/holdings: the balance per asset the exchange shows, typed
    once per import (the pending_balance precedent of D21)."""

    balances: list[HoldingBalance] = Field(min_length=1)

    @model_validator(mode="after")
    def _once(self) -> Self:
        ids = [b.asset_id for b in self.balances]
        if len(ids) != len(set(ids)):
            raise ValueError("each asset once")
        return self


class HoldingSource(StrEnum):
    """holding_statement.source."""

    REPORT = "report"  # a depot_report (D165 (3))
    USER = "user"  # typed for an exchange import (D166)


class HoldingStatement(Record):
    """holding_statement row (append-only): what a report or the user declares one account
    held of one asset on as_of, and whether Σ movements agreed."""

    account_id: UUID
    asset_id: UUID
    as_of: date
    declared_units: NonNegativeQuantity
    declared_value_eur: NonNegativeMoney | None = None
    source: HoldingSource
    document_id: UUID | None = None
    import_id: UUID | None = None
    reconciled: bool | None = None  # None until checked
    difference_units: Quantity | None = None


# --- Transfers (D162) -----------------------------------------------------------------


class TransferLinkKind(StrEnum):
    """transfer_link.kind (migration 0025). Phase 7 adds chain, bridge and wrap."""

    BANK = "bank"
    EXCHANGE_EUR = "exchange_eur"
    DEPOT_CASH = "depot_cash"


class TransferMatchedBy(StrEnum):
    MATCHER = "matcher"
    USER = "user"


class TransferCandidate(Schema):
    out_leg: TransferLeg
    in_leg: TransferLeg
    kind: TransferLinkKind

    @model_validator(mode="after")
    def _directions(self) -> Self:
        if self.out_leg.amount >= 0 or self.in_leg.amount <= 0:
            raise ValueError("out_leg is negative, in_leg positive")
        if self.out_leg.account_id == self.in_leg.account_id:
            raise ValueError("a transfer links two different accounts")
        return self


class TransferLinkCreate(Schema):
    """POST /transfers (a link by hand): exactly one out and one in leg."""

    kind: TransferLinkKind
    out_transaction_id: UUID | None = None
    out_movement_id: UUID | None = None
    in_transaction_id: UUID | None = None
    in_movement_id: UUID | None = None

    @model_validator(mode="after")
    def _one_each(self) -> Self:
        if (self.out_transaction_id is None) == (self.out_movement_id is None):
            raise ValueError("exactly one of out_transaction_id, out_movement_id")
        if (self.in_transaction_id is None) == (self.in_movement_id is None):
            raise ValueError("exactly one of in_transaction_id, in_movement_id")
        return self


class TransferLink(TransferLinkCreate, Record):
    matched_by: TransferMatchedBy
    superseded_by: UUID | None = None


# --- Prices (D167, D174) --------------------------------------------------------------


class PriceSourceKind(StrEnum):
    """price.source (migration 0025). Named ...Kind because §11's PriceSource is the fetcher
    protocol below. The precedence among one day's rows is rules.valuation's data."""

    TRADE = "trade"
    STATEMENT = "statement"
    ISSUER_NAV = "issuer_nav"
    COINGECKO_DAILY = "coingecko_daily"
    KRAKEN_DAILY = "kraken_daily"
    ECB_FX = "ecb_fx"
    USER = "user"


class PriceDraft(Schema):
    asset_id: UUID
    on: date
    price_eur: PriceEur
    source: PriceSourceKind
    document_id: UUID | None = None
    raw: dict[str, JsonValue] = Field(default_factory=dict)  # the native figure and currency


class Price(PriceDraft, Record):
    fetched_at: datetime


class PricePoint(Schema):
    asset_id: UUID
    on: date
    price_eur: PriceEur
    source: PriceSourceKind
    fetched_at: datetime


class NavPoint(Schema):
    """One day of an issuer's published NAV (D174), in the fund's NAV currency."""

    on: date
    nav: PriceEur  # positive, 8 places; the currency is `currency`, not always EUR
    currency: CurrencyCode


class PriceSource(Protocol):
    """services/prices/: ecb_fx, coingecko_daily, kraken_daily, issuer_nav (D167 (3))."""

    def daily(self, asset: Asset, start: date, end: date) -> list[PriceDraft]: ...


class IssuerNavAdapter(Protocol):
    """services/prices/issuer_nav/: one adapter per issuer, keyed through the asset registry
    (rules/household/assets.yaml: ISIN -> issuer, fund_ref, nav_currency; D174 (2))."""

    issuer: str

    def fetch(self, fund_ref: str) -> list[NavPoint]: ...


# --- Valuations and net worth (D167) --------------------------------------------------


class Position(Schema):
    account_id: UUID
    asset_id: UUID
    units: Quantity
    reconciled_through: date | None = None


class Valuation(Schema):
    position: Position
    on: date
    price: PricePoint | None = None
    value_eur: Money | None = None
    stale: bool = False
    not_valued_reason: str | None = None  # valuation.not_valued, price.no_source

    @model_validator(mode="after")
    def _valued(self) -> Self:
        if (self.value_eur is None) == (self.not_valued_reason is None):
            raise ValueError("a valuation has a value or a reason it has none")
        return self


class NetWorthClass(StrEnum):
    """Mockup 07's bar (D167 (4))."""

    BANK = "bank"
    DEPOT = "depot"
    CRYPTO = "crypto"
    PROPERTY = "property"
    LIABILITIES = "liabilities"


class NetWorth(Schema):
    on: date
    total: Money
    by_class: dict[NetWorthClass, Money]
    by_owner: dict[UUID, Money]  # over accounts with shares and the property's shares
    unassigned_accounts: list[UUID] = Field(default_factory=list)
    property_estimate: Money
    loan_balances: Money  # positive: what is owed
    snapshot_id: UUID | None = None


class ValuationSnapshot(Record):
    """valuation_snapshot row (immutable): one per account, asset and month-end; asset_id
    None for a bank account's balance."""

    account_id: UUID
    asset_id: UUID | None = None
    as_of: date
    units: Quantity | None = None
    price_eur: PriceEur | None = None
    price_source: PriceSourceKind | None = None
    value_eur: Money
    net_worth_run_id: UUID


# --- The household taxonomy (D159) -----------------------------------------------------


class HouseholdGroup(StrEnum):
    """The group a household code is shown under (mockup 10's spending groups and the
    others; rules/household/taxonomy.py)."""

    INCOME = "income"
    HOUSING = "housing"
    GROCERIES = "groceries"
    FAMILY_CHILD = "family_child"
    MOBILITY = "mobility"
    INSURANCE = "insurance"
    SUBSCRIPTIONS = "subscriptions"
    LEISURE_TRAVEL = "leisure_travel"
    HEALTH = "health"
    OTHER = "other"
    TAX = "tax"
    MOVEMENTS = "movements"
    DEPOT = "depot"


class Relevance(StrEnum):
    INCOME = "income"
    SPENDING = "spending"
    TRANSFER = "transfer"
    TAX = "tax"


class ProposedInput(StrEnum):
    """The input a tax-relevant household line proposes (D159, D161), through the existing
    commands, confirmed by the user."""

    RETURN_SPECIAL_EXPENSE = "return_input:" + ReturnInputKind.SPECIAL_EXPENSE.value
    RETURN_HOUSEHOLD_SERVICES = "return_input:" + ReturnInputKind.HOUSEHOLD_SERVICES.value
    RETURN_CHILDCARE_COST = "return_input:" + ReturnInputKind.CHILDCARE_COST.value
    TAX_PRIVATE_COST = "tax_input:private_cost"


class HouseholdCode(Schema):
    """One row of rules/household/taxonomy.py's table."""

    category: AllocationCategory
    group: HouseholdGroup
    relevance: Relevance
    proposes: ProposedInput | None = None


class HouseholdCashflow(Schema):
    """GET /household/cashflow?month= (the Haushalt screen): confirmed allocations only."""

    month: Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
    income: Money
    spending_by_group: dict[HouseholdGroup, Money]
    saved: Money  # links whose in-leg is a savings, depot or exchange account (D162)
    surplus: Money
    savings_rate: SavingsRate | None = None  # saved / income; None without income
