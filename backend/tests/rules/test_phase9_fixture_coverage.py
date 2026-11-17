"""Phase 9 step 0: the explanation fixtures and the rule data agree with each other.

The validator itself is step 4's (rules/advice/validator.py is a stub); what step 0 can check
now is that the must-accept texts could pass at all (their slots are held by the facts and
allowed in their mode, their words are in the vocabulary), that every reject names a closed
violation code, that the case counts meet §15.1, and that the data files load.
"""

import re

import fixture_models_phase9 as phase9
import pytest

from hausbuch.rules.advice import lexicon, rules, vocabulary
from hausbuch.rules.health.bands import proposals
from hausbuch.schemas.advice import STATES, AdviceMode, Topic, ViolationCode, known_figure_key
from hausbuch.schemas.health import LightColour, MarkedFigure

WORD = re.compile(r"[A-Za-zäöüÄÖÜß]+")


def validator_fixture() -> phase9.ExplainValidatorFixture:
    return phase9.load("explain_validator")


def outside_slots(text: str) -> str:
    return re.sub(rules()["slot_pattern"], " ", text)


def band_sentences() -> dict[str, str]:
    return proposals()[2]


def test_the_data_files_load_with_their_versions():
    version, by_figure, sentences = proposals()
    assert version
    assert set(by_figure) == set(MarkedFigure)
    assert rules()["version"].startswith("advice-rules-")
    lex_version, names = lexicon()
    assert lex_version and names
    voc_version, words = vocabulary()
    assert voc_version and len(words) > 300
    for proposal in by_figure.values():
        for codes in proposal.sentences.values():
            assert all(code in sentences for code in codes)


def test_the_case_counts_of_section_15_1():
    fixture = validator_fixture()
    assert len(fixture.accept) >= 25
    assert len(fixture.reject) >= 60


def test_every_reject_names_closed_codes_and_topics():
    for case in validator_fixture().reject:
        codes = case.rejects if isinstance(case.rejects, list) else [case.rejects]
        assert codes and all(isinstance(code, ViolationCode) for code in codes), case.id
        assert case.topic is None or isinstance(case.topic, Topic), case.id


def test_every_violation_code_has_a_reject_case():
    seen = set()
    for case in validator_fixture().reject:
        seen |= set(case.rejects if isinstance(case.rejects, list) else [case.rejects])
    assert set(ViolationCode) - seen == set()


@pytest.mark.parametrize("case", validator_fixture().accept, ids=lambda case: case.id)
def test_a_must_accept_text_uses_only_its_facts_and_allowed_words(case):
    fixture = validator_fixture()
    mode = case.mode or fixture.facts.mode
    assert phase9.slots_outside(case.text, fixture.facts, mode) == []
    for name, value in case.states.items():
        assert value in STATES[name], (case.id, name)
    _, words = vocabulary()
    missing = {w.lower() for w in WORD.findall(outside_slots(case.text))} - words
    assert missing == set(), case.id


def test_a_generic_text_names_no_account_plan_or_asset():
    allowed = set(rules()["slots_by_mode"][AdviceMode.GENERIC.value])
    assert allowed == {"f", "c"}
    for case in validator_fixture().accept:
        if case.mode is AdviceMode.GENERIC:
            letters = {m.group(1) for m in re.finditer(rules()["slot_pattern"], case.text)}
            assert letters <= allowed, case.id


@pytest.mark.parametrize("code", sorted(band_sentences()))
def test_every_mark_sentence_could_pass_the_validator(code):
    """The case `every_mark_sentence`: each sentence of bands.yaml is a must-accept text."""
    text = band_sentences()[code]
    fixture = validator_fixture()
    for letter, key in re.findall(rules()["slot_pattern"], text):
        assert letter == "f", (code, key)
        assert known_figure_key(key), (code, key)
        assert key in fixture.facts.figures, (code, key)
    _, words = vocabulary()
    missing = {w.lower() for w in WORD.findall(outside_slots(text))} - words
    assert missing == set(), code


def test_every_light_colour_has_its_sentences():
    _, by_figure, _ = proposals()
    for figure, proposal in by_figure.items():
        assert set(proposal.sentences) == set(LightColour), figure


def test_no_lexicon_name_is_a_vocabulary_word():
    _, names = lexicon()
    _, words = vocabulary()
    single = {name for name in names if " " not in name and "-" not in name}
    assert single & words == set()
