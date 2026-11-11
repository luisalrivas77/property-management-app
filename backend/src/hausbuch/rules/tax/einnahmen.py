"""The income lines and the Umlagen (phase 3 §5.7, D63, D71, D72).

Income booked on or after the handover only; what came in before it is §5.10's (vorab.py).

- rent_cold → one Z13 row per Wohnung (D71): an ETW or EFH is one Wohnung (the property's
  row, its rooms listed under it), an MFH has one row per unit. income.rent_total is their sum.
- operating_costs_prepayment → income.umlagen, a Pauschale (flat_rate) included.
- operating_costs_settlement → income.umlagen_settlement, sign kept.
- other_income → income.other; every other_income allocation of the year, whatever its date,
  raises income.other_review.
- Z24 (umlagen_not_agreed) is ticked when every tenancy let in the year is `included`; mixed
  terms warn instead (D72).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.tax import (
    AllocationFact,
    DraftFacts,
    DraftWarning,
    RoomRent,
    RuleLines,
    RuleSet,
    SourceRef,
    TaxTenancy,
    UnitRow,
    WarningCode,
)

from ._common import ZERO, LineBook, flat_area, is_flat, of_year, sources_of, unique, warning
from .rounding import money_str

ROUTES = {
    C.OPERATING_COSTS_PREPAYMENT: "income.umlagen",
    C.OPERATING_COSTS_SETTLEMENT: "income.umlagen_settlement",
    C.OTHER_INCOME: "income.other",
}


def unit_of(allocation: AllocationFact, facts: DraftFacts) -> UUID | None:
    """The unit a rent line belongs to: its own unit, else its tenancy's."""
    if allocation.unit_id is not None:
        return allocation.unit_id
    for t in facts.tenancies:
        if t.id == allocation.tenancy_id:
            return t.unit_id
    return None


def let_in(tenancy: TaxTenancy, tax_year: int) -> bool:
    return tenancy.start_date <= date(tax_year, 12, 31) and (
        tenancy.end_date is None or tenancy.end_date >= date(tax_year, 1, 1)
    )


def flat_row(facts: DraftFacts, amount: Decimal, sources: list[SourceRef], **extra) -> UnitRow:
    """The one row of an ETW or EFH (D71), or of an MFH line without a unit. No Wohnfläche
    and no units: no area (None, never 0); area_source units raises the to-do."""
    area, source = flat_area(facts)
    return UnitRow(
        wohnung="property",
        id=facts.property.id,
        label=facts.property.label,
        area_m2=area if area > 0 else None,
        area_source=source,
        amount=amount,
        sources=unique(sources),
        **extra,
    )


def _rent_rows(facts: DraftFacts, rent: list[AllocationFact]) -> list[UnitRow]:
    if not rent:
        return []
    by_unit: dict[UUID | None, list[AllocationFact]] = {}
    for a in rent:
        by_unit.setdefault(unit_of(a, facts), []).append(a)

    def total(rows: list[AllocationFact]) -> Decimal:
        return sum((a.amount for a in rows), ZERO)

    def refs(rows: list[AllocationFact]) -> list[SourceRef]:
        return [r for a in rows for r in sources_of(a)]

    if is_flat(facts.property):
        rooms = [
            RoomRent(unit_id=u.id, label=u.label, amount=total(by_unit[u.id]))
            for u in facts.units
            if u.id in by_unit
        ]
        return [flat_row(facts, total(rent), refs(rent), rooms=rooms)]

    rows = [
        UnitRow(
            wohnung="unit",
            id=u.id,
            label=u.label,
            area_m2=u.floor_area_m2,
            area_source="units",
            amount=total(by_unit[u.id]),
            sources=unique(refs(by_unit[u.id])),
        )
        for u in facts.units
        if u.id in by_unit
    ]
    known = {u.id for u in facts.units}
    stray = [a for key, rows_ in by_unit.items() if key not in known for a in rows_]
    if stray:  # an MFH rent line the matcher tied to no unit: the property's row
        rows.append(flat_row(facts, total(stray), refs(stray)))
    return rows


def _terms(facts: DraftFacts, tax_year: int, book: LineBook) -> list[DraftWarning]:
    let = [t for t in facts.tenancies if let_in(t, tax_year)]
    included = sorted(
        str(t.id) for t in let if t.operating_costs_terms is OperatingCostsTerms.INCLUDED
    )
    if not let or not included:
        return []
    if len(included) == len(let):
        book.add("income.umlagen_not_agreed", None, ticked=True)
        return []
    return [
        warning(
            WarningCode.UMLAGEN_MIXED_TERMS,
            [SourceRef(kind="property", id=facts.property.id)],
            tenancies=",".join(included),
        )
    ]


def lines(facts: DraftFacts, tax_year: int, rule_set: RuleSet) -> RuleLines:
    year = of_year(facts.allocations, tax_year, rule_set)
    handover = facts.property.purchase_date
    after = [a for a in year if a.booking_date >= handover]
    warnings: list[DraftWarning] = []

    book = LineBook()
    rent = [a for a in after if a.category is C.RENT_COLD]
    rows = _rent_rows(facts, rent)
    if rent:
        book.add(
            "income.rent_total",
            sum((a.amount for a in rent), ZERO),
            [r for a in rent for r in sources_of(a)],
        )
    for a in after:
        if a.category in ROUTES:
            book.add(ROUTES[a.category], a.amount, sources_of(a))
    warnings += _terms(facts, tax_year, book)

    if any(r.wohnung == "property" and r.area_source == "units" for r in rows):
        warnings.append(
            warning(
                WarningCode.INCOME_LIVING_AREA_MISSING,
                [SourceRef(kind="property", id=facts.property.id)],
            )
        )
    for a in year:  # whatever its date (§5.7)
        if a.category is C.OTHER_INCOME:
            params = {"amount": money_str(a.amount)}
            if a.counterparty_iban_masked is not None:
                params["counterparty_iban"] = a.counterparty_iban_masked
            warnings.append(
                warning(
                    WarningCode.INCOME_OTHER_REVIEW,
                    [SourceRef(kind="allocation", id=a.allocation_id)],
                    **params,
                )
            )
    return RuleLines(tax_year=tax_year, lines=book.lines(), unit_rows=rows, warnings=warnings)
