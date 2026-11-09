"""hausbuch.rules.document_checklist's phase 3 entries against checklist_phase3.yaml
(docs/phases/phase-3.md §8, D64, D70, D71)."""

import random

import pytest
from fixture_models_phase3 import ChecklistFixture, load

from hausbuch.rules import document_checklist as rules

FX = load("checklist_phase3")
assert isinstance(FX, ChecklistFixture)

_LISTS = ("acquisition_costs", "loans", "units", "tenancies", "documents", "allocations")


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_build_checklist(case):
    assert rules.build_checklist(case.facts, case.tax_year, case.today) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_input_order_does_not_matter(case, seed):
    rng = random.Random(seed)
    shuffled = {}
    for name in _LISTS:
        items = list(getattr(case.facts, name))
        rng.shuffle(items)
        shuffled[name] = items
    if case.facts.market_rents is not None:
        shuffled["market_rents"] = rng.sample(case.facts.market_rents, len(case.facts.market_rents))
    facts = case.facts.model_copy(update=shuffled)
    assert rules.build_checklist(facts, case.tax_year, case.today) == case.expect
