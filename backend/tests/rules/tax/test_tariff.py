"""§5.1 the income tax tariff (docs/phases/phase-4.md, tariff.yaml): income_tax, soli,
church_tax and total_tax on the 2026 tariff, every check of every case."""

from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase4 import TariffFixture, load

from hausbuch.rules.tax.registry import tariff
from hausbuch.rules.tax.tariff import (
    church_tax,
    income_tax,
    slope,
    soli,
    tax_effect,
    total_tax,
)
from hausbuch.schemas.enums import StateCode
from hausbuch.schemas.performance import OwnerTaxProfile

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


# --- Edges the fixtures do not reach -----------------------------------------------------

D = Decimal
OWNERS = [
    OwnerTaxProfile(owner_id=UUID(int=1), church_tax=True, tax_state=StateCode.BY),
    OwnerTaxProfile(owner_id=UUID(int=2)),
]


def test_a_negative_zve_pays_nothing():
    t = tariff(2026)
    assert income_tax(D("-5000.00"), t) == 0
    assert total_tax(D("-5000.00"), t, OWNERS).total == D("0.00")


def test_no_tax_either_way_gives_a_zero_rate_not_a_negative_zero():
    effect = tax_effect(D("0.00"), D("-5000.00"), tariff(2026), OWNERS)
    assert effect.effect == D("0.00")
    assert str(effect.effective_rate) == "0.0"


def test_an_override_on_a_zero_result_shows_its_rate_as_the_slope():
    effect = tax_effect(
        None, D("0.00"), tariff(2026), OWNERS, source="override", override_rate=D("0.4200")
    )
    assert (effect.effect, effect.effective_rate, effect.slope) == (D("0.00"), None, D("42.0"))
    assert (effect.breakdown_with, effect.breakdown_without) == (None, None)


def test_the_slope_takes_the_soli_factor_of_its_zone():
    t = tariff(2026)
    # 300,000 joint: the 42 % zone, the full Soli (5.5 %), one member at 8 % on half: × 1.095.
    assert slope(D("300000.00"), t, OWNERS) == D("46.0")
    assert slope(D("20000.00"), t, OWNERS) == D("0.0")  # half below the Grundfreibetrag


def test_a_household_effect_needs_a_base_without_an_override():
    with pytest.raises(ValueError):
        tax_effect(None, D("-1.00"), tariff(2026), OWNERS)
