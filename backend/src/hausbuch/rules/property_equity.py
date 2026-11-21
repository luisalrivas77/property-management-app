"""Own money put into a property (Übersicht's property tile, Luis 21 Nov 2026: „Both figures“).
Pure, Decimal.

The tile's headline is now „Zurück bisher“ (rules.performance.payback: the cash back so far,
measured against this figure, „von … eingesetzt“), with the net worth's value less the loans
(property_net) beside it; the panel lists this figure's parts under „Eigenkapital eingesetzt“:

    purchase price
    + Σ the acquisition cost rows (the Nebenkosten; rules.tax.afa.cost_rows_total, the sum the
      AfA basis adds to the price)
    + Σ the loan costs the household paid itself: the private_cost rows of class financing
      (phase 4b D134) in force, of every tax year, each amount × its share (rules.tax._common.
      shared, as the Anlage V draft takes it)
    − Σ the principal of the property's loans

Each private_cost row counts once: a superseded or withdrawn row (a correction, a move to the
year paid) is not in force. A row whose bill a confirmed allocation of the rental account
paid after all is left out, as the draft leaves it out (rules.tax._common.paid_bills, phase 2
D135): that payment is a loan_fees allocation, which phase 4's yearly cash flow counts as
cash paid out (rules.performance.year_figures.RUNNING), so the cash back already holds it.
Loan costs booked on the rental account are therefore never added here: they are money out
of pocket that the cash back counts, and adding them here as well would count them twice.

It is phase 4's initial equity (rules.performance.sale, price + cost rows − loan principal)
plus the loan costs paid privately. Nothing here is stored or feeds rule_version().
"""

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.property import PropertiesPaidIn, PropertyPaidIn
from hausbuch.schemas.tax import AfaCostRow, AllocationFact, TaxInputFact, TaxInputKind

from .tax._common import in_force, paid_bills, shared
from .tax.afa import cost_rows_total
from .tax.rounding import cents

ZERO = Decimal("0.00")
FINANCING = "financing"


def own_loan_costs(
    tax_inputs: Iterable[TaxInputFact], allocations: Iterable[AllocationFact] = ()
) -> Decimal:
    """Σ the private_cost rows of class financing in force (all tax years), amount × share,
    without a row whose bill the rental account paid (`allocations`: the draft facts' own)."""
    paid_from_account = paid_bills(allocations)
    rows = [
        r
        for r in in_force(tax_inputs, TaxInputKind.PRIVATE_COST)
        if r.value.cost_class == FINANCING
        and r.value.amount is not None
        and (r.document_id is None or r.document_id not in paid_from_account)
    ]
    return cents(sum((shared(r.value.amount, r) for r in rows), ZERO))


def paid_in(
    *,
    property_id: UUID,
    label: str,
    purchase_price: Decimal | None,
    cost_rows: Sequence[AfaCostRow],
    tax_inputs: Sequence[TaxInputFact],
    allocations: Sequence[AllocationFact],
    loan_principals: Iterable[Decimal],
    ownership_ended_on: date | None,
    today: date,
) -> PropertyPaidIn:
    """One property's parts and its paid_in; paid_in None with a reason when the property has
    no purchase price, or its ownership ended on or before today (the net worth no longer
    counts it)."""
    acquisition = cost_rows_total(cost_rows)
    loan_costs = own_loan_costs(tax_inputs, allocations)
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
