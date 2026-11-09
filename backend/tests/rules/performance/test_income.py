"""§5.1 tax_effect and §5.2 zve_base (docs/phases/phase-4.md D82–D84, marginal_rate.yaml).

Each case asserts what its `expect` names: the household base (`zve`), the tax effect
(`effect`), the tariff stamp (`tariff`), or that nothing applies (`missing`). Every field
compares exactly, None included, except `sources` (empty in the fixture; checked by kind
here)."""

import pytest
from fixture_models_phase4 import MarginalFixture, load

from hausbuch.rules.performance.income import year_tax_effect, zve_base
from hausbuch.rules.tax.registry import tariff
from hausbuch.rules.tax.tariff import tax_effect
from hausbuch.schemas.performance import TariffStamp

MARGINAL = load("marginal_rate")
assert isinstance(MARGINAL, MarginalFixture)
CASES = MARGINAL.cases


def _base(case, t):
    return zve_base(
        case.tax_year, case.inputs, case.owners, t, assessed_result=case.assessed_result
    )


def _year_effect(case, t):
    return year_tax_effect(
        case.tax_year,
        case.result,
        case.inputs,
        case.owners,
        t,
        assessed_result=case.assessed_result,
    )


@pytest.mark.rule
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_marginal_rate(case):
    t = tariff(case.tax_year)
    expect = case.expect
    estimate = _base(case, t)
    if expect.missing:
        assert estimate is None, case.id
        assert _year_effect(case, t) == (None, None), case.id
    if expect.zve is not None:
        assert estimate is not None, case.id
        got = estimate.model_dump(exclude={"sources"})
        assert got == expect.zve.model_dump(exclude={"sources"}), case.id
        assert {s.kind for s in estimate.sources} == {"income_input"}, case.id
    if expect.effect is not None:
        if case.zve_base is not None:  # a stated base: the tariff arithmetic alone
            effect = tax_effect(case.zve_base, case.result, t, case.owners)
        else:
            _, effect = _year_effect(case, t)
        assert effect is not None, case.id
        got = effect.model_dump(exclude={"sources"})
        assert got == expect.effect.model_dump(exclude={"sources"}), case.id
        kinds = {s.kind for s in effect.sources}
        assert "tariff" in kinds, case.id
        if case.zve_base is None:
            assert "income_input" in kinds, case.id
    if expect.tariff is not None:
        stamp = TariffStamp(tax_year=t.tax_year, version=t.version, reused_from=t.reused_from)
        assert stamp == expect.tariff, case.id


@pytest.mark.rule
@pytest.mark.parametrize("case", [c for c in CASES if c.inputs and c.result is not None])
def test_the_effect_uses_zve_base(case):
    """year_tax_effect's base is exactly zve_base's."""
    t = tariff(case.tax_year)
    estimate, _ = _year_effect(case, t)
    assert estimate == _base(case, t), case.id
