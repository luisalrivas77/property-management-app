"""rules/angaben_topics against tests/fixtures/phase10/angaben_topics.yaml (phase 10 §7.8 (b),
D294), and the maps that sort every question, row kind, point and proposal into a topic.
Invented owners only."""

from decimal import Decimal

import fixture_models_phase10 as phase10
import pytest
from fixture_models_angaben_topics import TAX_YEAR, ident

from hausbuch.rules import angaben_topics as rule
from hausbuch.rules import tax_guide
from hausbuch.rules.tax import registry
from hausbuch.schemas.employment import Questionnaire
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.tax_guide import GuideTopic

pytestmark = pytest.mark.rule

CASES = phase10.load("angaben_topics").cases


class _Row:
    def __init__(self, kind, owner_id):
        self.kind = kind
        self.owner_id = owner_id


def _topics(case) -> dict[str, GuideTopic]:
    owners = [
        rule.TopicOwner(
            owner_id=ident(o.id),
            church_tax=o.church_tax,
            tax_state=o.tax_state,
            status=o.status,
            gross_source=o.gross_source,
            gross=o.gross,
        )
        for o in case.owners
    ]
    rows = [_Row(r.kind, None if r.owner is None else ident(r.owner)) for r in case.rows]
    got = rule.topics(
        registry.questionnaire(TAX_YEAR),
        owners,
        case.answer_rows(),
        rows,
        case.guide_points(),
        case.booked(),
    )
    assert [t.topic for t in got] == list(rule.TOPICS)
    return {t.topic: t for t in got}


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_topics(case):
    got = _topics(case)
    for topic, expect in case.expect.items():
        t = got[topic]
        assert (t.state, t.open, t.proposals, t.questions_open, t.questions_total) == (
            expect.state,
            expect.open,
            expect.proposals,
            expect.questions_open,
            expect.questions_total,
        ), topic
    for topic, owners in case.owner_facts.items():
        by_owner = {o.owner_id: o for o in got[topic].owners}
        for facts in owners:
            o = by_owner[ident(facts["id"])]
            for field, value in facts.items():
                if field == "id":
                    continue
                if field == "gross" and value is not None:
                    value = Decimal(value)
                assert getattr(o, field) == value, (topic, facts["id"], field)
    for topic, rows in case.household_rows.items():
        assert got[topic].rows == rows, topic


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_an_owner_topic_lists_every_owner_and_adds_up(case):
    for topic, t in _topics(case).items():
        expected = len(case.owners) if topic in rule.OWNER_TOPICS else 0
        assert len(t.owners) == expected, topic
        if topic in rule.OWNER_TOPICS:
            # every open item belongs to one owner
            assert sum(o.open for o in t.owners) == t.open, topic
            assert t.open_questions == [] and t.rows == {}, topic


# --- The maps are complete -------------------------------------------------------------------


def test_every_owner_and_household_question_names_a_topic_and_no_property_question_does():
    for q in registry.questionnaire(TAX_YEAR).questions:
        assert (q.topic is None) == (q.scope == "property"), q.key
        if q.scope == "owner":
            assert q.topic in rule.OWNER_TOPICS, q.key
        elif q.scope == "household":
            assert q.topic in ("children", "household"), q.key


def test_a_question_without_its_topic_is_refused():
    data = registry.questionnaire(TAX_YEAR).model_dump()
    owner = next(q for q in data["questions"] if q["scope"] == "owner")
    owner["topic"] = None
    with pytest.raises(ValueError, match="names its topic"):
        Questionnaire.model_validate(data)
    data = registry.questionnaire(TAX_YEAR).model_dump()
    household = next(q for q in data["questions"] if q["scope"] == "household")
    household["topic"] = None
    with pytest.raises(ValueError, match="names its topic"):
        Questionnaire.model_validate(data)
    data = registry.questionnaire(TAX_YEAR).model_dump()
    prop = next(q for q in data["questions"] if q["scope"] == "property")
    prop["topic"] = "household"
    with pytest.raises(ValueError, match="names its topic"):
        Questionnaire.model_validate(data)


def test_every_return_input_kind_has_exactly_one_topic():
    seen = [kind for kinds in rule.TOPIC_KINDS.values() for kind in kinds]
    assert sorted(seen) == sorted(ReturnInputKind)


def test_every_point_of_eure_angaben_has_a_topic():
    codes = {c for c, step in tax_guide.STEP_OF_WARNING.items() if step == "angaben"}
    codes -= tax_guide.QUESTIONNAIRE_CODES  # never a point
    codes |= {c for c, step in tax_guide.NOTE_STEP.items() if step == "angaben"}
    codes |= {tax_guide.NO_PAY_CODE, tax_guide.BENEFIT_LETTER_CODE}
    codes |= set(tax_guide.PROPOSAL_CODE.values())
    assert codes == set(rule.POINT_TOPIC)
    assert set(rule.REFUSED_TOPIC) == set(tax_guide.REFUSAL_CODES)


def test_every_proposal_group_has_a_topic_and_its_point_the_same():
    assert set(rule.PROPOSAL_TOPIC) == set(tax_guide.PROPOSAL_CODE)
    for group, topic in rule.PROPOSAL_TOPIC.items():
        assert rule.POINT_TOPIC[tax_guide.PROPOSAL_CODE[group]] == topic


def test_a_negative_choice_is_a_choice_of_its_question():
    questionnaire = registry.questionnaire(TAX_YEAR)
    for key, choices in rule.NEGATIVE_CHOICES.items():
        q = questionnaire.question(key)
        assert q is not None and q.choices is not None
        assert choices <= {c.code for c in q.choices}, key
    for key in rule.CHOICE_FACT:
        assert questionnaire.question(key) is not None, key
