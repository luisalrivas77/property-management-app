"""§5.8 verbilligte Vermietung (docs/phases/phase-3.md, verbilligte_vermietung.yaml)."""

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import ReducedRentFixture, load

from hausbuch.rules.tax.registry import rule_set
from hausbuch.rules.tax.verbilligt import check

REDUCED = load("verbilligte_vermietung")
assert isinstance(REDUCED, ReducedRentFixture)
FIELDS = (
    "wohnung",
    "id",
    "agreed_warm",
    "area_m2",
    "market_cold_per_m2",
    "umlagefaehig_monthly",
    "market_warm",
    "ratio_percent",
    "outcome",
    "cut_percent",
)


@pytest.mark.rule
@pytest.mark.parametrize("case", REDUCED.cases, ids=lambda c: c.id)
def test_reduced_rent(case):
    expect = case.expect
    result = check(case.facts, expect.tax_year, rule_set(REDUCED.rule_set_year))
    assert result.tax_year == expect.tax_year
    assert len(result.checks) == len(expect.checks), result.checks
    for got, want in zip(result.checks, expect.checks, strict=True):
        for field in FIELDS:
            assert getattr(got, field) == getattr(want, field), (
                f"{case.id}: {field} {getattr(got, field)} != {getattr(want, field)}"
            )
        if want.sources is not None:
            assert cmp.refs(got.sources) == cmp.refs(want.sources), case.id
    cmp.warnings(result.warnings, expect.warnings, case.id)
