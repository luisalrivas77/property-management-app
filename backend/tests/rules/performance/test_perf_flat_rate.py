"""The Nebenkostenpauschale rise test against flat_rate_rise.yaml (docs/phases/phase-4.md
D155): every field compares exactly, sources included."""

from datetime import date
from decimal import Decimal

import pytest
from fixture_models_phase4 import FlatRateFixture, load

from hausbuch.rules.performance.flat_rate import flat_rate_check, month_after_next
from hausbuch.schemas.allocation import AllocationCategory, ServicePeriod
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.performance import FlatRateCheck, FlatRateVerdict, RateBasis

FIXTURE = load("flat_rate_rise")
assert isinstance(FIXTURE, FlatRateFixture)


def _case(case_id: str):
    return next(c for c in FIXTURE.cases if c.id == case_id)


@pytest.mark.rule
@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_flat_rate_rise_fixture(case):
    got = flat_rate_check(case.facts, case.today)
    want = FlatRateCheck.model_validate(case.expect.model_dump())
    assert got == want, case.id


def test_month_after_next():
    assert month_after_next(date(2027, 1, 15)) == date(2027, 3, 1)
    assert month_after_next(date(2027, 1, 1)) == date(2027, 3, 1)
    assert month_after_next(date(2027, 11, 30)) == date(2028, 1, 1)


def test_no_flat_rate_tenancy_gives_an_empty_check():
    case = _case("fall_lowers")
    tenancies = [
        t.model_copy(update={"operating_costs_terms": OperatingCostsTerms.PREPAYMENT})
        for t in case.facts.tenancies
    ]
    facts = case.facts.model_copy(update={"tenancies": tenancies})
    got = flat_rate_check(facts, case.today)
    assert (got.tenancies, got.levels, got.pauschalen_total) == ([], [], Decimal("0.00"))


def test_an_ended_tenancy_is_not_in_force():
    case = _case("hausgeld_rise_three_rooms")
    ended = case.facts.tenancies[2].model_copy(update={"end_date": date(2027, 1, 31)})
    facts = case.facts.model_copy(update={"tenancies": [*case.facts.tenancies[:2], ended]})
    got = flat_rate_check(facts, case.today)
    # Zimmer 3 gone: 45.00 split over 100 + 150 = 250 → 18.00 and 27.00
    assert [t.rise for t in got.tenancies] == [Decimal("18.00"), Decimal("27.00")]


def test_a_whole_year_service_period_does_not_move_a_monthly_payment():
    """A purpose naming the year gives a year's period; the booking date stays the month."""
    case = _case("rise_ten_01_todo")
    year = ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    allocations = [
        a.model_copy(update={"service_period": year}) if a.booking_date.year == 2026 else a
        for a in case.facts.allocations
    ]
    facts = case.facts.model_copy(update={"allocations": allocations})
    assert flat_rate_check(facts, case.today) == flat_rate_check(case.facts, case.today)


def test_without_any_recurring_line_every_line_is_a_payment():
    case = _case("hausgeld_rise_three_rooms")
    allocations = [a.model_copy(update={"recurring": False}) for a in case.facts.allocations]
    facts = case.facts.model_copy(update={"allocations": allocations})
    got = flat_rate_check(facts, case.today)
    assert got.settlements == []
    now = got.levels[-1].rates[0]
    assert now.category is AllocationCategory.HAUSGELD_UMLAGEFAEHIG
    assert now.monthly == Decimal("195.00")


def test_electricity_never_triggers_even_a_large_rise():
    case = _case("electricity_rise_no_todo")
    allocations = [
        a.model_copy(update={"amount": Decimal("-900.00")}) if a.amount == Decimal("-90.00") else a
        for a in case.facts.allocations
    ]
    facts = case.facts.model_copy(update={"allocations": allocations})
    got = flat_rate_check(facts, case.today)
    assert got.tenancies[0].verdict is FlatRateVerdict.NONE
    assert got.tenancies[0].changes[3].change == Decimal("840.00")


def test_a_lone_insurance_line_is_a_yearly_premium():
    case = _case("hausgeld_rise_three_rooms")
    got = flat_rate_check(case.facts, case.today)
    assert got.levels[-1].rates[2].basis is RateBasis.YEARLY_PREMIUM
