"""Helpers the phase 3 tax rules share (docs/phases/phase-3.md §5). Pure, no I/O."""

import calendar
from collections.abc import Iterable, Iterator
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import PropertyKind
from hausbuch.schemas.tax import (
    AfaFacts,
    AllocationFact,
    DraftFacts,
    DraftWarning,
    LineAmount,
    RuleSet,
    SourceRef,
    TaxInputFact,
    TaxInputKind,
    TaxProperty,
    TaxYearResult,
    TrackedAmount,
    UsefulLifeFact,
    WarningCode,
)

from .tax_year import tax_year_of

ZERO = Decimal("0")
INCOME_CATEGORIES = frozenset(
    {
        AllocationCategory.RENT_COLD,
        AllocationCategory.OPERATING_COSTS_PREPAYMENT,
        AllocationCategory.OPERATING_COSTS_SETTLEMENT,
        AllocationCategory.OTHER_INCOME,
    }
)


def nonzero(allocations: Iterable[AllocationFact]) -> list[AllocationFact]:
    """D70: a 0.00 allocation is not evidence; every rule ignores it."""
    return [a for a in allocations if a.amount != 0]


def with_years(
    allocations: Iterable[AllocationFact], rule_set: RuleSet
) -> Iterator[tuple[AllocationFact, TaxYearResult]]:
    """Each non-zero allocation with the tax year §5.1 gives it."""
    for a in nonzero(allocations):
        yield a, tax_year_of(a, rule_set)


def of_year(
    allocations: Iterable[AllocationFact], tax_year: int, rule_set: RuleSet
) -> list[AllocationFact]:
    """The non-zero allocations §5.1 counts in `tax_year`."""
    return [a for a, r in with_years(allocations, rule_set) if r.year == tax_year]


def year_of(allocation: AllocationFact, rule_set: RuleSet) -> int:
    return tax_year_of(allocation, rule_set).year


def sources_of(allocation: AllocationFact) -> list[SourceRef]:
    """An allocation and its Beleg (principle 2)."""
    refs = [SourceRef(kind="allocation", id=allocation.allocation_id)]
    if allocation.document_id is not None:
        refs.append(SourceRef(kind="document", id=allocation.document_id))
    return refs


def input_sources(row: TaxInputFact) -> list[SourceRef]:
    refs = [SourceRef(kind="tax_input", id=row.id)]
    if row.document_id is not None:
        refs.append(SourceRef(kind="document", id=row.document_id))
    return refs


def unique(refs: Iterable[SourceRef]) -> list[SourceRef]:
    """Sources without repeats, first occurrence kept."""
    seen: set[tuple[str, UUID | None]] = set()
    out = []
    for ref in refs:
        key = (ref.kind, ref.id)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def in_force(rows: Iterable[TaxInputFact], kind: TaxInputKind | None = None) -> list[TaxInputFact]:
    """The rows in force: not withdrawn and not superseded by another row (D58)."""
    rows = list(rows)
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [
        r
        for r in rows
        if not r.withdrawn and r.id not in superseded and (kind is None or r.kind is kind)
    ]


def by_allocation(rows: Iterable[TaxInputFact], kind: TaxInputKind) -> dict[UUID, TaxInputFact]:
    """The input in force per allocation for an allocation-scoped kind (the last one wins)."""
    return {r.allocation_id: r for r in in_force(rows, kind) if r.allocation_id is not None}


def add_years(day: date, years: int) -> date:
    """The same calendar day `years` later; a day the month lacks (29 Feb) → its last day."""
    year = day.year + years
    last = calendar.monthrange(year, day.month)[1]
    return date(year, day.month, min(day.day, last))


def owned_in(prop: TaxProperty, tax_year: int) -> bool:
    """Owned on at least one day of the year (from the handover)."""
    ended = prop.ownership_ended_on
    return prop.purchase_date.year <= tax_year and (ended is None or ended.year >= tax_year)


def owned_months(purchase_date: date, ended: date | None, tax_year: int) -> int:
    """Months owned in the year: the handover month counts in full, as does the month
    ownership ends (§7 Abs. 1 S. 4 EStG; coverage, §5.11)."""
    if purchase_date.year > tax_year or (ended is not None and ended.year < tax_year):
        return 0
    first = purchase_date.month if purchase_date.year == tax_year else 1
    last = ended.month if ended is not None and ended.year == tax_year else 12
    return max(last - first + 1, 0)


def is_flat(prop: TaxProperty) -> bool:
    """ETW and EFH are one Wohnung; an MFH has one per unit (D71)."""
    return prop.kind is not PropertyKind.MFH


def flat_area(facts: DraftFacts) -> tuple[Decimal, str]:
    """The flat's area for Z11/Z13: living_area_m2, else Σ the units' m² (D71)."""
    if facts.property.living_area_m2 is not None:
        return facts.property.living_area_m2, "living_area"
    return sum((u.floor_area_m2 for u in facts.units), ZERO), "units"


def cost_amount(allocations: Iterable[AllocationFact]) -> Decimal:
    """A cost line's amount: the negated signed sum (costs are booked negative)."""
    return -sum((a.amount for a in allocations), ZERO)


def cost_line(key: str, allocations: list[AllocationFact], **extra) -> LineAmount | None:
    if not allocations:
        return None
    refs = unique(r for a in allocations for r in sources_of(a))
    return LineAmount(key=key, amount=cost_amount(allocations), sources=refs, **extra)


def warning(code: WarningCode, refs: list[SourceRef] | None = None, **params: str) -> DraftWarning:
    return DraftWarning.model_validate(
        {"code": code, "params": params, "refs": [r.model_dump() for r in refs or []]}
    )


def tracked_amount(category, allocations: list[AllocationFact]) -> TrackedAmount | None:
    """Booked, not deductible (Tilgung, Rücklage): the magnitude of the signed sum."""
    if not allocations:
        return None
    return TrackedAmount(
        category=category,
        amount=abs(sum((a.amount for a in allocations), ZERO)),
        count=len(allocations),
        sources=unique(r for a in allocations for r in sources_of(a)),
    )


class LineBook:
    """Collects line contributions by key: amounts summed, sources united (first occurrence
    kept), the provisional flag OR-ed, Einzelangaben united and sorted, a flag line ticked when
    any contribution ticks it. The draft merges the rules' lines the same way."""

    def __init__(self) -> None:
        self._lines: dict[str, dict] = {}

    def add(
        self,
        key: str,
        amount: Decimal | None,
        sources: Iterable[SourceRef] = (),
        *,
        provisional: bool = False,
        einzelangaben: Iterable[str] = (),
        ticked: bool | None = None,
    ) -> None:
        line = self._lines.setdefault(
            key,
            {"amount": None, "sources": [], "provisional": False, "names": set(), "ticked": None},
        )
        if amount is not None:
            line["amount"] = (line["amount"] or ZERO) + amount
        line["sources"].extend(sources)
        line["provisional"] = line["provisional"] or provisional
        line["names"].update(einzelangaben)
        if ticked is not None:
            line["ticked"] = bool(line["ticked"]) or ticked

    def merge(self, lines: Iterable[LineAmount]) -> None:
        for line in lines:
            self.add(
                line.key,
                line.amount,
                line.sources,
                provisional=line.provisional,
                einzelangaben=line.einzelangaben,
                ticked=line.ticked,
            )

    def __contains__(self, key: str) -> bool:
        return key in self._lines

    def amount(self, key: str) -> Decimal:
        line = self._lines.get(key)
        return ZERO if line is None or line["amount"] is None else line["amount"]

    def lines(self) -> list[LineAmount]:
        out = []
        for key, v in self._lines.items():
            amount = v["amount"]
            if amount is None and v["ticked"] is None:
                amount = Decimal("0.00")  # a provisional line with nothing on it yet
            out.append(
                LineAmount(
                    key=key,
                    amount=amount,
                    ticked=v["ticked"],
                    sources=unique(v["sources"]),
                    provisional=v["provisional"],
                    einzelangaben=sorted(v["names"]),
                )
            )
        return out


def afa_facts(facts: DraftFacts, additions=()) -> AfaFacts:
    """§5.2's facts from the draft's (the AfA basis from the record only, D55)."""
    prop = facts.property
    evidence = {AllocationCategory.ACQUISITION_COST, AllocationCategory.PURCHASE_PRICE}
    return AfaFacts(
        property_id=prop.id,
        purchase_price=prop.purchase_price,
        build_year=prop.build_year,
        purchase_date=prop.purchase_date,
        ownership_ended_on=prop.ownership_ended_on,
        cost_rows=facts.cost_rows,
        split=facts.split,
        additions=list(additions),
        allocations=[a for a in facts.allocations if a.category in evidence],
        useful_lives=[
            UsefulLifeFact(
                tax_input_id=row.id,
                from_year=row.tax_year,
                years=row.value.years,  # type: ignore[union-attr]
                document_id=row.document_id,
            )
            for row in in_force(facts.tax_inputs, TaxInputKind.USEFUL_LIFE)
            if row.tax_year is not None
        ],
    )
