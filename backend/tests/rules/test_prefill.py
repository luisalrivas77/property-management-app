"""Pending until hausbuch.rules.prefill exists (phase 2); then these must pass unchanged."""

import random

import pytest
from fixture_models_phase2 import PrefillExpect, PrefillFixture, load

from hausbuch.schemas.proposal import PrefillContext, ProposalRecord

rules = pytest.importorskip("hausbuch.rules.prefill", reason="phase 2 rule not implemented")
FX = load("prefill")
assert isinstance(FX, PrefillFixture)

LISTS = ("properties", "units", "owners", "costs", "loans", "tenancies", "tenants")


def _shuffled(context: PrefillContext, seed: int) -> PrefillContext:
    rng = random.Random(seed)
    update = {}
    for name in LISTS:
        items = list(getattr(context, name))
        rng.shuffle(items)
        update[name] = items
    return context.model_copy(update=update)


def _check(records: list[ProposalRecord], expect: PrefillExpect) -> None:
    by_key = {r.key: r for r in records}
    assert len(by_key) == len(records), "record keys repeat"
    assert set(by_key) == set(expect.keys)
    for e in expect.records:
        r = by_key[e.key]
        assert (r.entity_type, r.mode, r.target_id) == (e.entity_type, e.mode, e.target_id), e.key
        assert set(r.fields) == set(e.values), e.key
        for name, value in e.values.items():
            got = r.fields[name]
            assert got.value == value, (e.key, name)
            assert got.derived_from == e.derived.get(name, []), (e.key, name)
            assert ("derived" in got.checks) == (name in e.derived), (e.key, name)
        assert r.current == e.current, e.key
        for text in e.notes_contain:
            assert any(text in note for note in r.notes), (e.key, text)


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_build_proposal(case):
    _check(rules.build_proposal(case.kind, case.extraction_model(), case.context), case.expect)


@pytest.mark.rule
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_build_proposal_ignores_context_order(case, seed):
    extraction = case.extraction_model()
    expected = rules.build_proposal(case.kind, extraction, case.context)
    got = rules.build_proposal(case.kind, extraction, _shuffled(case.context, seed))
    assert sorted(got, key=lambda r: r.key) == sorted(expected, key=lambda r: r.key)


def _unit_confidence(case_id: str) -> str:
    (case,) = [c for c in FX.cases if c.id == case_id]
    records = rules.build_proposal(case.kind, case.extraction_model(), case.context)
    (tenancy,) = [r for r in records if r.key == "tenancy"]
    return tenancy.fields["unit_id"].confidence


@pytest.mark.rule
def test_a_unit_found_inside_a_longer_label_is_at_most_medium():
    """prefill.yaml header, "lease unit": a unit whose label was only found inside the
    lease's longer label is proposed at most at medium, so the review looks twice; an equal
    label keeps the confidence of its inputs."""
    assert _unit_confidence("mietvertrag_room_unit_inside_label") == "medium"
    assert _unit_confidence("mietvertrag_room_one_is_not_room_ten") == "high"
