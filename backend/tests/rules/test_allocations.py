"""Pending until hausbuch.rules.allocations exists (phase 2); then these must pass unchanged."""

import pytest
from fixture_models_phase2 import SumFixture, load

from hausbuch.rules.errors import RuleError

rules = pytest.importorskip("hausbuch.rules.allocations", reason="phase 2 rule not implemented")
FX = load("allocation_sum")
assert isinstance(FX, SumFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_check_sum(case):
    if case.expect.error is None:
        assert rules.check_sum(case.transaction_amount, case.allocations) is None
        return
    with pytest.raises(RuleError) as excinfo:
        rules.check_sum(case.transaction_amount, case.allocations)
    assert excinfo.value.code == case.expect.error
