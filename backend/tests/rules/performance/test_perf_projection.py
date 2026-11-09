"""§5.4 synthesize + project against projection.yaml (docs/phases/phase-4.md D86–D89, §10.3,
§10.4): the projected years exactly, and the sale block where the case has one."""

from datetime import date
from decimal import Decimal

import pytest
from fixture_models_phase4 import ProjectionFixture, load
from test_perf_actuals import assert_year
from test_perf_sale import assert_sale

from hausbuch.rules.performance.projection import project
from hausbuch.rules.performance.synthesize import snap_cadence, synthesize
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.tax import TaxInputKind

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


@pytest.mark.parametrize(
    ("months", "cadence"),
    [("1", 1), ("2.5", 2), ("3", 3), ("4", 3), ("4.5", 3), ("5", 6), ("9", 6), ("11", 12)],
)
def test_snap_cadence(months, cadence):
    assert snap_cadence(Decimal(months)) == cadence
