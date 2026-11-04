"""Pending until hausbuch.rules.validation exists; then these must pass unchanged."""

import pytest
from fixture_models import ValidationFixture, load

rules = pytest.importorskip("hausbuch.rules.validation", reason="phase 1 rule not implemented")
FX = load("validation")
assert isinstance(FX, ValidationFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.ownership_shares, ids=lambda c: c.id)
def test_ownership_shares(case):
    assert rules.check_ownership_shares(case.shares) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.tenancy_parties, ids=lambda c: c.id)
def test_tenancy_parties(case):
    assert rules.check_tenancy_parties(case.roles) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.rent_rows, ids=lambda c: c.id)
def test_rent_row(case):
    got = rules.check_rent_row(case.tenancy_start, case.tenancy_end, case.effective_from)
    assert got == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.corrections, ids=lambda c: c.id)
def test_correction(case):
    assert rules.check_correction(case.original, case.correction) == case.expect
