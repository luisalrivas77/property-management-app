"""Assembling the Anlage V draft (phase 3 §5.11, D59, D60, D70). Pure: no DB, no model.

draft(facts, tax_year, rule_set) runs the rules of §5.1–5.10 over the property's confirmed
allocations and tax inputs and returns the draft with every line's sources:

1. The 15 % status first (§5.3), because a crossing moves costs from wk.maintenance into the
   AfA basis. The AfA additions are the herstellung elections (§5.4, all years) plus the
   limit_exceeded rows; each allocation is added once (D74).
2. AfA (§5.2) and the AfA on movable assets (D80, wk.afa_movable, from the same facts, so a
   kitchen bought with the flat leaves the building basis of both the AfA and the 15 % test),
   the line rules (§5.4–5.7, 5.10), then the categories no rule owns, routed by
   the rule set's category_lines (Grundsteuer, insurance, management and bank fees,
   professional fees), and the year's private_cost rows into wk.other (phase 4b §5.12, D111).
3. Lines are merged by key (amounts summed, sources united). income.rent_total is the sum of
   the merged Z13 rows (D71). Open categories, an undecided renovation and the pending lines
   of the year are excluded and counted (D59).
4. Whole euros (D60, rounding.py): income lines down, cost lines up, totals from the rounded
   lines, the owners' parts by largest remainder.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.checklist import (
    ChecklistAllocation,
    ChecklistFacts,
    ChecklistItem,
    ChecklistMarketRent,
)
from hausbuch.schemas.tax import (
    AllocationFact,
    AnlageVDraft,
    BasisAddition,
    Coverage,
    DraftFacts,
    DraftHeader,
    DraftLine,
    DraftTotals,
    DraftWarning,
    FormStatus,
    LimitFacts,
    MovableAfaLine,
    MovedPayment,
    OpenLines,
    OwnerResult,
    RuleLines,
    RuleSet,
    RuleSetStamp,
    SourceRef,
    TaxInputKind,
    TrackedAmount,
    UnitRow,
    WarningCode,
)

from ..document_checklist import build_checklist
from . import einnahmen, erhaltungsaufwand, hausgeld, schuldzinsen, vorab
from ._common import (
    ZERO,
    LineBook,
    afa_facts,
    flat_area,
    in_force,
    input_sources,
    owned_months,
    sources_of,
    unique,
    warning,
    with_years,
)
from .afa import afa
from .anschaffungsnah import CATEGORIES as LIMIT_CATEGORIES
from .anschaffungsnah import limit_status
from .movable import movable_afa
from .rounding import cents, money_str, split_cents, split_euros, whole_euros
from .spekulation import tax_free_from
from .tax_year import tax_year_of
from .verbilligt import check as reduced_rent_check

# Z32 = Z15 + Z20 + Z21 + Z31; Z83 = the abzugsfähig column (§4.2). Z57 (the spread's total)
# is information, its deductible part is on Z58–60.
INCOME_KEYS = ("income.rent_total", "income.umlagen", "income.umlagen_settlement", "income.other")
COST_KEYS = (
    "wk.afa",
    "wk.afa_movable",
    "wk.interest",
    "wk.financing_costs",
    "wk.maintenance",
    "wk.maintenance_spread_current",
    "wk.maintenance_spread_prior_1",
    "wk.maintenance_spread_prior_2",
    "wk.maintenance_spread_prior_3",
    "wk.maintenance_spread_prior_4",
    "wk.allocable_costs",
    "wk.non_allocable_costs",
    "wk.other",
)
OWNER_KEYS = ("result.owner_a", "result.owner_b")

# The categories a line rule owns; the rest of the mapped ones are routed generically.
RULE_CATEGORIES = frozenset(
    {
        C.RENT_COLD,
        C.OPERATING_COSTS_PREPAYMENT,
        C.OPERATING_COSTS_SETTLEMENT,
        C.OTHER_INCOME,
        C.LOAN_INTEREST,
        C.LOAN_FEES,
        C.LOAN_PRINCIPAL,
        C.REPAIRS,
        C.RENOVATION,
        C.HAUSGELD_UMLAGEFAEHIG,
        C.HAUSGELD_VERWALTUNG,
        C.HAUSGELD_RUECKLAGE,
    }
)


def _additions(facts: DraftFacts, rule_set: RuleSet, limit) -> list[BasisAddition]:
    """Herstellung elections first, then the 15 % crossing; each allocation once (D74)."""
    out: dict[UUID, BasisAddition] = {}
    for b in erhaltungsaufwand.herstellung_additions(facts, rule_set) + list(limit.basis_additions):
        out.setdefault(b.allocation_id, b)
    return list(out.values())


MOVABLE_LABELS = {"einbaukueche": "Einbauküche"}


def _euro_de(amount: Decimal) -> str:
    """12345.6 → "12.345,60 €" (Z42's Einzelangaben are typed as they read)."""
    return f"{cents(amount):,.2f} €".replace(",", "_").replace(".", ",").replace("_", ".")


def _movable_einzelangabe(line: MovableAfaLine, prop) -> str:
    """Z42 asks for Bezeichnung, Anschaffungskosten und -datum, Nutzungsdauer."""
    if line.in_purchase_price and line.from_year == prop.purchase_date.year:
        acquired = f"{prop.purchase_date.month:02d}/{prop.purchase_date.year}"
    else:
        acquired = str(line.from_year)
    return (
        f"{MOVABLE_LABELS.get(line.label, line.label)}: Anschaffungskosten "
        f"{_euro_de(line.basis)}, angeschafft {acquired}, Nutzungsdauer "
        f"{line.useful_life_years} Jahre"
    )


def _merge_rows(rows: list[UnitRow]) -> list[UnitRow]:
    """einnahmen's and vorab's rows of one Wohnung become one (D63: the part before the
    handover is listed under the flat's row)."""
    merged: dict[tuple[str, UUID], UnitRow] = {}
    for row in rows:
        key = (row.wohnung, row.id)
        if key not in merged:
            merged[key] = row
            continue
        a = merged[key]
        merged[key] = a.model_copy(
            update={
                "amount": a.amount + row.amount,
                "pre_handover": a.pre_handover + row.pre_handover,
                "rooms": a.rooms or row.rooms,
                "sources": unique(a.sources + row.sources),
            }
        )
    return list(merged.values())


def _generic(facts: DraftFacts, tax_year: int, rule_set: RuleSet, book: LineBook) -> None:
    mapping = rule_set.form.category_lines
    for a, r in with_years(facts.allocations, rule_set):
        if r.year != tax_year or a.category in RULE_CATEGORIES:
            continue
        key = mapping.get(a.category)
        if key is None:
            continue
        amount = a.amount if key.startswith("income.") else -a.amount
        book.add(key, amount, sources_of(a))


TRAVEL_FREQUENCY_TRIPS = 50  # phase 4b D111: above this, the flat may be a Tätigkeitsstätte


def _private_cost_line(value, rule_set: RuleSet) -> str:
    """A private cost's line: its category's (phase 2 D135, the rule set's category_lines,
    where a confirmed allocation of it would land), else wk.financing_costs for the class
    financing (D134), else wk.other."""
    if value.category is not None:
        key = rule_set.form.category_lines.get(value.category)
        if key is None or not key.startswith("wk."):
            raise ValueError(f"rule set {rule_set.tax_year} books {value.category} on no cost line")
        return key
    return "wk.financing_costs" if value.cost_class == "financing" else "wk.other"


def _private_costs(
    facts: DraftFacts, tax_year: int, rule_set: RuleSet, book: LineBook
) -> list[DraftWarning]:
    """Phase 4b §5.12, D111: rental costs paid from a private account, summed into wk.other,
    except the class financing (D134), which goes to wk.financing_costs beside the ledger's
    loan_fees (a privately paid Grundschuld notary bill is a Geldbeschaffungskosten).
    travel = trips × km_round_trip × travel_rate_per_km (§9 Abs. 1 S. 3 Nr. 4a S. 2 EStG with
    §5 Abs. 2 BRKG); every other class amount × share, to the cent. More than 50 trips in the
    year adds tax.travel_frequency (info): a near-daily presence can make the flat a
    regelmäßige Tätigkeitsstätte, where only the Entfernungspauschale applies.

    Phase 2 D135: a row whose document is the Beleg of a confirmed allocation (the bill was
    paid from the rental account after all) is not counted, so the bill is never deducted
    twice; tax.private_cost_paid_from_account (warning) names the row and the allocations."""
    warnings = []
    paid_from_account: dict[UUID, list[AllocationFact]] = {}
    for a in facts.allocations:
        if a.document_id is not None:
            paid_from_account.setdefault(a.document_id, []).append(a)
    rows = [
        r for r in in_force(facts.tax_inputs, TaxInputKind.PRIVATE_COST) if r.tax_year == tax_year
    ]
    trips = 0
    for row in rows:
        v = row.value
        paying = paid_from_account.get(row.document_id) if row.document_id else None
        if paying:
            warnings.append(
                warning(
                    WarningCode.TAX_PRIVATE_COST_PAID_FROM_ACCOUNT,
                    unique([*input_sources(row), *(r for a in paying for r in sources_of(a))]),
                    label=row.value.label,
                )
            )
            continue
        if v.cost_class == "travel":
            amount = cents(Decimal(v.trips) * v.km_round_trip * rule_set.travel_rate_per_km)
            trips += v.trips
        else:
            amount = cents(v.amount * v.share.numerator / v.share.denominator)
        if amount:
            book.add(_private_cost_line(v, rule_set), amount, input_sources(row))
    if trips > TRAVEL_FREQUENCY_TRIPS:
        warnings.append(
            warning(
                WarningCode.TAX_TRAVEL_FREQUENCY,
                [ref for r in rows if r.value.cost_class == "travel" for ref in input_sources(r)],
                trips=str(trips),
            )
        )
    return warnings


def _open(facts: DraftFacts, tax_year: int, rule_set: RuleSet, rules_open: OpenLines) -> OpenLines:
    open_cats = set(rule_set.form.open)
    rows = [
        a
        for a, r in with_years(facts.allocations, rule_set)
        if r.year == tax_year and a.category in open_cats
    ]
    return OpenLines(
        count=len(rows) + rules_open.count + facts.pending.count,
        sum=sum((a.amount for a in rows), ZERO) + rules_open.sum + facts.pending.sum,
    )


def _moved(facts: DraftFacts, tax_year: int, rule_set: RuleSet) -> list[MovedPayment]:
    out = []
    for a, r in with_years(facts.allocations, rule_set):
        if r.year != a.booking_date.year and tax_year in (r.year, a.booking_date.year):
            out.append(
                MovedPayment(
                    allocation_id=a.allocation_id,
                    booking_date=a.booking_date,
                    category=a.category,
                    amount=a.amount,
                    tax_year=r.year,
                    rule=r.rule,
                )
            )
    return out


def _coverage(facts: DraftFacts, tax_year: int) -> Coverage:
    prop = facts.property
    owned = owned_months(prop.purchase_date, prop.ownership_ended_on, tax_year)
    ends = [c.covered_through for c in facts.coverage]
    through = None if not ends or any(e is None for e in ends) else min(ends)
    covered = 0
    if through is not None and owned:
        first = prop.purchase_date.month if prop.purchase_date.year == tax_year else 1
        for month in range(first, first + owned):
            nxt = date(tax_year + (month == 12), month % 12 + 1, 1)
            last_day = date.fromordinal(nxt.toordinal() - 1)
            if last_day <= through:
                covered += 1
    return Coverage(months_owned=owned, months_covered=covered, covered_through=through)


def _missing_documents(facts: DraftFacts, rule_set: RuleSet, tax_year: int) -> list[ChecklistItem]:
    if facts.checklist is None or facts.today is None:
        return []
    allocations = [
        ChecklistAllocation(
            id=a.allocation_id,
            category=a.category,
            amount=a.amount,
            booking_date=a.booking_date,
            tax_year=tax_year_of(a, rule_set).year,
            document_id=a.document_id,
        )
        for a in facts.allocations
    ]
    market_rents = [
        ChecklistMarketRent(
            id=r.id,
            unit_id=r.unit_id,
            valid_from=r.value.valid_from,
            supersedes_id=r.supersedes_id,
            withdrawn=r.withdrawn,
        )
        for r in facts.tax_inputs
        if r.kind is TaxInputKind.MARKET_RENT
    ]
    checklist = ChecklistFacts.model_validate(
        facts.checklist.model_dump() | {"allocations": allocations, "market_rents": market_rents}
    )
    return build_checklist(checklist, tax_year, facts.today)


def _header(facts: DraftFacts) -> DraftHeader:
    prop = facts.property
    area, _ = flat_area(facts)
    return DraftHeader(
        address=prop.address,
        aktenzeichen=prop.grundsteuer_reference,
        contract_date=prop.notarized_on,
        transfer_date=prop.purchase_date,
        completed=prop.build_year,
        sold=prop.ownership_ended_on,
        living_area_m2=area if area > 0 else None,
    )


def _cut_line(reduced, book: LineBook) -> None:
    """Z87: the Kürzung the Finanzamt would make, proposed only below 50 % (D61). With several
    Wohnungen below it, the largest is proposed (no fixture yet)."""
    partial = [c for c in reduced.checks if c.cut_percent is not None]
    if partial:
        worst = max(partial, key=lambda c: c.cut_percent)
        book.add("reduced_rent.cut_percent", Decimal(worst.cut_percent), worst.sources)


def draft(facts: DraftFacts, tax_year: int, rule_set: RuleSet) -> AnlageVDraft:
    prop = facts.property
    form = rule_set.form
    warnings: list[DraftWarning] = []

    # 1. The 15 % status and the AfA it changes.
    limit = limit_status(
        LimitFacts(
            afa=afa_facts(facts),
            allocations=[a for a in facts.allocations if a.category in LIMIT_CATEGORIES],
            tax_inputs=facts.tax_inputs,
        ),
        tax_year,
        rule_set,
    )
    building = afa_facts(facts, _additions(facts, rule_set, limit))
    afa_line = afa(building, tax_year, rule_set)
    book = LineBook()
    if afa_line.amount:
        book.add("wk.afa", afa_line.amount, afa_line.sources)
    warnings += afa_line.warnings + limit.warnings
    movable = [m for m in movable_afa(building, tax_year) if m.months]
    for m in movable:  # D80: Z42–45, one Einzelangabe per asset
        if m.amount:
            book.add(
                "wk.afa_movable",
                m.amount,
                m.sources,
                einzelangaben=[_movable_einzelangabe(m, prop)],
            )
        warnings += m.warnings

    # 2. The line rules.
    results: list[RuleLines] = [
        einnahmen.lines(facts, tax_year, rule_set),
        vorab.split(facts, tax_year, rule_set),
        schuldzinsen.lines(facts, tax_year, rule_set),
        erhaltungsaufwand.lines(facts, tax_year, rule_set, limit=limit),
        hausgeld.lines(facts, tax_year, rule_set),
    ]
    rows: list[UnitRow] = []
    tracked: list[TrackedAmount] = []
    pre_handover: list[SourceRef] = []
    rules_open = OpenLines(count=0)
    for r in results:
        book.merge(line for line in r.lines if line.key != "income.rent_total")
        rows += r.unit_rows
        tracked += r.tracked
        pre_handover += r.pre_handover
        rules_open = OpenLines(
            count=rules_open.count + r.excluded_open.count, sum=rules_open.sum + r.excluded_open.sum
        )
        warnings += r.warnings
    _generic(facts, tax_year, rule_set, book)
    warnings += _private_costs(facts, tax_year, rule_set, book)

    rows = _merge_rows(rows)
    if rows:
        book.add(
            "income.rent_total",
            sum((row.amount for row in rows), ZERO),
            [s for row in rows for s in row.sources],
        )
    living_missing = [w for w in warnings if w.code is WarningCode.INCOME_LIVING_AREA_MISSING]
    if not living_missing and any(
        r.wohnung == "property" and r.area_source == "units" for r in rows
    ):
        warnings.append(
            warning(
                WarningCode.INCOME_LIVING_AREA_MISSING, [SourceRef(kind="property", id=prop.id)]
            )
        )

    reduced = reduced_rent_check(facts, tax_year, rule_set)
    warnings += reduced.warnings
    _cut_line(reduced, book)

    for a, r in with_years(facts.allocations, rule_set):
        if r.year == tax_year and "year_end_straddle" in r.notes:
            warnings.append(
                warning(
                    WarningCode.YEAR_END_STRADDLE,
                    [SourceRef(kind="allocation", id=a.allocation_id)],
                    booking_date=a.booking_date.isoformat(),
                    value_date=a.value_date.isoformat() if a.value_date else "",
                )
            )

    excluded = _open(facts, tax_year, rule_set, rules_open)
    if excluded.count > 0:
        warnings.append(
            warning(
                WarningCode.DRAFT_OPEN_LINES_EXCLUDED,
                count=str(excluded.count),
                sum=money_str(excluded.sum),
            )
        )
    if form.status is FormStatus.PROVISIONAL:
        warnings.append(
            warning(
                WarningCode.DRAFT_FORM_PROVISIONAL,
                [SourceRef(kind="rule")],
                form_year=str(form.form_year),
            )
        )

    # 3. Totals in cents and in whole euros (D60).
    rounded_rows = [
        whole_euros(row.amount, form.lines["income.rent_units"].rounding) for row in rows
    ]

    def rounded(key: str) -> int:
        if key == "income.rent_total":
            return sum(rounded_rows)
        return whole_euros(book.amount(key), form.lines[key].rounding)

    income = cents(sum((book.amount(k) for k in INCOME_KEYS), ZERO))
    costs = cents(sum((book.amount(k) for k in COST_KEYS), ZERO))
    income_eur = sum(rounded(k) for k in INCOME_KEYS if k in book)
    costs_eur = sum(rounded(k) for k in COST_KEYS if k in book)
    totals = DraftTotals(
        income=income,
        costs=costs,
        result=income - costs,
        income_rounded=income_eur,
        costs_rounded=costs_eur,
        result_rounded=income_eur - costs_eur,
    )
    shares = [o.share for o in facts.owners]
    owners = [
        OwnerResult(owner_id=o.owner_id, share=o.share, amount=cents_part, amount_rounded=eur)
        for o, cents_part, eur in zip(
            facts.owners,
            split_cents(totals.result, shares),
            split_euros(totals.result_rounded, shares),
            strict=True,
        )
    ]

    # 4. The lines in form order.
    fixed = {
        "income.total": (totals.income, totals.income_rounded),
        "wk.total": (totals.costs, totals.costs_rounded),
        "result.surplus": (totals.result, totals.result_rounded),
    }
    for key, owner in zip(OWNER_KEYS, owners, strict=False):
        fixed[key] = (owner.amount, owner.amount_rounded)
    by_key = {line.key: line for line in book.lines()}
    lines: list[DraftLine] = []
    for key, form_line in form.lines.items():
        if form_line.kind == "header":
            continue
        placement = {
            "zeile_from": form_line.zeile_from,
            "zeile": form_line.zeile,
            "kennzahl": form_line.kennzahl,
            "label": form_line.label,
        }
        if key in fixed:
            amount, eur = fixed[key]
            lines.append(DraftLine(key=key, amount=amount, amount_rounded=eur, **placement))
            continue
        line = by_key.get(key)
        if line is None or not (line.ticked or line.provisional or line.amount):
            continue
        lines.append(
            DraftLine(
                **line.model_dump(),
                amount_rounded=None if line.amount is None else rounded(key),
                **placement,
            )
        )

    return AnlageVDraft(
        property_id=prop.id,
        tax_year=tax_year,
        rule_set=RuleSetStamp(
            tax_year=rule_set.tax_year,
            version=rule_set.version,
            form_year=form.form_year,
            form_status=form.status,
        ),
        header=_header(facts),
        lines=lines,
        unit_rows=rows,
        totals=totals,
        owners=owners,
        afa=afa_line,
        movable_assets=movable,
        tracked_not_deductible=tracked,
        moved_to_other_year=_moved(facts, tax_year, rule_set),
        pre_handover=pre_handover,
        excluded_open=excluded,
        warnings=warnings,
        missing_documents=_missing_documents(facts, rule_set, tax_year),
        limit_15=limit,
        reduced_rent=reduced,
        coverage=_coverage(facts, tax_year),
        tax_free_from=None
        if prop.notarized_on is None
        else tax_free_from(prop.notarized_on, rule_set),
    )
