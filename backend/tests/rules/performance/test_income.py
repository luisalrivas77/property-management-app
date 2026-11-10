"""§5.1 tax_effect and §5.2 zve_base (docs/phases/phase-4.md D82–D84, marginal_rate.yaml).

Each case asserts what its `expect` names: the household base (`zve`), the tax effect
(`effect`), the tariff stamp (`tariff`), or that nothing applies (`missing`). Every field
compares exactly, None included, except `sources` (empty in the fixture; checked by kind
here)."""

from datetime import date
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal
from uuid import UUID

import pytest
from fixture_models_phase4 import MarginalFixture, load

from hausbuch.rules.performance.income import year_tax_effect, zve_base
from hausbuch.rules.tax.registry import tariff
from hausbuch.rules.tax.tariff import income_tax, tax_effect
from hausbuch.schemas.performance import (
    ChildFact,
    HouseholdTaxContext,
    IncomeInputFact,
    OwnerTaxProfile,
    TariffStamp,
)

MARGINAL = load("marginal_rate")
assert isinstance(MARGINAL, MarginalFixture)
CASES = MARGINAL.cases


def _base(case, t):
    return zve_base(
        case.tax_year,
        case.inputs,
        case.owners,
        t,
        assessed_result=case.assessed_result,
        employment=case.employment or None,
        household=case.household,
    )


def _year_effect(case, t):
    return year_tax_effect(
        case.tax_year,
        case.result,
        case.inputs,
        case.owners,
        t,
        assessed_result=case.assessed_result,
        employment=case.employment or None,
        household=case.household,
    )


@pytest.mark.rule
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_marginal_rate(case):
    if case.pending:
        pytest.skip(f"written ahead of its rule: {case.pending}")
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


def _row(n: int, kind: str, value: dict) -> IncomeInputFact:
    return IncomeInputFact.model_validate(
        {
            "id": UUID(int=n),
            "kind": kind,
            "tax_year": 2026,
            "document_id": UUID(int=100 + n) if kind == "bescheid_zve" else None,
            "value": value,
        }
    )


def _progression_tax(z: Decimal, benefits: Decimal, t) -> Decimal:
    """§32b by hand: income_tax(z + L) ÷ (z + L) cut to six decimals, times z, floored."""
    fictitious = z + benefits
    rate = (income_tax(fictitious, t) / fictitious).quantize(Decimal("0.000001"), ROUND_DOWN)
    return (z * rate).to_integral_value(ROUND_FLOOR)


@pytest.mark.rule
def test_a_bescheid_keeps_only_the_years_benefits():
    """D114: under a Bescheid's zvE the effect is computed with the Progressionsvorbehalt of
    the year's benefits only; children, the §35a credit and the deductions are already in the
    assessed zvE. Without benefits it is the plain tariff; an override stays R × rate."""
    t = tariff(2026)
    owners = [OwnerTaxProfile(owner_id=UUID(int=1)), OwnerTaxProfile(owner_id=UUID(int=2))]
    bescheid = [_row(1, "bescheid_zve", {"amount": "20000.00"})]
    full = HouseholdTaxContext(
        children=[ChildFact(born_on=date(2020, 5, 1))],
        replacement_benefits=Decimal("25000.00"),
        donations=Decimal("1000.00"),
        household_services_credit=Decimal("500.00"),
    )
    result = Decimal("-5000.00")

    def effect(inputs, household):
        return year_tax_effect(
            2026, result, inputs, owners, t, assessed_result=Decimal("0"), household=household
        )

    base, got = effect(bescheid, full)
    assert base.household == HouseholdTaxContext(replacement_benefits=Decimal("25000.00"))
    assert base.household_deductions == 0
    by_hand = _progression_tax(Decimal("15000"), Decimal("25000.00"), t) - _progression_tax(
        Decimal("20000"), Decimal("25000.00"), t
    )
    _, plain = effect(bescheid, None)
    assert got.effect == by_hand
    assert got.effect != plain.effect

    no_benefits = full.model_copy(update={"replacement_benefits": Decimal("0")})
    base, got = effect(bescheid, no_benefits)
    assert base.household is None
    assert got == plain

    override = [*bescheid, _row(2, "marginal_rate_override", {"rate": "0.30"})]
    base, got = effect(override, full)
    assert base.household is None
    assert got == effect(override, None)[1]
    assert got.effect == result * Decimal("0.30")


@pytest.mark.rule
@pytest.mark.parametrize(
    "case", [c for c in CASES if c.inputs and c.result is not None and not c.pending]
)
def test_the_effect_uses_zve_base(case):
    """year_tax_effect's base is exactly zve_base's."""
    t = tariff(case.tax_year)
    estimate, _ = _year_effect(case, t)
    assert estimate == _base(case, t), case.id
