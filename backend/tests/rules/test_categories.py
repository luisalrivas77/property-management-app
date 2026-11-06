"""Pending until hausbuch.rules.categories exists (phase 2); then these must pass unchanged."""

import pytest
from fixture_models_phase2 import CategoriesFixture, load

rules = pytest.importorskip("hausbuch.rules.categories", reason="phase 2 rule not implemented")
FX = load("categories")
assert isinstance(FX, CategoriesFixture)


@pytest.mark.rule
@pytest.mark.parametrize("row", FX.categories, ids=lambda r: r.category.value)
def test_tax_treatment(row):
    assert rules.tax_treatment(row.category) == row.treatment
