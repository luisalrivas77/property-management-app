"""§5.4 synthesize + project against projection.yaml (docs/phases/phase-4.md D86–D89, §10.3,
§10.4): the projected years exactly, and the sale block where the case has one."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase4 import ProjectionFixture, load
from test_perf_actuals import assert_year
from test_perf_sale import assert_sale

from hausbuch.rules.performance.projection import actual_year, project
from hausbuch.rules.performance.synthesize import snap_cadence, synthesize
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.tariff import total_tax
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import ServicePeriod
from hausbuch.schemas.enums import IncomeInputKind
from hausbuch.schemas.performance import IncomeInputFact
from hausbuch.schemas.tax import AllocationFact, TaxInputFact, TaxInputKind

PROJECTION = load("projection")
assert isinstance(PROJECTION, ProjectionFixture)


def run(case, scenario_id=None):
    return project(
        case.facts.resolve(),
        case.levers,
        case.owners,
        case.income_inputs,
        case.today,
        {loan.loan_id: loan.terms for loan in case.loans},
        scenario_id=scenario_id,
    )


@pytest.fixture(scope="module")
def results():
    return {case.id: run(case) for case in PROJECTION.cases}


@pytest.mark.rule
@pytest.mark.parametrize("case", PROJECTION.cases, ids=lambda c: c.id)
def test_projection_fixture(case, results):
    got = results[case.id]
    assert [y.year for y in got.years] == [y.year for y in case.expect.years], case.id
    for g, w in zip(got.years, case.expect.years, strict=True):
        assert_year(g, w, f"{case.id} {w.year}")
    if case.expect.sale is not None:
        assert_sale(got.sale, case.expect.sale, f"{case.id} sale")


def _case(case_id: str):
    return next(c for c in PROJECTION.cases if c.id == case_id)


def test_synthetic_rows_are_deterministic_and_trace_to_the_scenario():
    case = _case("baseline_no_growth_tax_free_sale")
    facts = case.facts.resolve()
    loans = {loan.loan_id: loan.terms for loan in case.loans}
    first = synthesize(facts, case.levers, loans, case.today)
    again = synthesize(facts, case.levers, loans, case.today)
    assert [r.allocation_id for r in first.all_rows()] == [
        r.allocation_id for r in again.all_rows()
    ]
    rows_2027 = first.rows[2027]
    # January's rent was received on 29 Dec 2026 (§11: 2027); it is not synthesized again.
    rent = [r for r in rows_2027 if r.category is C.RENT_COLD]
    assert [r.booking_date.month for r in rent] == list(range(2, 13))
    assert all(r.recurring and r.due_on == r.booking_date for r in rent)
    assert {first.refs[r.allocation_id].kind for r in rows_2027} == {"scenario", "schedule_entry"}
    loan_rows = [r for r in rows_2027 if r.category is C.LOAN_INTEREST]
    assert all(r.service_period.start.month == r.due_on.month for r in loan_rows)
    grundsteuer = [r.booking_date for r in rows_2027 if r.category is C.GRUNDSTEUER]
    assert grundsteuer == [
        date(2027, 2, 16),
        date(2027, 5, 16),
        date(2027, 8, 16),
        date(2027, 11, 16),
    ]


def test_upkeep_is_excluded_from_the_15pct_test():
    case = _case("upkeep_excluded_from_15pct")
    facts = case.facts.resolve()
    loans = {loan.loan_id: loan.terms for loan in case.loans}
    out = synthesize(facts, case.levers, loans, case.today)
    upkeep = [r for r in out.rows[2027] if r.category is C.REPAIRS]
    assert [(r.booking_date, r.amount) for r in upkeep] == [
        (date(2027, 6, 30), -case.levers.upkeep_per_year)
    ]
    (exclusion,) = [t for t in out.tax_inputs if t.allocation_id == upkeep[0].allocation_id]
    assert exclusion.kind is TaxInputKind.EXCLUDE_FROM_15PCT
    assert exclusion.value.reason == "annual_maintenance"


def test_projected_sources_name_the_saved_scenario(results):
    case = _case("baseline_no_growth_tax_free_sale")
    scenario_id = case.facts.resolve().property.id  # any id stands in for a saved row
    got = run(case, scenario_id=scenario_id)
    kinds = {s.kind for s in got.years[0].sources}
    assert {"scenario", "schedule_entry", "allocation", "tariff", "income_input"} <= kinds
    assert any(s.kind == "scenario" and s.id == scenario_id for s in got.years[0].sources)
    assert {a.code for a in got.assumptions} >= {"sale.on_31_december", "draft.form_provisional"}


def test_a_sale_year_not_after_today_is_refused():
    case = _case("baseline_no_growth_tax_free_sale")
    levers = case.levers.model_copy(update={"sale_year": 2026})
    with pytest.raises(ValueError):
        project(
            case.facts.resolve(),
            levers,
            case.owners,
            case.income_inputs,
            case.today,
            {loan.loan_id: loan.terms for loan in case.loans},
        )


def _with_hausgeld_paid_early(booked: date):
    """Baseline case A plus a Hausgeld (umlagefähig) line for January 2027 paid in December."""
    case = _case("baseline_no_growth_tax_free_sale")
    facts = case.facts.resolve()
    early = AllocationFact(
        allocation_id=UUID("000000f2-0000-4000-8000-000000000001"),
        booking_date=booked,
        amount=Decimal("-180.00"),
        category=C.HAUSGELD_UMLAGEFAEHIG,
        property_id=facts.property.id,
        recurring=True,
        service_period=ServicePeriod(start=date(2027, 1, 1), end=date(2027, 1, 31)),
        due_on=date(2027, 1, 1),
    )
    facts = facts.model_copy(update={"allocations": [*facts.allocations, early]})
    loans = {loan.loan_id: loan.terms for loan in case.loans}
    synthetic = synthesize(facts, case.levers, loans, case.today).rows[2027]
    rows = [r for r in synthetic if r.category is C.HAUSGELD_UMLAGEFAEHIG]
    got = project(facts, case.levers, case.owners, case.income_inputs, case.today, loans)
    return rows, got.years[0]


def test_a_cost_paid_early_but_counted_in_its_paid_year_leaves_no_gap():
    """Review F2: paid 15 Dec 2026, outside the ten days, so §11 books it in 2026. The
    synthetic Hausgeld rows are dated by the booking pattern (the 15th), not by the service
    month, so January 2027 must still get its row: 12 rows, 2,160.00 + 240.00 Grundsteuer."""
    rows, y2027 = _with_hausgeld_paid_early(date(2026, 12, 15))
    assert len(rows) == 12
    assert y2027.year == 2027 and y2027.umlagen_out == Decimal("2400.00")


def test_a_cost_paid_early_and_counted_in_the_next_year_replaces_one_row():
    """Paid 28 Dec 2026 and due 1 Jan 2027: inside the ten days, so §11 books it in 2027 and
    the year's earliest synthetic row is left out: 11 rows + the real one, still 2,400.00."""
    rows, y2027 = _with_hausgeld_paid_early(date(2026, 12, 28))
    assert len(rows) == 11
    assert min(r.booking_date for r in rows) == date(2027, 2, 28)
    assert y2027.umlagen_out == Decimal("2400.00")


@pytest.mark.parametrize("bescheid_year", [2025, 2024])
def test_a_bescheid_of_a_year_before_the_purchase_is_carried_forward(bescheid_year):
    """A Bescheid of a year before the purchase (the flat was bought in 2026) contains nothing
    of the property, so its zvE is the base as it stands: nothing is drafted for that year
    (for 2024 no rule set reaches back, so drafting it raised projection.no_rule_set).
    2026 and the projected years carry it forward."""
    case = _case("baseline_no_growth_tax_free_sale")
    bescheid = IncomeInputFact(
        id=UUID("000000f3-0000-4000-8000-000000000001"),
        kind=IncomeInputKind.BESCHEID_ZVE,
        tax_year=bescheid_year,
        document_id=UUID("000000f3-0000-4000-8000-000000000002"),
        value={"amount": "80000.00"},
    )
    facts = case.facts.resolve()
    loans = {loan.loan_id: loan.terms for loan in case.loans}
    actual = actual_year(facts, 2026, case.today, case.owners, [bescheid])
    got = project(facts, case.levers, case.owners, [bescheid], case.today, loans)
    for year in (actual, *got.years):
        effect = year.tax_effect
        assert effect is not None, year.year
        assert effect.carried_forward and effect.source == "bescheid"
        # T(zvE_base) is the tax on 80,000.00 itself: nothing subtracted for 2025.
        base_tax = total_tax(Decimal("80000.00"), registry.tariff(year.year), case.owners)
        assert effect.breakdown_without == base_tax, year.year
    assert got.sale is not None


def test_a_kitchen_s_afa_is_added_back_at_the_sale():
    """D94 through project(): C's sale inside ten years with a kitchen (D80) of 8,000.00 in
    the price. The sale adds back the kitchen's AfA of every year owned, the actual 2026
    included, next to the building's, and the gain takes both."""
    case = _case("useful_life_and_sale_inside_ten_years")
    facts = case.facts.resolve()
    kitchen = TaxInputFact.model_validate(
        {
            "id": UUID("000000f4-0000-4000-8000-000000000001"),
            "property_id": facts.property.id,
            "kind": "movable_asset",
            "tax_year": 2026,
            "value": {"label": "einbaukueche", "amount": "8000.00"},
        }
    )
    facts = facts.model_copy(update={"tax_inputs": [*facts.tax_inputs, kitchen]})
    loans = {loan.loan_id: loan.terms for loan in case.loans}
    actual = actual_year(facts, 2026, case.today, case.owners, case.income_inputs)
    got = project(facts, case.levers, case.owners, case.income_inputs, case.today, loans)
    owned = [actual, *got.years]
    s = got.sale
    assert s is not None and s.taxable
    movable = sum((y.afa_movable or Decimal("0") for y in owned), Decimal("0"))
    assert movable > 0
    assert s.afa_claimed_movable == movable
    assert s.afa_claimed_building == sum((y.afa or Decimal("0") for y in owned), Decimal("0"))
    assert s.afa_claimed == s.afa_claimed_building + s.afa_claimed_movable
    cost_rows = sum((c.amount for c in facts.cost_rows), Decimal("0"))
    basis = facts.property.purchase_price + cost_rows - s.afa_claimed
    assert s.gain == s.value - s.selling_costs - basis


@pytest.mark.parametrize(
    ("months", "cadence"),
    [("1", 1), ("2.5", 2), ("3", 3), ("4", 3), ("4.5", 3), ("5", 6), ("9", 6), ("11", 12)],
)
def test_snap_cadence(months, cadence):
    assert snap_cadence(Decimal(months)) == cadence
