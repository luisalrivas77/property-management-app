"""D80 AfA on a movable asset, the Einbauküche (docs/phases/phase-3.md, movable_asset.yaml)."""

from decimal import Decimal

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import MovableFixture, load

from hausbuch.rules.tax.movable import movable_afa

MOVABLE = load("movable_asset")
assert isinstance(MOVABLE, MovableFixture)
CASES = [(c, e) for c in MOVABLE.cases for e in c.expect]
FIELDS = (
    "amount",
    "basis",
    "months",
    "method_code",
    "book_value_start",
    "remaining_months",
)


@pytest.mark.rule
@pytest.mark.parametrize(("case", "expect"), CASES, ids=[f"{c.id}-{e.tax_year}" for c, e in CASES])
def test_movable_afa(case, expect):
    lines = movable_afa(case.facts, expect.tax_year)
    where = f"{case.id} {expect.tax_year}"
    assert len(lines) == 1, where
    line = lines[0]
    for field in FIELDS:
        assert getattr(line, field) == getattr(expect, field), (
            f"{where}: {field} {getattr(line, field)} != {getattr(expect, field)}"
        )
    if expect.sources is not None:
        assert cmp.refs(line.sources) == cmp.refs(expect.sources), where
    cmp.warnings(line.warnings, expect.warnings, where)


def _case(case_id: str):
    return next(c for c in MOVABLE.cases if c.id == case_id)


@pytest.mark.rule
@pytest.mark.parametrize(
    ("case_id", "years", "basis"),
    [
        ("kitchen_in_price_from_purchase", range(2026, 2037), Decimal("9208.33")),
        ("kitchen_bought_separately", range(2027, 2037), Decimal("6000.00")),
    ],
)
def test_the_ten_years_sum_to_the_basis(case_id, years, basis):
    """The life's years sum to the basis to the cent; no year is negative; 0.00 after it."""
    facts = _case(case_id).facts
    amounts = [movable_afa(facts, year)[0].amount for year in years]
    assert all(a >= 0 for a in amounts)
    assert sum(amounts) == basis
    after = movable_afa(facts, 2045)[0]
    assert after.amount == Decimal("0.00")
    assert after.model_dump(mode="json")["amount"] == "0.00"


@pytest.mark.rule
def test_no_line_before_the_first_year():
    assert movable_afa(_case("kitchen_bought_separately").facts, 2026) == []


@pytest.mark.rule
def test_one_asset_per_label_the_later_wins():
    """Two inputs of one label in force (the service prevents it): the later in the list."""
    facts = _case("kitchen_in_price_from_purchase").facts
    first = facts.movable_assets[0]
    later = first.model_copy(update={"amount": Decimal("6000.00")})
    lines = movable_afa(facts.model_copy(update={"movable_assets": [first, later]}), 2027)
    assert [line.price_amount for line in lines] == [Decimal("6000.00")]
