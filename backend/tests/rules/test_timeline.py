"""Pending until hausbuch.rules.timeline exists; then these must pass unchanged."""

import random

import pytest
from fixture_models import TimelineFixture, load

rules = pytest.importorskip("hausbuch.rules.timeline", reason="phase 1 rule not implemented")
FX = load("timeline")
assert isinstance(FX, TimelineFixture)
PAYLOAD = {e.id: e.payload for e in FX.events}


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_build_history(case):
    entries = rules.build_history(FX.events, case.scope)
    assert [e.model_dump(exclude={"payload"}) for e in entries] == [
        x.model_dump() for x in case.expect
    ]
    for e in entries:
        assert e.payload == PAYLOAD[e.event_id]


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_input_order_does_not_matter(case):
    shuffled = list(FX.events)
    random.Random(4).shuffle(shuffled)
    assert rules.build_history(shuffled, case.scope) == rules.build_history(FX.events, case.scope)
