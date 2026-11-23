"""The seller's renovation inside the purchase price (phase 3 D287, Q-O180). Pure.

The Kaufvertrag obliges the seller to renovate the flat after the handover, paid inside the
price. The household may claim that part of the price as its own renovation, openly: BFH
IX R 34/03 points the other way, so the Finanzamt may refuse it (Luis, 22 Nov 2026: „I just
want to apply the renovation as the 15% and if they decline then it's deducted as now“).

A `price_renovation` tax input in force with status `claimed` or `accepted` applies when its
work begins on or after the handover and its gross amount, with any movable asset bought with
the flat, stays below the building's share of the price:

- its gross amount leaves the building's share of the AfA basis, never the land's
  (afa.basis; so the 15 % limit falls with it);
- it counts in the 15 % test at its net, tested on its period's end (D76;
  anschaffungsnah.limit_status);
- within the limit it is Erhaltungsaufwand (wk.maintenance, gross) in the year the price was
  paid (§11 EStG); over the limit it returns to the basis as a limit_exceeded addition of that
  year, and nothing is booked here.

A claim in force that cannot apply raises tax.price_renovation_not_applied (reason
before_handover or exceeds_building) and changes nothing else. Once the Einkommensteuerbescheid
of the year paid is confirmed (DraftFacts.assessed_years), a booked claim still `claimed` raises
tax.price_renovation_ask, the to-do „Hat das Finanzamt die Renovierung im Kaufpreis anerkannt?“
whose „Nein“ records `declined` and whose „Ja“ records `accepted`. Declined, or no input: the
draft is exactly the draft without this module.
"""

from typing import Literal

from hausbuch.schemas.tax import (
    AfaFacts,
    DraftFacts,
    DraftWarning,
    LimitStatus,
    PriceRenovation,
    PriceRenovationFact,
    TaxInputFact,
    TaxInputKind,
    WarningCode,
)

from ._common import LineBook, in_force, input_sources, warning
from .afa import building_share, movable_deducted
from .rounding import money_str

NotApplied = Literal["before_handover", "exceeds_building"]


def current(facts: DraftFacts) -> TaxInputFact | None:
    """The price_renovation input in force (claimed, accepted or declined), if any."""
    rows = in_force(facts.tax_inputs, TaxInputKind.PRICE_RENOVATION)
    return rows[-1] if rows else None


def applied(
    facts: DraftFacts, afa: AfaFacts
) -> tuple[PriceRenovationFact | None, NotApplied | None]:
    """The claim as the AfA and the 15 % test see it, or why a counting claim cannot apply.
    (None, None): no input, or declined. `afa` is the property's AfA facts without the claim."""
    row = current(facts)
    if row is None:
        return None, None
    v = row.value
    assert isinstance(v, PriceRenovation)
    if not v.counts:
        return None, None
    if v.period_start < facts.property.purchase_date:
        return None, "before_handover"
    share = building_share(afa)
    if share is not None and movable_deducted(afa) + v.amount >= share:
        return None, "exceeds_building"
    return (
        PriceRenovationFact(
            tax_input_id=row.id,
            amount=v.amount,
            amount_net=v.amount_net,
            period_start=v.period_start,
            period_end=v.period_end,
            paid_on=v.paid_on,
            document_id=row.document_id,
        ),
        None,
    )


def booked(claim: PriceRenovationFact | None, limit: LimitStatus, tax_year: int) -> bool:
    """The claim is Erhaltungsaufwand of tax_year: paid that year and not moved into the basis
    by the 15 % crossing."""
    if claim is None or claim.paid_on.year != tax_year:
        return False
    return not any(
        b.reason == "limit_exceeded" and b.tax_input_id == claim.tax_input_id
        for b in limit.basis_additions
    )


def lines(
    facts: DraftFacts, afa: AfaFacts, tax_year: int, book: LineBook, limit: LimitStatus
) -> list[DraftWarning]:
    """Books a claim within the limit on wk.maintenance in the year paid and returns the
    warnings of the drafted year. `afa` is the property's AfA facts (afa_facts(facts)), whose
    price_renovation is the claim that applies."""
    row = current(facts)
    if row is None:
        return []
    v = row.value
    assert isinstance(v, PriceRenovation)
    refs = input_sources(row)
    claim = afa.price_renovation
    if claim is None:
        _, reason = applied(facts, afa)
        if reason is None:
            return []  # declined: nothing changes
        return [
            warning(
                WarningCode.TAX_PRICE_RENOVATION_NOT_APPLIED,
                refs,
                reason=reason,
                amount=money_str(v.amount),
            )
        ]
    if not booked(claim, limit, tax_year):
        return []
    book.add("wk.maintenance", claim.amount, refs)
    if v.status == "claimed" and tax_year in facts.assessed_years:
        return [
            warning(
                WarningCode.TAX_PRICE_RENOVATION_ASK,
                refs,
                amount=money_str(claim.amount),
                tax_year=str(tax_year),
            )
        ]
    return []
