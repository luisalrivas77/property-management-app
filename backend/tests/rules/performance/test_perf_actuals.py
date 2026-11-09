"""§5.3 year figures of an actual year against actuals.yaml (docs/phases/phase-4.md D85, D91)."""

from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase4 import ActualsFixture, load

from hausbuch.rules.performance.projection import actual_year, actual_years
from hausbuch.schemas.performance import YearKind
from hausbuch.schemas.tax import PreHandoverIncome, TaxInputFact, TaxInputKind

ACTUALS = load("actuals")
assert isinstance(ACTUALS, ActualsFixture)

EXCLUDE = {"sources": True, "flags": True, "tax_effect": {"sources"}}


def assert_year(got, want, where: str) -> None:
    """Every field exactly except `sources`; flags as sets (fixture_models_phase4)."""
    assert got.model_dump(exclude=EXCLUDE) == want.model_dump(exclude=EXCLUDE), where
    assert set(got.flags) == set(want.flags), f"{where}: flags"


@pytest.mark.rule
@pytest.mark.parametrize("case", ACTUALS.cases, ids=lambda c: c.id)
def test_actuals_fixture(case):
    got = actual_year(case.facts.resolve(), case.year, case.today, case.owners, case.income_inputs)
    assert_year(got, case.expect, case.id)


def _case(case_id: str):
    return next(c for c in ACTUALS.cases if c.id == case_id)


def test_sources_name_every_counted_allocation_and_the_tariff():
    case = _case("anlage_v_2026_case_a")
    got = actual_year(case.facts.resolve(), case.year, case.today, case.owners, case.income_inputs)
    kinds = {s.kind for s in got.sources}
    assert {"allocation", "income_input", "tariff"} <= kinds
    ids = {s.id for s in got.sources if s.kind == "allocation"}
    deposit = next(a for a in case.facts.resolve().allocations if a.category.value == "deposit_in")
    assert deposit.allocation_id not in ids  # financing flows are never cash of the year


def test_actual_years_run_from_the_purchase_year_through_today():
    case = _case("current_year_partial")
    years = actual_years(case.facts.resolve(), case.today, case.owners, case.income_inputs)
    assert [(y.year, y.kind) for y in years] == [(2026, YearKind.ACTUAL_PARTIAL)]


def test_pre_handover_income_moved_to_rent_counts_as_rent():
    """D63: with a pre_handover_income input in force, the seller's forwarded share is rent;
    cash before tax does not change."""
    case = _case("anlage_v_2026_case_a")
    facts = case.facts.resolve()
    decided = TaxInputFact(
        id=UUID("000000d7-0000-4000-8000-000000000001"),
        property_id=facts.property.id,
        kind=TaxInputKind.PRE_HANDOVER_INCOME,
        value=PreHandoverIncome(treatment="rent"),
    )
    facts = facts.model_copy(update={"tax_inputs": [*facts.tax_inputs, decided]})
    got = actual_year(facts, case.year, case.today, case.owners, case.income_inputs)
    assert (got.rent_in, got.other_income) == (Decimal("5950.00"), Decimal("0.00"))
    want = case.expect
    assert got.net_cash + got.tax_effect.effect == want.net_cash + want.tax_effect.effect
    assert got.taxable_result == want.taxable_result + Decimal("850.00")
