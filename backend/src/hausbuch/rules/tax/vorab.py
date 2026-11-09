"""Income before the handover (phase 3 §5.10, D63; Q1 decided 9 Nov 2026: rent).

Income allocations of the tax year booked before the property's (current) purchase_date:
with a `pre_handover_income` input in force they are the flat's rent before the handover (one
property row, amount = pre_handover = their sum; the draft merges it into the flat's Z13 row);
without it they wait in the "Vor dem Übergang" block with a warning. Costs before the handover
are not touched: they stay in their lines (vorab entstandene Werbungskosten).
"""

from hausbuch.schemas.tax import (
    DraftFacts,
    RuleLines,
    RuleSet,
    SourceRef,
    TaxInputKind,
    WarningCode,
)

from ._common import INCOME_CATEGORIES, ZERO, in_force, input_sources, of_year, sources_of, warning
from .einnahmen import flat_row
from .rounding import money_str


def split(facts: DraftFacts, tax_year: int, rule_set: RuleSet) -> RuleLines:
    handover = facts.property.purchase_date
    before = [
        a
        for a in of_year(facts.allocations, tax_year, rule_set)
        if a.category in INCOME_CATEGORIES and a.booking_date < handover
    ]
    if not before:
        return RuleLines(tax_year=tax_year)
    total = sum((a.amount for a in before), ZERO)
    decided = in_force(facts.tax_inputs, TaxInputKind.PRE_HANDOVER_INCOME)
    if decided:
        refs = [r for a in before for r in sources_of(a)] + input_sources(decided[-1])
        row = flat_row(facts, total, refs, pre_handover=total)
        return RuleLines(tax_year=tax_year, unit_rows=[row])
    return RuleLines(
        tax_year=tax_year,
        pre_handover=[SourceRef(kind="allocation", id=a.allocation_id) for a in before],
        warnings=[
            warning(
                WarningCode.INCOME_PRE_HANDOVER,
                [SourceRef(kind="allocation", id=a.allocation_id) for a in before],
                amount=money_str(total),
                count=str(len(before)),
            )
        ],
    )
