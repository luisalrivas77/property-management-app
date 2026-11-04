"""Pending until hausbuch.rules.rent exists; then these must pass unchanged."""

import pytest
from fixture_models import RentFixture, load

rules = pytest.importorskip("hausbuch.rules.rent", reason="phase 1 rule not implemented")
FX = load("rent_at")
assert isinstance(FX, RentFixture)


@pytest.mark.rule
@pytest.mark.parametrize(
    ("rows", "case"),
    [(FX.rows, c) for c in FX.cases] + [(FX.tie_break.rows, c) for c in FX.tie_break.cases],
    ids=lambda v: getattr(v, "id", ""),
)
def test_rent_at(rows, case):
    row = rules.rent_at(FX.entries(rows), case.at)
    assert (row.id if row else None) == case.expect
