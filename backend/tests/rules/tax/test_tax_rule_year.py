"""§5.1 the tax year of a payment and §5.9 the §23 date (docs/phases/phase-3.md)."""

from decimal import Decimal

import pytest
from fixture_models_phase3 import SpekulationFixture, TaxYearFixture, load

from hausbuch.rules.tax.registry import rule_set
from hausbuch.rules.tax.rounding import split_cents, split_euros, whole_euros
from hausbuch.rules.tax.spekulation import tax_free_from
from hausbuch.rules.tax.tax_year import tax_year_of
from hausbuch.schemas.common import Fraction

TAX_YEAR = load("tax_year")
assert isinstance(TAX_YEAR, TaxYearFixture)
SPEKULATION = load("spekulation")
assert isinstance(SPEKULATION, SpekulationFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", TAX_YEAR.cases, ids=lambda c: c.id)
def test_tax_year(case):
    assert tax_year_of(case.payment, rule_set(TAX_YEAR.rule_set_year)) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", SPEKULATION.cases, ids=lambda c: c.id)
def test_tax_free_from(case):
    assert tax_free_from(case.notarized_on, rule_set(SPEKULATION.rule_set_year)) == case.expect


# --- rounding.py (D60): not fixture-backed; the draft fixture checks it end to end --------


def test_whole_euros_directions():
    assert whole_euros(Decimal("10.99"), "down") == 10
    assert whole_euros(Decimal("10.01"), "up") == 11
    assert whole_euros(Decimal("-10.01"), "down") == -11  # a refund line: lower income
    assert whole_euros(Decimal("10.50"), "none") == 11


def test_largest_remainder_odd_unit_to_first_owner():
    half = Fraction(numerator=1, denominator=2)
    assert split_euros(1001, [half, half]) == [501, 500]
    assert split_euros(-1001, [half, half]) == [-501, -500]
    assert split_cents(Decimal("0.01"), [half, half]) == [Decimal("0.01"), Decimal("0.00")]
    third = Fraction(numerator=1, denominator=3)
    two_thirds = Fraction(numerator=2, denominator=3)
    assert split_cents(Decimal("1.00"), [third, two_thirds]) == [
        Decimal("0.33"),
        Decimal("0.67"),
    ]
