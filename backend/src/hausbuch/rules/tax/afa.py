"""AfA linear (phase 3 §5.2, D55, D73). Pure: AfaFacts in, one AfaLine per tax year out.

The basis comes from the property record only: purchase price plus the acquisition cost rows,
times the selected split's building share (the Nebenkosten split by the same share). The
`acquisition_cost` and `purchase_price` allocations are evidence and never summed (D55): a
cost booked with no row warns, a price paid in a different total warns, neither changes the
figure. Basis additions (Herstellungskosten, the 15 % crossing) are gross and accrue from
1 January of the year they were paid (R 7.4 Abs. 9 EStR).
"""

from decimal import Decimal

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.tax import (
    AfaFacts,
    AfaLine,
    DraftWarning,
    RuleSet,
    SourceRef,
    WarningCode,
)

from ._common import ZERO, nonzero, owned_months, unique, warning
from .rounding import cents, money_str


def basis(facts: AfaFacts) -> Decimal | None:
    """(price + Σ cost rows) × building share, to the cent; None without a split."""
    if facts.split is None:
        return None
    total = facts.purchase_price + sum((r.amount for r in facts.cost_rows), ZERO)
    return cents(total * facts.split.building_share)


def _sources(facts: AfaFacts, tax_year: int) -> list[SourceRef]:
    refs = [SourceRef(kind="property", id=facts.property_id)]
    if facts.split is not None:
        refs.append(SourceRef(kind="price_split", id=facts.split.id))
        if facts.split.evidence_document_id is not None:
            refs.append(SourceRef(kind="document", id=facts.split.evidence_document_id))
    refs += [SourceRef(kind="acquisition_cost", id=r.id) for r in facts.cost_rows]
    refs += [
        SourceRef(kind="document", id=r.document_id)
        for r in facts.cost_rows
        if r.document_id is not None
    ]
    refs += [
        SourceRef(kind="allocation", id=a.allocation_id)
        for a in facts.additions
        if a.tax_year <= tax_year
    ]
    return unique(refs)


def _warnings(facts: AfaFacts) -> list[DraftWarning]:
    """The record's evidence checks; they concern the basis, so every year carries them."""
    out: list[DraftWarning] = []
    if facts.split is None:
        out.append(
            warning(WarningCode.AFA_NO_SPLIT, [SourceRef(kind="property", id=facts.property_id)])
        )
    for row in facts.cost_rows:
        if not row.paid_by:
            out.append(
                warning(
                    WarningCode.AFA_COST_ROW_UNPAID,
                    [SourceRef(kind="acquisition_cost", id=row.id)],
                    kind=row.kind.value,
                    amount=money_str(row.amount),
                )
            )
    paired = {a for row in facts.cost_rows for a in row.paid_by}
    evidence = nonzero(facts.allocations)  # D70: a 0.00 line is not evidence
    for a in evidence:
        if a.category is AllocationCategory.ACQUISITION_COST and a.allocation_id not in paired:
            out.append(
                warning(
                    WarningCode.AFA_ACQUISITION_COST_WITHOUT_ROW,
                    [SourceRef(kind="allocation", id=a.allocation_id)],
                    amount=money_str(abs(a.amount)),
                )
            )
    price_lines = [a for a in evidence if a.category is AllocationCategory.PURCHASE_PRICE]
    if price_lines:
        paid = sum((abs(a.amount) for a in price_lines), ZERO)
        if paid != facts.purchase_price:
            out.append(
                warning(
                    WarningCode.AFA_PURCHASE_PRICE_MISMATCH,
                    [SourceRef(kind="allocation", id=a.allocation_id) for a in price_lines],
                    price=money_str(facts.purchase_price),
                    paid=money_str(paid),
                )
            )
    return out


def afa(facts: AfaFacts, tax_year: int, rule_set: RuleSet) -> AfaLine:
    months = owned_months(facts.purchase_date, facts.ownership_ended_on, tax_year)
    base = basis(facts)
    additions = cents(sum((a.amount for a in facts.additions if a.tax_year <= tax_year), ZERO))
    sources = _sources(facts, tax_year)
    warnings = _warnings(facts)
    if base is None:
        return AfaLine(
            tax_year=tax_year,
            amount=None,
            basis=None,
            additions=additions,
            rate=None,
            months=months,
            method_code=None,
            sources=sources,
            warnings=warnings,
        )
    rate = rule_set.afa_rate(facts.build_year)
    amount = cents((base + additions) * rate * months / 12)
    changed = tax_year == facts.purchase_date.year or any(
        a.tax_year == tax_year for a in facts.additions
    )
    return AfaLine(
        tax_year=tax_year,
        amount=amount,
        basis=base,
        additions=additions,
        rate=rate,
        months=months,
        method_code=(2 if changed else 1) if months else None,
        sources=sources,
        warnings=warnings,
    )
