"""Own money put into a property (Übersicht's property tile, Luis 21 Nov 2026: „Both figures“).
Pure, Decimal.

The tile's main figure stays the net worth's value less the loans (property_net); this is the
second line, „Eigenkapital eingesetzt“:

    purchase price
    + Σ the acquisition cost rows (the Nebenkosten; rules.tax.afa.cost_rows_total, the sum the
      AfA basis adds to the price)
    + Σ the loan costs the household paid itself: the private_cost rows of class financing
      (phase 4b D134) in force, of every tax year, each amount × its share (rules.tax._common.
      shared, as the Anlage V draft takes it)
    − Σ the principal of the property's loans

Each private_cost row counts once: a superseded or withdrawn row (a correction, a move to the
year paid) is not in force. Loan costs booked as loan_fees allocations on the rental account
are not added: no read sums them per property over all years (the draft sums them per tax
year, phase 4's figures as running cash), and phase 4's yearly cash flow already holds them.
So a financing bill recorded as a private_cost row counts through its row only, whichever
account paid it.

It is phase 4's initial equity (rules.performance.sale, price + cost rows − loan principal)
plus the loan costs paid privately. Nothing here is stored or feeds rule_version().
"""

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.property import PropertiesPaidIn, PropertyPaidIn
from hausbuch.schemas.tax import AfaCostRow, TaxInputFact, TaxInputKind

from .tax._common import in_force, shared
from .tax.afa import cost_rows_total
from .tax.rounding import cents

ZERO = Decimal("0.00")
FINANCING = "financing"


def own_loan_costs(tax_inputs: Iterable[TaxInputFact]) -> Decimal:
    """Σ the private_cost rows of class financing in force (all tax years), amount × share."""
    rows = [
        r
        for r in in_force(tax_inputs, TaxInputKind.PRIVATE_COST)
        if r.value.cost_class == FINANCING and r.value.amount is not None
    ]
    return cents(sum((shared(r.value.amount, r) for r in rows), ZERO))


def paid_in(
    *,
    property_id: UUID,
    label: str,
    purchase_price: Decimal | None,
    cost_rows: Sequence[AfaCostRow],
    tax_inputs: Sequence[TaxInputFact],
    loan_principals: Iterable[Decimal],
    ownership_ended_on: date | None,
    today: date,
) -> PropertyPaidIn:
    """One property's parts and its paid_in; paid_in None with a reason when the property has
    no purchase price, or its ownership ended on or before today (the net worth no longer
    counts it)."""
    acquisition = cost_rows_total(cost_rows)
    loan_costs = own_loan_costs(tax_inputs)
    principal = cents(sum(loan_principals, ZERO))
    reason = None
    total = None
    if purchase_price is None:
        reason = "no_purchase_price"
    elif ownership_ended_on is not None and ownership_ended_on <= today:
        reason = "ownership_ended"
    else:
        total = cents(purchase_price + acquisition + loan_costs - principal)
    return PropertyPaidIn(
        property_id=property_id,
        label=label,
        purchase_price=purchase_price,
        acquisition_costs=acquisition,
        own_loan_costs=loan_costs,
        loan_principal=principal,
        paid_in=total,
        reason=reason,
    )


def total(properties: Sequence[PropertyPaidIn]) -> PropertiesPaidIn:
    """Σ paid_in over the properties that have one."""
    known = [p.paid_in for p in properties if p.paid_in is not None]
    return PropertiesPaidIn(properties=list(properties), total_paid_in=cents(sum(known, ZERO)))
