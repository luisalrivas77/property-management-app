"""Schuldzinsen, Geldbeschaffungskosten and the Zinsbescheinigung (phase 3 §5.5, D63, D66).

wk.interest is the ledger's loan_interest of the tax year (after §5.1), wk.financing_costs its
loan_fees (plus a privately paid financing cost, added by draft.py, phase 4b D134); both count
before the handover too (vorab entstandene Werbungskosten). Tilgung is
tracked, never deducted. A confirmed Zinsbescheinigung is compared with the booked interest of
its loan and year, never summed: the ledger stays the source of the figure.
"""

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.tax import (
    DraftFacts,
    RuleLines,
    RuleSet,
    SourceRef,
    TaxInputKind,
    WarningCode,
)

from ._common import (
    ZERO,
    LineBook,
    in_force,
    input_sources,
    of_year,
    sources_of,
    tracked_amount,
    warning,
)
from .rounding import money_str


def lines(facts: DraftFacts, tax_year: int, rule_set: RuleSet) -> RuleLines:
    allocations = of_year(facts.allocations, tax_year, rule_set)
    interest = [a for a in allocations if a.category is C.LOAN_INTEREST]
    fees = [a for a in allocations if a.category is C.LOAN_FEES]
    principal = [a for a in allocations if a.category is C.LOAN_PRINCIPAL]

    lenders = {loan.id: loan.lender for loan in facts.loans}
    book = LineBook()
    for a in interest:
        names = [lenders[a.loan_id]] if a.loan_id in lenders else []
        book.add("wk.interest", -a.amount, sources_of(a), einzelangaben=names)
    for a in fees:
        book.add("wk.financing_costs", -a.amount, sources_of(a))

    warnings = []
    certificates = [
        r
        for r in in_force(facts.tax_inputs, TaxInputKind.INTEREST_CERTIFIED)
        if r.tax_year == tax_year
    ]
    for cert in certificates:
        mine = [a for a in interest if a.loan_id == cert.loan_id]
        booked = -sum((a.amount for a in mine), ZERO)
        certified = cert.value.amount
        if booked != certified:
            refs = input_sources(cert) + [
                SourceRef(kind="allocation", id=a.allocation_id) for a in mine
            ]
            warnings.append(
                warning(
                    WarningCode.INTEREST_CERTIFICATE_MISMATCH,
                    refs,
                    certified=money_str(certified),
                    booked=money_str(booked),
                    difference=money_str(abs(certified - booked)),
                )
            )

    tracked = tracked_amount(C.LOAN_PRINCIPAL, principal)
    return RuleLines(
        tax_year=tax_year,
        lines=book.lines(),
        tracked=[tracked] if tracked else [],
        warnings=warnings,
    )
