"""The sale at the end of a projection (docs/phases/phase-4.md §5.5, D88, D89). Pure, Decimal.

The sale contract, the transfer and the price's Zufluss are all on 31 December of the sale
year (D88). §23 applies when that day is before `tax_free_from` (phase 3 spekulation.py); a
property without a contract date has no such day and is treated as outside §23 (the
projection states it as an assumption).

- selling costs C = value × rate, half-up to the cent.
- gain = V − C − (purchase price + Σ cost rows + Σ basis additions − building AfA claimed);
  the kitchen's own AfA (D80, wk.afa_movable) is never added back.
- Each owner's share of the gain is half-up to the cent, the last owner taking the
  remainder; it is below the Freigrenze when 0 < share < 1,000 (§23 Abs. 3 S. 5, a cliff,
  tested per spouse). The taxed gain is the sum of the shares that are positive and not below
  it; a loss is taxed at 0.00 and never refunded (§23 Abs. 3 S. 7: "nicht verrechenbar").
- sale tax T = T(base + R + taxed gain) − T(base + R) under the sale year's tariff, with base
  the household's zvE without the property and R the property's result of the sale year
  (D82's difference, never gain × rate); under a marginal_rate_override it is taxed gain ×
  rate, half-up. Without a base and without an override the tax cannot be computed and is
  0.00 (the caller states it as an assumption).
- E₀ = price + Σ cost rows − Σ loan principal; profit = (V − C − T − L) + Σ CF − E₀; the bridge
  rows (appreciation V − price, loan paid down L₀ − L, Σ CF, −Σ cost rows, −C, −T) sum to it.
- flows (D89): −E₀ on the purchase date, each year's net cash on 31 Dec, the net proceeds on
  the sale date; CAGR over the days from the purchase date to the sale ÷ 365.25, IRR by XIRR.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from hausbuch.rules.tax.rounding import cents
from hausbuch.rules.tax.tariff import total_tax
from hausbuch.schemas.performance import (
    BridgeRow,
    DatedFlow,
    OwnerGain,
    OwnerTaxProfile,
    SaleResult,
    Tariff,
)
from hausbuch.schemas.tax import SourceRef, TaxOwner

from .irr import cagr, xirr, years_between

ZERO = Decimal("0.00")
FREIGRENZE_23 = Decimal("1000")  # §23 Abs. 3 S. 5 EStG, per spouse (BMF 5 Oct 2000 Rz. 41)


@dataclass(frozen=True)
class SaleFacts:
    """What the sale reads (sale.yaml's facts, plus the basis additions and an override)."""

    sale_year: int
    purchase_date: date
    tax_free_from: date | None
    purchase_price: Decimal
    cost_rows: Decimal  # Σ the property's acquisition cost rows
    loan_principal: Decimal  # Σ the loans' principal, for E₀
    value: Decimal  # V at the sale: the sale year's projected value
    selling_costs_rate: Decimal
    afa_claimed: Decimal  # Σ building AfA of every year owned through the sale year
    loan_balance: Decimal  # L at the sale
    zve_base: Decimal | None  # the sale year's household zvE without the property
    result_sale_year: Decimal  # R: the property's taxable result of the sale year
    shares: Sequence[TaxOwner]  # the owners' shares of the property
    profiles: Sequence[OwnerTaxProfile]  # their church tax status, for T
    cash_flows: Sequence[DatedFlow]  # CF_y on 31 Dec of each year, actual and projected
    basis_additions: Decimal = ZERO  # Σ costs capitalised into the basis (D73)
    override_rate: Decimal | None = None  # the sale year's marginal_rate_override
    joint: bool = True
    value_sources: list[SourceRef] = field(default_factory=list)
    balance_sources: list[SourceRef] = field(default_factory=list)
    afa_sources: list[SourceRef] = field(default_factory=list)
    tax_sources: list[SourceRef] = field(default_factory=list)


def _owner_gains(gain: Decimal, shares: Sequence[TaxOwner]) -> list[OwnerGain]:
    """The gain split by the owners' shares, half-up, the last owner taking the remainder."""
    gains: list[OwnerGain] = []
    left = gain
    for i, owner in enumerate(shares):
        last = i == len(shares) - 1
        part = left if last else cents(gain * owner.share.numerator / owner.share.denominator)
        left -= part
        gains.append(
            OwnerGain(
                owner_id=owner.owner_id,
                share=owner.share,
                gain=part,
                below_freigrenze=0 < part < FREIGRENZE_23,
            )
        )
    return gains


def _sale_tax(f: SaleFacts, taxed: Decimal, tariff: Tariff) -> Decimal:
    if taxed <= 0:
        return ZERO
    if f.override_rate is not None:
        return cents(taxed * f.override_rate)
    if f.zve_base is None:
        return ZERO
    base = f.zve_base + f.result_sale_year
    with_gain = total_tax(base + taxed, tariff, f.profiles, f.joint)
    without = total_tax(base, tariff, f.profiles, f.joint)
    return cents(with_gain.total - without.total)


def _sum(flows: Iterable[DatedFlow]) -> Decimal:
    return sum((f.amount for f in flows), ZERO)


def sale(f: SaleFacts, tariff: Tariff) -> SaleResult:
    """D88, D89 for one sale on 31 Dec of f.sale_year (see the module doc)."""
    sale_date = date(f.sale_year, 12, 31)
    taxable = f.tax_free_from is not None and sale_date < f.tax_free_from
    selling = cents(f.value * f.selling_costs_rate)

    gain: Decimal | None = None
    owners: list[OwnerGain] = []
    tax = ZERO
    if taxable:
        basis = f.purchase_price + f.cost_rows + f.basis_additions - f.afa_claimed
        gain = f.value - selling - basis
        owners = _owner_gains(gain, f.shares)
        if owners:
            taxed = sum((o.gain for o in owners if o.gain > 0 and not o.below_freigrenze), ZERO)
        else:  # no owner shares known: the whole gain, without the per-spouse Freigrenze
            taxed = max(gain, ZERO)
        tax = _sale_tax(f, taxed, tariff)

    net = f.value - selling - tax - f.loan_balance
    equity = f.purchase_price + f.cost_rows - f.loan_principal
    cash = _sum(f.cash_flows)
    profit = net + cash - equity
    cf_refs = [s for flow in f.cash_flows for s in flow.sources]
    bridge = [
        BridgeRow(key="appreciation", amount=f.value - f.purchase_price, sources=f.value_sources),
        BridgeRow(
            key="loan_paid_down",
            amount=f.loan_principal - f.loan_balance,
            sources=f.balance_sources,
        ),
        BridgeRow(key="cash_flow", amount=cash, sources=cf_refs),
        BridgeRow(key="buying_costs", amount=-f.cost_rows),
        BridgeRow(key="selling_costs", amount=-selling, sources=f.value_sources),
        BridgeRow(key="sale_tax", amount=-tax, sources=f.tax_sources),
    ]
    flows = [
        DatedFlow(on=f.purchase_date, amount=-equity, kind="initial_equity"),
        *(
            DatedFlow(on=c.on, amount=c.amount, kind="cash_flow", sources=c.sources)
            for c in f.cash_flows
        ),
        DatedFlow(on=sale_date, amount=net, kind="net_proceeds", sources=f.value_sources),
    ]
    return SaleResult(
        sale_date=sale_date,
        tax_free_from=f.tax_free_from,
        taxable=taxable,
        value=f.value,
        selling_costs=selling,
        afa_claimed=f.afa_claimed,
        gain=gain,
        gain_per_owner=owners,
        freigrenze_applied=any(o.below_freigrenze for o in owners),
        sale_tax=tax,
        loan_balance=f.loan_balance,
        net_proceeds=net,
        initial_equity=equity,
        profit=profit,
        bridge=bridge,
        cagr=cagr(equity, profit, years_between(f.purchase_date, sale_date)),
        irr=xirr([(fl.on, fl.amount) for fl in flows]),
        flows=flows,
    )
