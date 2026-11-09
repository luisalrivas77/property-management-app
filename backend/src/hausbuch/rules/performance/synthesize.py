"""Synthesized payments of the projected years (docs/phases/phase-4.md §5.4, §10.4, D86). Pure.

For each projected year Y (today.year + 1 … the sale year, n = Y − today.year) this builds the
AllocationFact rows the property would book, so phase 3's draft() can run over them unchanged:

- Rent and NK prepayment per tenancy, from its rent row in force on today (the latest
  effective_from ≤ today, or ≤ its start when it starts later): the year's total is
  monthly × 12 × (1 + rent_growth)^(n−1) × (months let − vacancy) ÷ 12, half-up once, minus
  what real recurring lines already pay for months of Y (January's rent received on
  29 December, §11), spread in equal cents over the open months, the last taking the rest.
  Vacant months are the year's last (whole months; a part month is rent spread thinner).
  Rows are dated the tenancy's rent due day, recurring, service period the month, due_on the
  row's date. An `included` tenancy has no NK; `flat_rate` and `prepayment` do.
- Each recurring cost category (Hausgeld parts, Grundsteuer, insurance, electricity (D92),
  management, bank fees): its latest confirmed line on or before today, annualised as
  amount × 12 ÷ cadence and grown by (1 + cost_inflation)^(n−1), half-up once, then spread
  in equal cents over the
  rows of the year, which repeat the line's date every `cadence` months (the day clamped to the
  month's end). cadence = the months of the line's service period, else the gap between the
  category's two latest dates ÷ 30.4, else 12; snapped to {1, 2, 3, 6, 12}, ties to the
  shorter. The rows are not recurring and carry no period, so §11 books them in their year.
- Upkeep: the lever × (1 + cost_inflation)^(n−1), half-up, one `repairs` row on 30 June with
  a synthetic `exclude_from_15pct` input (reason annual_maintenance, D56), so the 15 % test
  never capitalises routine upkeep.
- Each loan with terms: build_schedule with the scenario's follow-up rate (else the loan's);
  every entry due in Y gives a `loan_interest` and a `loan_principal` row on its due_on,
  recurring, service period the month of due_on; the year's balance is the closing balance
  of its last entry due on or before 31 Dec of Y.

A synthesized rent, NK or loan row (dated in its service month) whose month a real recurring
line of the same category and tenancy or loan already pays (its service period) is left out.
Cost rows are dated by the line's booking pattern instead, so for each real line of the
category that §11 puts into the year (recurring or not), the year's earliest cost row is left
out. Ids are UUID5 over a fixed namespace and the
row's meaning (scenario key, property, year, category, month or entry, tenancy or loan), so
the same levers give the same ids. Every row maps to a SourceRef: `schedule_entry` (the row's
own id) for loan rows, `scenario` for the rest (the saved scenario's id, else the row's id).
"""

import calendar
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid5

from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.rules.tax import registry
from hausbuch.rules.tax._common import of_year
from hausbuch.rules.tax.rounding import CENT, cents
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import ServicePeriod
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.loan import LoanTerms
from hausbuch.schemas.performance import Levers
from hausbuch.schemas.tax import (
    AllocationFact,
    DraftFacts,
    ExcludeFrom15Pct,
    RuleSet,
    SourceRef,
    TaxInputFact,
    TaxInputKind,
    TaxRent,
    TaxTenancy,
)

NAMESPACE = UUID("6f1c5e0a-4d3b-5b7e-9a51-2f0d8c4e7b10")  # fixed: ids must not change
ZERO = Decimal("0.00")
ONE = Decimal("1")
DAYS_PER_MONTH = Decimal("30.4")
CADENCES = (1, 2, 3, 6, 12)
COST_CATEGORIES = (
    C.HAUSGELD_UMLAGEFAEHIG,
    C.HAUSGELD_VERWALTUNG,
    C.HAUSGELD_RUECKLAGE,
    C.GRUNDSTEUER,
    C.INSURANCE,
    C.ELECTRICITY,  # D92: the flat's own supply, projected like Grundsteuer
    C.PROPERTY_MANAGEMENT,
    C.BANK_FEES,
)
UPKEEP_DAY = (6, 30)


@dataclass
class Synthesis:
    """The rows of every projected year, the inputs they need, the loans' year-end balances
    and the source of each row."""

    rows: dict[int, list[AllocationFact]] = field(default_factory=dict)
    tax_inputs: list[TaxInputFact] = field(default_factory=list)
    balances: dict[int, Decimal] = field(default_factory=dict)
    refs: dict[UUID, SourceRef] = field(default_factory=dict)

    def all_rows(self) -> list[AllocationFact]:
        return [r for year in sorted(self.rows) for r in self.rows[year]]


# --- Helpers -----------------------------------------------------------------------------


def _day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(base: date, months: int) -> date:
    index = base.year * 12 + base.month - 1 + months
    return _day(index // 12, index % 12 + 1, base.day)


def _month_period(year: int, month: int) -> ServicePeriod:
    return ServicePeriod(start=date(year, month, 1), end=_day(year, month, 31))


def _months_of(period: ServicePeriod, year: int) -> set[int]:
    """The months of `year` a service period covers."""
    months = set()
    cursor = date(period.start.year, period.start.month, 1)
    while cursor <= period.end:
        if cursor.year == year:
            months.add(cursor.month)
        cursor = _add_months(cursor, 1)
    return months


def _spread(total: Decimal, count: int) -> list[Decimal]:
    """`total` in `count` equal cents, the last taking the rest (§10.4)."""
    if count <= 0:
        return []
    each = (total / count).quantize(CENT, rounding=ROUND_HALF_UP)
    return [each] * (count - 1) + [total - each * (count - 1)]


def _growth(rate: Decimal, n: int) -> Decimal:
    return (ONE + rate) ** (n - 1)


def snap_cadence(months: Decimal) -> int:
    """The nearest of 1, 2, 3, 6, 12 months (ties to the shorter)."""
    return min(CADENCES, key=lambda c: (abs(Decimal(c) - months), c))


def _covered(
    allocations: Iterable[AllocationFact], year: int
) -> dict[tuple[C, UUID | None], tuple[set[int], Decimal]]:
    """What real recurring lines already pay for months of `year`, per category and tenancy
    or loan: the months and the amount."""
    covered: dict[tuple[C, UUID | None], tuple[set[int], Decimal]] = {}
    for a in allocations:
        if a.amount == 0 or not a.recurring or a.service_period is None:
            continue
        months = _months_of(a.service_period, year)
        if not months:
            continue
        key = (a.category, a.tenancy_id or a.loan_id)
        seen, amount = covered.get(key, (set(), ZERO))
        covered[key] = (seen | months, amount + a.amount)
    return covered


class _Builder:
    def __init__(self, facts: DraftFacts, levers: Levers, scenario_key: str, scenario_id):
        self.facts = facts
        self.levers = levers
        self.key = scenario_key
        self.scenario_id = scenario_id
        self.out = Synthesis()

    def uid(self, *parts: object) -> UUID:
        name = "|".join(str(p) for p in (self.key, self.facts.property.id, *parts))
        return uuid5(NAMESPACE, name)

    def add(self, year: int, row: AllocationFact, ref_kind: str) -> None:
        assert row.allocation_id is not None
        self.out.rows.setdefault(year, []).append(row)
        ref_id = self.scenario_id if ref_kind == "scenario" and self.scenario_id else None
        self.out.refs[row.allocation_id] = SourceRef(
            kind=ref_kind,  # type: ignore[arg-type]
            id=ref_id or row.allocation_id,
            amount=row.amount,
        )

    def row(self, year: int, category: C, amount: Decimal, on: date, *parts, **extra):
        return AllocationFact(
            allocation_id=self.uid(year, category.value, *parts),
            booking_date=on,
            amount=amount,
            category=category,
            property_id=self.facts.property.id,
            **extra,
        )


# --- Rent and NK -------------------------------------------------------------------------


def _rent_row(tenancy: TaxTenancy, today: date) -> TaxRent | None:
    anchor = max(today, tenancy.start_date)
    rows = [r for r in tenancy.rents if r.effective_from <= anchor]
    return max(rows, key=lambda r: r.effective_from) if rows else None


def _let_months(tenancy: TaxTenancy, year: int) -> list[int]:
    """The months of `year` the tenancy runs in (started by the month's end, not ended before
    its first day)."""
    return [
        m
        for m in range(1, 13)
        if tenancy.start_date <= _day(year, m, 31)
        and (tenancy.end_date is None or tenancy.end_date >= date(year, m, 1))
    ]


def _rents(b: _Builder, year: int, n: int, today: date, covered) -> None:
    lv = b.levers
    for tenancy in b.facts.tenancies:
        rent = _rent_row(tenancy, today)
        let = _let_months(tenancy, year)
        if rent is None or not let:
            continue
        vacancy = min(lv.vacancy_months, Decimal(len(let)))
        months = let[: len(let) - int(vacancy)]
        parts = [(C.RENT_COLD, rent.cold_rent)]
        if tenancy.operating_costs_terms is not OperatingCostsTerms.INCLUDED:
            parts.append((C.OPERATING_COSTS_PREPAYMENT, rent.operating_costs_prepayment))
        for category, monthly in parts:
            if monthly == 0:
                continue
            total = cents(
                monthly * 12 * _growth(lv.rent_growth, n) * (Decimal(len(let)) - vacancy) / 12
            )
            paid_months, paid = covered.get((category, tenancy.id), (set(), ZERO))
            open_months = [m for m in months if m not in paid_months]
            for month, amount in zip(
                open_months, _spread(total - paid, len(open_months)), strict=True
            ):
                if amount <= 0:
                    continue
                on = _day(year, month, tenancy.rent_due_day)
                b.add(
                    year,
                    b.row(
                        year,
                        category,
                        amount,
                        on,
                        month,
                        tenancy.id,
                        recurring=True,
                        service_period=_month_period(year, month),
                        due_on=on,
                        unit_id=tenancy.unit_id,
                        tenancy_id=tenancy.id,
                    ),
                    "scenario",
                )


# --- Recurring costs ---------------------------------------------------------------------


@dataclass(frozen=True)
class CostTemplate:
    category: C
    line: AllocationFact  # the latest confirmed line on or before today
    cadence: int  # months


def cost_templates(allocations: Sequence[AllocationFact], today: date) -> list[CostTemplate]:
    """Per recurring cost category, its latest line on or before today and its cadence."""
    templates = []
    for category in COST_CATEGORIES:
        lines = sorted(
            (
                a
                for a in allocations
                if a.category is category and a.amount != 0 and a.booking_date <= today
            ),
            key=lambda a: (a.booking_date, str(a.allocation_id)),
        )
        if not lines:
            continue
        latest = lines[-1]
        if latest.service_period is not None:
            p = latest.service_period
            span = (p.end.year - p.start.year) * 12 + p.end.month - p.start.month + 1
            cadence = snap_cadence(Decimal(span))
        else:
            dates = sorted({a.booking_date for a in lines})
            if len(dates) >= 2:
                gap = Decimal((dates[-1] - dates[-2]).days) / DAYS_PER_MONTH
                cadence = snap_cadence(gap.quantize(ONE, rounding=ROUND_HALF_UP))
            else:
                cadence = 12
        templates.append(CostTemplate(category=category, line=latest, cadence=cadence))
    return templates


def _costs(
    b: _Builder, year: int, n: int, templates: list[CostTemplate], real_of_year: Sequence
) -> None:
    """The cost rows of `year`. Synthetic cost rows are dated by the line's booking pattern,
    not by a service month, so what real lines already pay is judged by §11: for each real
    line of the category that §11 puts into `year` (recurring or not), the year's earliest
    synthetic row is left out (review F2)."""
    for t in templates:
        annual = cents(-t.line.amount * 12 / t.cadence * _growth(b.levers.cost_inflation, n))
        dates = []
        k = 1
        while True:
            on = _add_months(t.line.booking_date, k * t.cadence)
            if on.year > year:
                break
            if on.year == year:
                dates.append(on)
            k += 1
        paid = sum(1 for a in real_of_year if a.category is t.category)
        rows = list(zip(dates, _spread(annual, len(dates)), strict=True))
        for on, amount in rows[paid:]:  # the dates ascend: the earliest are left out
            if amount == 0:
                continue
            b.add(
                year,
                b.row(
                    year,
                    t.category,
                    -amount,
                    on,
                    on.isoformat(),
                    unit_id=t.line.unit_id,
                ),
                "scenario",
            )


def _upkeep(b: _Builder, year: int, n: int) -> None:
    amount = cents(b.levers.upkeep_per_year * _growth(b.levers.cost_inflation, n))
    if amount == 0:
        return
    row = b.row(year, C.REPAIRS, -amount, date(year, *UPKEEP_DAY), "upkeep")
    b.add(year, row, "scenario")
    b.out.tax_inputs.append(
        TaxInputFact(
            id=b.uid(year, "exclude_from_15pct", row.allocation_id),
            property_id=b.facts.property.id,
            kind=TaxInputKind.EXCLUDE_FROM_15PCT,
            allocation_id=row.allocation_id,
            value=ExcludeFrom15Pct(reason="annual_maintenance"),
        )
    )


# --- Loans -------------------------------------------------------------------------------


def _loans(b: _Builder, years: range, loans: Mapping[UUID, LoanTerms], covered_by_year) -> None:
    follow_up = {lv.loan_id: lv.follow_up_rate for lv in b.levers.loans}
    for loan_id, terms in loans.items():
        rate = follow_up.get(loan_id)
        if rate is not None:
            terms = terms.model_copy(update={"follow_up_rate": rate})
        schedule = build_schedule(terms)
        for year in years:
            due = [e for e in schedule.entries if e.due_on.year <= year]
            balance = due[-1].closing_balance if due else terms.principal
            b.out.balances[year] = b.out.balances.get(year, ZERO) + balance
            covered = covered_by_year[year]
            for e in (e for e in due if e.due_on.year == year):
                for category, amount in (
                    (C.LOAN_INTEREST, e.interest),
                    (C.LOAN_PRINCIPAL, e.principal),
                ):
                    paid_months, _ = covered.get((category, loan_id), (set(), ZERO))
                    if amount == 0 or e.due_on.month in paid_months:
                        continue
                    b.add(
                        year,
                        b.row(
                            year,
                            category,
                            -amount,
                            e.due_on,
                            e.number,
                            loan_id,
                            recurring=True,
                            service_period=_month_period(e.due_on.year, e.due_on.month),
                            due_on=e.due_on,
                            loan_id=loan_id,
                        ),
                        "schedule_entry",
                    )


# --- Entry point -------------------------------------------------------------------------


def synthesize(
    facts: DraftFacts,
    levers: Levers,
    loans: Mapping[UUID, LoanTerms],
    today: date,
    *,
    scenario_id: UUID | None = None,
    rule_sets: Callable[[int], RuleSet] = registry.rule_set_or_latest,
) -> Synthesis:
    """The synthesized rows of every year from today.year + 1 to levers.sale_year.
    `rule_sets` gives the §11 rule set of a year (the cost rows' check against real lines)."""
    key = str(scenario_id) if scenario_id is not None else levers.model_dump_json()
    b = _Builder(facts, levers, key, scenario_id)
    years = range(today.year + 1, levers.sale_year + 1)
    real = facts.allocations
    covered_by_year = {y: _covered(real, y) for y in years}
    templates = cost_templates(real, today)
    for year in years:
        n = year - today.year
        _rents(b, year, n, today, covered_by_year[year])
        _costs(b, year, n, templates, of_year(real, year, rule_sets(year)))
        _upkeep(b, year, n)
    _loans(b, years, loans, covered_by_year)
    return b.out
