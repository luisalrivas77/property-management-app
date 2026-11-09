"""§5.5 xirr and cagr against irr.yaml (docs/phases/phase-4.md D89, §10.4: ±0.000001)."""

from datetime import date
from decimal import Decimal

import pytest
from fixture_models_phase4 import IrrFixture, load

from hausbuch.rules.performance.irr import cagr, xirr, years_between

IRR = load("irr")
assert isinstance(IRR, IrrFixture)
TOLERANCE = Decimal("0.000001")


def assert_rate(got: Decimal | None, want: Decimal | None, where: str) -> None:
    if want is None:
        assert got is None, f"{where}: {got} != None"
        return
    assert got is not None, f"{where}: None != {want}"
    assert abs(got - want) <= TOLERANCE, f"{where}: {got} != {want}"
    assert got == got.quantize(TOLERANCE), f"{where}: six decimals"


@pytest.mark.rule
@pytest.mark.parametrize("case", IRR.cases, ids=lambda c: c.id)
def test_irr_fixture(case):
    if case.flows is not None:
        got = xirr([(f.on, f.amount) for f in case.flows])
    else:
        c = case.cagr
        got = cagr(c.initial_equity, c.profit, c.years)
    assert_rate(got, case.expect, case.id)


def test_xirr_does_not_depend_on_flow_order():
    flows = [(f.on, f.amount) for f in IRR.cases[2].flows]
    assert xirr(flows) == xirr(list(reversed(flows)))


def test_xirr_zero_rate():
    assert xirr([(date(2026, 1, 1), Decimal("-100")), (date(2027, 1, 1), Decimal("100"))]) == 0


def test_cagr_without_equity_is_none():
    assert cagr(Decimal("0"), Decimal("1000"), Decimal("5")) is None


def test_years_between_uses_365_25():
    assert years_between(date(2026, 7, 1), date(2030, 12, 31)) == Decimal(1644) / Decimal("365.25")
