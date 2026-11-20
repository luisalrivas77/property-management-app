"""The plan explanation's template text in English (phase 10 D284, Q-O178,
services/advice_texts.py): every German sentence part has its English, and the template cases
of explain_render.yaml render in English with every slot filled and nothing German left."""

import re

import pytest
from fixture_models_phase9 import load

from hausbuch.rules.advice import render as render_rules
from hausbuch.rules.advice import rules
from hausbuch.rules.advice import template as template_rules
from hausbuch.schemas.advice import ExplainFacts, Fact, FigureType, StateValue
from hausbuch.services import advice_texts

RENDER = load("explain_render")
TEMPLATE_CASES = [c for c in RENDER.cases if c.id.startswith("template_")]
GERMAN_MARKS = ("ä", "ö", "ü", "ß", "„", " euch ", " eure ", " Monate", " der ", " und ")


def test_every_german_part_has_english():
    german = {part for s in template_rules.ALL for part in advice_texts.parts(s.text)}
    assert german == set(advice_texts.PARTS)
    slots = rules()["slot_pattern"]
    for de, en in advice_texts.PARTS.items():
        assert sorted(re.findall(slots, de)) == sorted(re.findall(slots, en)), de


def _with_states(facts: ExplainFacts, states: dict[str, str]) -> ExplainFacts:
    merged = {**facts.states, **{k: StateValue(v) for k, v in states.items()}}
    return facts.model_copy(update={"states": merged})


@pytest.mark.parametrize("case", TEMPLATE_CASES, ids=[c.id for c in TEMPLATE_CASES])
def test_template_in_english(case):
    facts = _with_states(case.facts or RENDER.facts, case.states)
    german = template_rules.render(facts, facts.mode)
    english = advice_texts.template(facts, facts.mode)
    assert [p.topic for p in english] == [p.topic for p in german]
    for paragraph in english:
        assert "{" not in paragraph.text, paragraph.text
        assert not any(mark in f" {paragraph.text} " for mark in GERMAN_MARKS), paragraph.text
        assert not re.search(r"\d €", paragraph.text), paragraph.text
    # the German rendering is untouched by the English one
    assert render_rules.render(german, facts, case.labels or RENDER.labels)


def test_formats():
    def fact(kind: FigureType, value: str) -> Fact:
        return Fact.model_construct(key="x", type=kind, value=value)

    assert advice_texts.format_fact(fact(FigureType.MONEY, "9990.40")) == "€9,990"
    assert advice_texts.format_fact(fact(FigureType.MONEY, "-11770")) == "-€11,770"
    assert advice_texts.format_fact(fact(FigureType.PERCENT, "0.7388")) == "73.9%"
    assert advice_texts.format_fact(fact(FigureType.MONTHS, "3.8")) == "3.8 months"
    assert advice_texts.format_fact(fact(FigureType.MONTH, "2028-03")) == "March 2028"
