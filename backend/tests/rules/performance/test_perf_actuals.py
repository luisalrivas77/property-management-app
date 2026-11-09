"""§5.3 year figures of an actual year against actuals.yaml (docs/phases/phase-4.md D85, D91,
D92)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase4 import ActualsFixture, load

from hausbuch.rules.performance.projection import actual_year, actual_years
from hausbuch.rules.performance.year_figures import flat_rate_months
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.performance import YearKind
from hausbuch.schemas.tax import PreHandoverIncome, TaxInputFact, TaxInputKind, TaxTenancy

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


# --- D92: the months the Pauschalen were in force --------------------------------------

UNIT = UUID("0000000c-0000-4000-8000-000000000001")


def _tenancy(n: int, start: date, end: date | None = None, terms=OperatingCostsTerms.FLAT_RATE):
    return TaxTenancy(
        id=UUID(f"0000000e-0000-4000-8000-{n:012d}"),
        unit_id=UNIT,
        start_date=start,
        end_date=end,
        operating_costs_terms=terms,
    )


@pytest.mark.parametrize(
    ("tenancies", "handover", "months"),
    [
        # A tenancy from before the handover counts from the handover's month only.
        ([_tenancy(1, date(2026, 3, 1))], date(2026, 7, 1), 6),
        # Any day of a month counts it: in force from 31 Aug, handover 20 Jul → Aug–Dec.
        ([_tenancy(1, date(2026, 8, 31))], date(2026, 7, 20), 5),
        # Without a handover date there is no restriction.
        ([_tenancy(1, date(2026, 3, 1))], None, 10),
        # A handover in an earlier year restricts nothing; an end date does (Jan–Apr).
        ([_tenancy(1, date(2020, 1, 1), date(2026, 4, 1))], date(2020, 1, 1), 4),
        # Two flat-rate tenancies with a gap between them, and one overlapping the first:
        # each month counts once (Jan–Mar from the first, Feb–Dec from the third).
        (
            [
                _tenancy(1, date(2025, 1, 1), date(2026, 3, 15)),
                _tenancy(2, date(2026, 10, 1)),
                _tenancy(3, date(2026, 2, 1)),
            ],
            None,
            12,
        ),
        # Jan–Mar and Oct–Dec only: 6, not the 12 calendar months.
        (
            [_tenancy(1, date(2025, 1, 1), date(2026, 3, 15)), _tenancy(2, date(2026, 10, 1))],
            None,
            6,
        ),
        # A handover after the year: none of its months count.
        ([_tenancy(1, date(2026, 1, 1))], date(2027, 1, 1), None),
        # A flat-rate tenancy that ended before the year: 0 months, None.
        ([_tenancy(1, date(2024, 1, 1), date(2025, 12, 31))], None, None),
        # Only prepayment tenancies: no check.
        ([_tenancy(1, date(2026, 1, 1), terms=OperatingCostsTerms.PREPAYMENT)], None, None),
    ],
)
def test_flat_rate_months(tenancies, handover, months):
    assert flat_rate_months(2026, tenancies, handover) == months


@pytest.mark.parametrize(
    ("until", "months"),
    [
        # The year still being booked: July (the handover) to today's month only.
        (date(2026, 11, 9), 5),
        (date(2026, 7, 1), 1),
        (date(2026, 12, 31), 6),
        # A later today leaves a past year whole.
        (date(2027, 1, 15), 6),
        # Today before the handover month, or before the year: no month yet.
        (date(2026, 6, 30), None),
        (date(2025, 12, 31), None),
    ],
)
def test_flat_rate_months_stop_at_today_in_the_current_year(until, months):
    """Review F1 of D92: in the year being booked the sums hold only the months so far, so the
    months divided by stop at today's month; past and projected years are not capped."""
    tenancies = [_tenancy(1, date(2026, 7, 1))]
    assert flat_rate_months(2026, tenancies, date(2026, 7, 1), until) == months


def test_the_gap_divides_by_the_months_from_the_handover():
    """Case A's flat_rate tenancy moved to 1 Mar: the seller had the flat until the handover on
    1 Jul, so the gap still divides by July to December (6), not by 10 or 12."""
    case = _case("flat_rate_costs_above_pauschale")
    facts = case.facts.resolve()
    earlier = [t.model_copy(update={"start_date": date(2026, 3, 1)}) for t in facts.tenancies]
    facts = facts.model_copy(update={"tenancies": earlier})
    got = actual_year(facts, case.year, case.today, case.owners, case.income_inputs)
    assert (got.flat_rate_months, got.flat_rate_gap_monthly) == (6, Decimal("-20.00"))
