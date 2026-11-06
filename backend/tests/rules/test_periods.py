"""Pending until hausbuch.rules.periods exists (phase 2); then these must pass unchanged."""

import pytest
from fixture_models_phase2 import ServicePeriodFixture, load

rules = pytest.importorskip("hausbuch.rules.periods", reason="phase 2 rule not implemented")
FX = load("service_period")
assert isinstance(FX, ServicePeriodFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_service_period(case):
    assert rules.service_period(case.purpose, case.booking_date, case.recurring) == case.expect
