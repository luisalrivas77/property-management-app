"""Pending until hausbuch.rules.loan_schedule exists; then these must pass unchanged."""

import pytest
from fixture_models import LoanFixture, load

rules = pytest.importorskip("hausbuch.rules.loan_schedule", reason="phase 1 rule not implemented")
FX = load("loan_schedule")
assert isinstance(FX, LoanFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_build_schedule(case):
    if case.expect.error:
        with pytest.raises(Exception) as excinfo:
            rules.build_schedule(case.terms)
        assert excinfo.value.code == case.expect.error.code
        assert excinfo.value.at_number == case.expect.error.at_number
        return

    schedule = rules.build_schedule(case.terms)
    assert len(schedule.entries) == case.expect.entry_count
    by_number = {e.number: e for e in schedule.entries}
    for expected in case.expect.entries:
        assert by_number[expected.number] == expected
    by_year = {y.year: y for y in schedule.years}
    for expected in case.expect.years:
        assert by_year[expected.year] == expected
    assert schedule.entries[-1].closing_balance == 0
