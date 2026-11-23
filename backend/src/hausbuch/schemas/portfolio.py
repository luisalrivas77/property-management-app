"""GET /assets/performance (docs/phases/phase-10.md §7.6 Anlagen, items 1–3, build decision
(b)): each portfolio's gain since the start and its money-weighted yearly return, and the
totals of the Anlagen tabs. Computed on read, never stored (services/portfolio_performance.py,
rules/performance/portfolio.py)."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from .common import _STRING_ONLY, Money, NonNegativeMoney, ReturnRate, Schema, _string_schema
from .enums import AccountKind
from .household import Quantity

# A gain as a share of the money paid in, signed, six places (−1 = everything lost; a gain
# since the start never falls below it, since a value and the money taken out are ≥ 0).
GainRatio = Annotated[
    Decimal,
    Field(ge=-1, max_digits=12, decimal_places=6),
    _STRING_ONLY,
    _string_schema(6, signed=True),
]


class NoRateReason(StrEnum):
    """Why a portfolio shows no yearly return (rules.performance.portfolio.NoRate)."""

    SHORT_HISTORY = "short_history"  # less than a year since the start: euros and percent only
    UNPRICED = "unpriced"  # a payment's euros are unknown (units moved on a day with no price)
    # A position held today has no value (no price, or units a depot report contradicts): the
    # value would read too low while its purchase stays paid in, so no gain is shown.
    UNVALUED = "unvalued"
    NO_FLOWS = "no_flows"  # nothing paid in or taken out
    NO_RATE = "no_rate"  # the payments have no rate


class PortfolioFlowKind(StrEnum):
    """What a payment in or out of a portfolio is (decision (b))."""

    # The portfolio's value on the first day the app knows it (item 3): a depot's cash anchor
    # and the funds it held that day.
    START = "start"
    # A line of a depot's cash statement booked as a transfer (Einlage, Auszahlung), or an
    # exchange's euro deposit or withdrawal.
    TRANSFER = "transfer"
    # A bank payment to the depot whose statement does not reach its day yet (phase 6 D292).
    IN_TRANSIT = "in_transit"
    # Units moved in or out (between own depots, wallets and exchanges, or from outside),
    # valued at that day's price.
    UNITS = "units"
    # A purchase or sale on a depot without cash statements (its cash is in no figure).
    TRADE = "trade"
    # A distribution on a depot without cash statements: it left the valued portfolio.
    DISTRIBUTION = "distribution"


class PortfolioFlow(Schema):
    """One payment in (+) or out (−) of a portfolio, with the rows it was read from."""

    on: date
    amount_eur: Money | None  # None: no price that day for the units moved
    kind: PortfolioFlowKind
    # A move to or from another own portfolio: it nets out in the ETF, Krypto and Anlagen
    # totals, which count only money from or to outside.
    internal: bool = False
    counterpart_account_id: UUID | None = None
    transaction_id: UUID | None = None
    movement_id: UUID | None = None
    asset_id: UUID | None = None
    units: Quantity | None = None


class PortfolioFigures(Schema):
    """Gain since the start = value + taken out − paid in; `gain_ratio` = gain ÷ paid in;
    `yearly_return` the money-weighted return (xirr), None for less than a year of history
    (`no_rate`). Every figure None (but the value) when a payment's euros are unknown
    (`unpriced_flows`) or a position held today has no value (`unvalued_positions`, the value
    then leaving it out); a total is None when one of its portfolios is."""

    start: date | None = None
    value_eur: Money
    paid_in_eur: NonNegativeMoney | None = None
    taken_out_eur: NonNegativeMoney | None = None
    gain_eur: Money | None = None
    gain_ratio: GainRatio | None = None
    yearly_return: ReturnRate | None = None
    no_rate: NoRateReason | None = None
    unpriced_flows: int = Field(default=0, ge=0)
    unvalued_positions: int = Field(default=0, ge=0)


class PortfolioPerformance(Schema):
    account_id: UUID
    label: str
    kind: AccountKind
    figures: PortfolioFigures
    flows: list[PortfolioFlow] = Field(default_factory=list)
    # Lines of a depot's cash statement inside its balance that no one has booked yet: one
    # may be a payment in or out the figures miss („siehe Zu prüfen“).
    unbooked_lines: int = Field(default=0, ge=0)
    # A wallet with read transactions that wait for a person: they are in no figure yet.
    waiting: bool = False


class AssetsPerformance(Schema):
    """The Anlagen tabs' figures on `as_of` (today, Berlin). Each portfolio's value is its
    `total_eur` on GET /assets/overview (positions, a depot's cash and money on its way);
    `etf` sums the depots, `crypto` the exchanges and wallets, `invested` both, each with the
    money from or to outside the set only. `total_eur` adds the property's equity (its
    estimated value less its loans, the net worth's own figures), an estimate."""

    as_of: date
    portfolios: list[PortfolioPerformance] = Field(default_factory=list)
    etf: PortfolioFigures
    crypto: PortfolioFigures
    invested: PortfolioFigures  # ETF and Krypto together
    property_estimate_eur: Money
    property_loans_eur: Money
    property_equity_eur: Money
    total_eur: Money  # invested value + property equity
    # „Ausschüttungen <Jahr>“: the distributions booked on the depots in `as_of`'s year
    distributions_year: int
    distributions_eur: Money
    distributions_tax_eur: Money


__all__ = [
    "AssetsPerformance",
    "GainRatio",
    "NoRateReason",
    "PortfolioFigures",
    "PortfolioFlow",
    "PortfolioFlowKind",
    "PortfolioPerformance",
]
