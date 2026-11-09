"""§5.1 the income tax tariff (docs/phases/phase-4.md, tariff.yaml): income_tax, soli,
church_tax and total_tax on the 2026 tariff, every check of every case."""

import pytest
from fixture_models_phase4 import TariffFixture, load

from hausbuch.rules.tax.registry import tariff
from hausbuch.rules.tax.tariff import church_tax, income_tax, soli, total_tax

TARIFF = load("tariff")
assert isinstance(TARIFF, TariffFixture)
CHECKS = [(c, i, check) for c in TARIFF.cases for i, check in enumerate(c.checks)]


@pytest.mark.rule
@pytest.mark.parametrize(
    ("case", "check"),
    [(c, check) for c, _, check in CHECKS],
    ids=[f"{c.id}-{i}-{check.op}" for c, i, check in CHECKS],
)
def test_tariff(case, check):
    t = tariff(case.tariff_year)
    where = f"{case.id} {check.op}"
    if check.op == "income_tax":
        got = income_tax(check.zve, t, case.joint)
    elif check.op == "soli":
        got = soli(check.est, t, case.joint)
    elif check.op == "church_tax":
        got = church_tax(check.est, case.owners, t, case.joint)
    else:
        breakdown = total_tax(check.zve, t, case.owners, case.joint)
        assert breakdown == check.breakdown, f"{where}: {breakdown} != {check.breakdown}"
        return
    assert got == check.expect, f"{where}: {got} != {check.expect}"
