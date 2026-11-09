"""AfA linear (phase 3 §5.2, D55, D73, D79). Pure: AfaFacts in, one AfaLine per tax year out.

The basis comes from the property record only: purchase price plus the acquisition cost rows,
times the selected split's building share (the Nebenkosten split by the same share). The
`acquisition_cost` and `purchase_price` allocations are evidence and never summed (D55): a
cost booked with no row warns, a price paid in a different total warns, neither changes the
figure. Basis additions (Herstellungskosten, the 15 % crossing) are gross and accrue from
1 January of the year they were paid (R 7.4 Abs. 9 EStR).

A shorter actual useful life (§7 Abs. 4 S. 2 EStG, D79): from the first year of a useful_life
input in force, the AfA is the book value at the start of the year (basis + additions so far
− the AfA of earlier years) spread over the remaining months of that life (R 7.4 Abs. 9 EStR).
The life's months are counted from the AfA start month when it applies from the purchase year,
else from January of its first year; a later input restarts the count. Years before the first
input keep the statutory amount; without any input the line is exactly the statutory one.
"""

from decimal import Decimal

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.tax import (
    AfaFacts,
    AfaLine,
    DraftWarning,
    RuleSet,
    SourceRef,
    UsefulLifeFact,
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


def _additions_through(facts: AfaFacts, tax_year: int) -> Decimal:
    return cents(sum((a.amount for a in facts.additions if a.tax_year <= tax_year), ZERO))


def _statutory(base: Decimal, facts: AfaFacts, tax_year: int, rate: Decimal) -> Decimal:
    months = owned_months(facts.purchase_date, facts.ownership_ended_on, tax_year)
    return cents((base + _additions_through(facts, tax_year)) * rate * months / 12)


def governing(facts: AfaFacts, tax_year: int) -> UsefulLifeFact | None:
    """The useful_life input that governs `tax_year`: the one with the greatest from_year at or
    before it (the service keeps one per year; with two, the later in the list wins)."""
    best: UsefulLifeFact | None = None
    for row in facts.useful_lives:
        if row.from_year <= tax_year and (best is None or row.from_year >= best.from_year):
            best = row
    return best


def _remaining_value(
    base: Decimal, facts: AfaFacts, tax_year: int, rate: Decimal
) -> tuple[Decimal, Decimal, int]:
    """(book value at the start of `tax_year`, AfA of `tax_year`, months of the useful life
    remaining at its start), walking the years from the
    purchase year. Statutory until the first governing row; from each governing row's first
    year, the remaining life restarts at years × 12 months."""
    book = base
    remaining = 0
    current: UsefulLifeFact | None = None
    added = {
        year: sum((a.amount for a in facts.additions if a.tax_year == year), ZERO)
        for year in {a.tax_year for a in facts.additions}
    }
    # additions dated before the purchase year count from its start (as the statutory sum)
    book += sum((v for y, v in added.items() if y < facts.purchase_date.year), ZERO)
    amount = ZERO
    start = 0
    for year in range(facts.purchase_date.year, tax_year + 1):
        book += added.get(year, ZERO)
        months = owned_months(facts.purchase_date, facts.ownership_ended_on, year)
        row = governing(facts, year)
        if row is None:
            amount = min(_statutory(base, facts, year, rate), book)
        else:
            if row is not current:
                current, remaining = row, row.years * 12
            start = remaining
            if months == 0:
                amount = ZERO
            elif months >= remaining:  # the last months take the rest (0 once written off)
                amount = book
            else:
                amount = cents(book * months / remaining)
            remaining = max(remaining - months, 0)
        amount = max(amount, ZERO)
        if year == tax_year:
            return book, amount, start
        book -= amount
    raise AssertionError("tax_year is not before the purchase year here")


def afa(facts: AfaFacts, tax_year: int, rule_set: RuleSet) -> AfaLine:
    months = owned_months(facts.purchase_date, facts.ownership_ended_on, tax_year)
    base = basis(facts)
    additions = _additions_through(facts, tax_year)
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
    row = governing(facts, tax_year) if tax_year >= facts.purchase_date.year else None
    changed = (
        tax_year == facts.purchase_date.year
        or any(a.tax_year == tax_year for a in facts.additions)
        or (row is not None and row.from_year == tax_year)
    )
    method_code = (2 if changed else 1) if months else None
    if row is None:
        return AfaLine(
            tax_year=tax_year,
            amount=cents((base + additions) * rate * months / 12),
            basis=base,
            additions=additions,
            rate=rate,
            months=months,
            method_code=method_code,
            sources=sources,
            warnings=warnings,
        )
    book, amount, remaining = _remaining_value(base, facts, tax_year, rate)
    refs = [SourceRef(kind="tax_input", id=row.tax_input_id)]
    if row.document_id is not None:
        refs.append(SourceRef(kind="document", id=row.document_id))
    report = warning(
        WarningCode.AFA_USEFUL_LIFE_REPORT,
        refs,
        years=str(row.years),
        from_year=str(row.from_year),
    )
    return AfaLine(
        tax_year=tax_year,
        amount=amount,
        basis=base,
        additions=additions,
        rate=None,
        months=months,
        method_code=method_code,
        method="useful_life",
        useful_life_years=row.years,
        useful_life_from_year=row.from_year,
        book_value_start=book,
        remaining_months=remaining,
        sources=unique(sources + refs),
        warnings=[*warnings, report],
    )
