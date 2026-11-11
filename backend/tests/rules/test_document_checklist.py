"""hausbuch.rules.document_checklist against document_checklist.yaml (spec amendment 3)."""

import random

import pytest
from fixture_models import ChecklistFixture, load

rules = pytest.importorskip("hausbuch.rules.document_checklist", reason="rule not implemented")
FX = load("document_checklist")
assert isinstance(FX, ChecklistFixture)

_LISTS = ("acquisition_costs", "loans", "units", "tenancies", "documents", "allocations")


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_build_checklist(case):
    if case.pending:
        pytest.skip(case.pending)
    assert rules.build_checklist(case.facts, case.tax_year, case.today) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_input_order_does_not_matter(case, seed):
    if case.pending:
        pytest.skip(case.pending)
    rng = random.Random(seed)
    shuffled = {}
    for name in _LISTS:
        items = list(getattr(case.facts, name))
        rng.shuffle(items)
        shuffled[name] = items
    documents = [
        d.model_copy(update={"links": rng.sample(d.links, len(d.links))})
        for d in shuffled["documents"]
    ]
    facts = case.facts.model_copy(update=shuffled | {"documents": documents})
    assert rules.build_checklist(facts, case.tax_year, case.today) == case.expect
