"""Verbilligte Vermietung (phase 3 §5.8, D61, D71, D72; §21 Abs. 2 EStG, R 21.3 EStR).

Per Wohnung (the flat of an ETW or EFH, each unit of an MFH), in monthly figures: the agreed
Warmmiete against the ortsübliche Warmmiete, which is the market_rent input's cold rent per m²
× the Wohnung's area plus the year's umlagefähige costs from the ledger ÷ months owned.
≥ 66 % full deduction; 50 % to under 66 % needs a Totalüberschussprognose; under 50 % the
Finanzamt cuts the costs by (1 − ratio), proposed for Z87. The draft never cuts (D61).

Two readings this module makes where the spec is silent (both reported with the step 1 PR):
- An MFH's umlagefähige costs are shared among its units by floor area.
- A Wohnung's agreed rent sums, per unit, the tenancy in force on 1 January, else the first
  tenancy that starts in the year (so a room re-let mid-year is not counted twice).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.tax import (
    DraftFacts,
    DraftWarning,
    ReducedRentCheck,
    ReducedRentResult,
    RuleSet,
    SourceRef,
    TaxInputFact,
    TaxInputKind,
    TaxTenancy,
    WarningCode,
)

from ._common import (
    ZERO,
    flat_area,
    in_force,
    input_sources,
    is_flat,
    of_year,
    owned_months,
    unique,
    warning,
)
from .einnahmen import let_in
from .rounding import cents, one_decimal, whole_half_up

UMLAGEFAEHIG = frozenset({C.HAUSGELD_UMLAGEFAEHIG, C.GRUNDSTEUER, C.INSURANCE})


def _agreed(tenancies: list[TaxTenancy], tax_year: int) -> Decimal:
    """Σ per unit of the warm rent in force on 1 January or the tenancy's later start."""
    jan_1 = date(tax_year, 1, 1)
    first: dict[UUID, TaxTenancy] = {}
    for t in sorted(tenancies, key=lambda t: (t.start_date, str(t.id))):
        if let_in(t, tax_year):
            current = first.get(t.unit_id)
            # The tenancy in force on 1 January wins over a later one on the same unit.
            if current is None or (t.start_date <= jan_1 and current.start_date <= t.start_date):
                first[t.unit_id] = t
    total = ZERO
    for t in first.values():
        day = max(jan_1, t.start_date)
        rows = sorted(t.rents, key=lambda r: r.effective_from)
        in_force_rows = [r for r in rows if r.effective_from <= day] or rows[:1]
        if in_force_rows:
            row = in_force_rows[-1]
            total += row.cold_rent + row.operating_costs_prepayment
    return total


def market_rent(facts: DraftFacts, unit_id: UUID | None, tax_year: int) -> TaxInputFact | None:
    """The market_rent input in force for the Wohnung with valid_from ≤ 31 Dec (§8's test)."""
    rows = [
        r
        for r in in_force(facts.tax_inputs, TaxInputKind.MARKET_RENT)
        if r.unit_id == unit_id and r.value.valid_from <= date(tax_year, 12, 31)
    ]
    return max(rows, key=lambda r: r.value.valid_from, default=None)


def check(facts: DraftFacts, tax_year: int, rule_set: RuleSet) -> ReducedRentResult:
    prop = facts.property
    months = owned_months(prop.purchase_date, prop.ownership_ended_on, tax_year)
    costs = [
        a for a in of_year(facts.allocations, tax_year, rule_set) if a.category in UMLAGEFAEHIG
    ]
    yearly = -sum((a.amount for a in costs), ZERO)
    cost_refs = [SourceRef(kind="allocation", id=a.allocation_id) for a in costs]
    let = [t for t in facts.tenancies if let_in(t, tax_year)]

    # (wohnung, id, area, share of the umlagefähige costs, its tenancies, market_rent unit)
    wohnungen = []
    if is_flat(prop):
        if let:
            area, _ = flat_area(facts)
            wohnungen.append(("property", prop.id, area, Decimal(1), let, None))
    else:
        total_area = sum((u.floor_area_m2 for u in facts.units), ZERO)
        for u in facts.units:
            mine = [t for t in let if t.unit_id == u.id]
            if mine:
                share = u.floor_area_m2 / total_area if total_area else Decimal(0)
                wohnungen.append(("unit", u.id, u.floor_area_m2, share, mine, u.id))

    checks: list[ReducedRentCheck] = []
    warnings: list[DraftWarning] = []
    for kind, wid, area, share, tenancies, unit_id in wohnungen:
        agreed = _agreed(tenancies, tax_year)
        ref = [SourceRef(kind="property", id=prop.id)]
        row = market_rent(facts, unit_id, tax_year)
        if row is None:
            checks.append(
                ReducedRentCheck(
                    wohnung=kind,
                    id=wid,
                    agreed_warm=agreed,
                    area_m2=area,
                    outcome="no_market_rent",
                    sources=ref,
                )
            )
            warnings.append(warning(WarningCode.REDUCED_RENT_NO_MARKET_RENT, ref))
            continue
        per_m2 = row.value.cold_rent_per_m2
        monthly = cents(yearly * share / months) if months else Decimal("0.00")
        market = cents(per_m2 * area) + monthly
        ratio = agreed / market
        sources = unique(ref + input_sources(row) + cost_refs)
        cut = None
        if ratio >= rule_set.reduced_rent_full:
            outcome = "full"
        elif ratio >= rule_set.reduced_rent_split:
            outcome = "prognosis_needed"
            warnings.append(warning(WarningCode.REDUCED_RENT_PROGNOSIS_NEEDED, sources))
        else:
            outcome = "partial"
            cut = whole_half_up((1 - ratio) * 100)
            warnings.append(
                warning(WarningCode.REDUCED_RENT_PARTIAL, sources, cut_percent=str(cut))
            )
        checks.append(
            ReducedRentCheck(
                wohnung=kind,
                id=wid,
                agreed_warm=agreed,
                area_m2=area,
                market_cold_per_m2=per_m2,
                umlagefaehig_monthly=monthly,
                market_warm=market,
                ratio_percent=one_decimal(ratio * 100),
                outcome=outcome,
                cut_percent=cut,
                sources=sources,
            )
        )
    return ReducedRentResult(tax_year=tax_year, checks=checks, warnings=warnings)
