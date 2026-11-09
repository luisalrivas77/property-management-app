"""§5.3 the 15 % tracker (docs/phases/phase-3.md, anschaffungsnah.yaml)."""

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import LimitFixture, load

from hausbuch.rules.tax.anschaffungsnah import limit_status
from hausbuch.rules.tax.registry import rule_set

LIMIT = load("anschaffungsnah")
assert isinstance(LIMIT, LimitFixture)
CASES = [(c, e) for c in LIMIT.cases for e in c.expect]
FIELDS = (
    "basis_building_cost",
    "limit",
    "window_start",
    "window_end",
    "counted",
    "used_net",
    "ratio_percent",
    "exceeded",
    "exceeded_in_year",
)


@pytest.mark.rule
@pytest.mark.parametrize(("case", "expect"), CASES, ids=[f"{c.id}-{e.tax_year}" for c, e in CASES])
def test_limit_status(case, expect):
    status = limit_status(case.facts, expect.tax_year, rule_set(LIMIT.rule_set_year))
    where = f"{case.id} {expect.tax_year}"
    assert status.tax_year == expect.tax_year
    for field in FIELDS:
        assert getattr(status, field) == getattr(expect, field), (
            f"{where}: {field} {getattr(status, field)} != {getattr(expect, field)}"
        )
    cmp.basis_additions(status.basis_additions, expect.basis_additions, where)
    cmp.warnings(status.warnings, expect.warnings, where)
