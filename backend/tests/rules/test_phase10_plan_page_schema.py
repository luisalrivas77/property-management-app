"""„Euer Plan“ (phase 10 §7.11 (h), D306): every case of tests/fixtures/phase10/plan_page.yaml
through services.plan_page.page into the answer's models, both views, every switch: the cards'
figures fit the schema's types (two places, signs, bounds) and the shared view's absent keys
stay out of the JSON, never null or empty."""

import functools
import itertools
import json

import pytest
from test_phase10_plan_page import FIXTURE, _absent, _inputs

from hausbuch.rules.goal import household_goal
from hausbuch.rules.goal.risks import plan_cards
from hausbuch.schemas.plan_page import PlanPage, PlanSwitches, PlanView
from hausbuch.services.plan_page import page

LABEL = "Musterstraße 1, Erdgeschoss links"  # invented: a property label can be an address
NAMES = ("round_amounts", "accounts", "wants", "risks", "assumptions")
SWITCHES = [
    PlanSwitches(**dict(zip(NAMES, v, strict=True)))
    for v in itertools.product((True, False), repeat=5)
]


@functools.cache
def _cards(case_id: str):
    case = next(c for c in FIXTURE.cases if c.id == case_id)
    inputs = _inputs(case)
    answer = household_goal(inputs.goal)
    return plan_cards(inputs, answer), answer, {p.key: LABEL for p in inputs.properties}


def _page(case, view: PlanView, switches: PlanSwitches) -> dict:
    cards, answer, labels = _cards(case.id)
    got = page(cards, answer, view, switches, labels=labels, window=("2026-08", "2026-10"))
    text = got.model_dump_json(by_alias=True)
    PlanPage.model_validate_json(text)  # what the screen gets parses back
    return json.loads(text)


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_own_view(case) -> None:
    data = _page(case, PlanView.OWN, PlanSwitches())
    assert "switches" not in data
    assert "todos" in data
    for p in (data.get("assumptions") or {}).get("properties", []):
        assert p["label"] == LABEL


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_share_view(case) -> None:
    for s in SWITCHES:
        data = _page(case, PlanView.SHARE, s)
        assert LABEL not in json.dumps(data), "a property's label is never shared"
        assert "todos" not in data and "todos" not in (data.get("risks") or {})
        for p in (data.get("assumptions") or {}).get("properties", []):
            assert "label" not in p
        for path, on in (
            ("heute.reserve_accounts", s.accounts),
            ("plan.wants", s.wants),
            ("risks", s.risks),
            ("assumptions", s.assumptions),
        ):
            if not on:
                _absent(data, path)
        if data.get("heute") is not None and s.accounts:
            assert data["heute"]["reserve_accounts"] == list(case.reserve_accounts)
        assert data["switches"] == s.model_dump()
