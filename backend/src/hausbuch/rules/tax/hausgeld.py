"""Hausgeld and the Erhaltungsrücklage (phase 3 §5.6, D57, BFH IX R 19/24).

The umlagefähige and Verwaltung shares are deductible in their tax year (§5.1). The share paid
into the Rücklage is tracked, not deducted: it becomes Erhaltungsaufwand only when the WEG
spends it, which the Hausgeldabrechnung of year Y states (a `ruecklage_spent` input for Y, on
wk.maintenance of Y with the Abrechnung as its source). Until that input exists for a year an
ETW was owned, the line is provisional and the Abrechnung pending; an input of 0.00 is an
answer (nothing spent), not a gap.
"""

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.enums import PropertyKind
from hausbuch.schemas.tax import (
    DraftFacts,
    RuleLines,
    RuleSet,
    SourceRef,
    TaxInputKind,
    WarningCode,
)

from ._common import (
    LineBook,
    in_force,
    input_sources,
    of_year,
    owned_in,
    sources_of,
    tracked_amount,
    warning,
)

ROUTES = {
    C.HAUSGELD_UMLAGEFAEHIG: "wk.allocable_costs",
    C.HAUSGELD_VERWALTUNG: "wk.non_allocable_costs",
}


def lines(facts: DraftFacts, tax_year: int, rule_set: RuleSet) -> RuleLines:
    allocations = of_year(facts.allocations, tax_year, rule_set)
    book = LineBook()
    for a in allocations:
        if a.category in ROUTES:
            book.add(ROUTES[a.category], -a.amount, sources_of(a))

    warnings = []
    spent = [
        r
        for r in in_force(facts.tax_inputs, TaxInputKind.RUECKLAGE_SPENT)
        if r.tax_year == tax_year
    ]
    if spent:
        row = spent[-1]
        if row.value.amount != 0:
            book.add("wk.maintenance", row.value.amount, input_sources(row))
    elif facts.property.kind is PropertyKind.ETW and owned_in(facts.property, tax_year):
        book.add("wk.maintenance", None, provisional=True)
        warnings.append(
            warning(
                WarningCode.HAUSGELD_ABRECHNUNG_PENDING,
                [SourceRef(kind="property", id=facts.property.id)],
                tax_year=str(tax_year),
            )
        )

    ruecklage = [a for a in allocations if a.category is C.HAUSGELD_RUECKLAGE]
    tracked = tracked_amount(C.HAUSGELD_RUECKLAGE, ruecklage)
    return RuleLines(
        tax_year=tax_year,
        lines=book.lines(),
        tracked=[tracked] if tracked else [],
        warnings=warnings,
    )
