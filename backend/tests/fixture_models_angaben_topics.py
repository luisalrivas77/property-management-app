"""Shape of tests/fixtures/phase10/angaben_topics.yaml (phase 10 §7.8 (b), D294), registered in
fixture_models_phase10.MODELS. Labels (A, B) become fixed UUIDs through `ident`; the answers are
built as QuestionnaireAnswer over the shipped 2026 questionnaire, so a fixture naming a question
the file does not ask fails on load."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.questionnaire import slots
from hausbuch.schemas.booked_inputs import BookedGroup, BookedProposal
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.employment import QuestionnaireAnswer, QuestionnaireAnswerValue
from hausbuch.schemas.enums import HOUSEHOLD_KINDS, ReturnInputKind
from hausbuch.schemas.tax_guide import GuidePoint, GuideTopicKind, GuideTopicState

WORKSPACE = uuid5(NAMESPACE_URL, "hausbuch:fixture:phase10:angaben_topics:workspace")
RECORDED = datetime(2026, 11, 26, tzinfo=UTC)
TAX_YEAR = 2026


def ident(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"hausbuch:fixture:phase10:angaben_topics:{label}")


class OwnerIn(Schema):
    id: str
    church_tax: bool
    tax_state: str | None
    status: Literal["certified", "estimate", "none"] | None
    gross_source: Literal["certificate", "payslip", "expected", "declared_none"] | None
    gross: Money | None


class AnswerIn(Schema):
    key: str
    owner: str | None = None
    answer: QuestionnaireAnswerValue | None  # None: leave the slot unanswered
    choice: str | None = None


class RowIn(Schema):
    kind: ReturnInputKind
    owner: str | None = None

    @model_validator(mode="after")
    def _scope(self) -> RowIn:
        if (self.kind in HOUSEHOLD_KINDS) != (self.owner is None):
            raise ValueError(f"{self.kind}: a household kind has no owner, an owner kind one")
        return self


class PointIn(Schema):
    code: str
    owner: str | None = None
    refused: str | None = None
    blocking: bool = True
    step: Literal["angaben", "property", "review"] = "angaben"
    proposal: BookedGroup | None = None  # a proposal's point: never counted as open


class ProposalIn(Schema):
    group: BookedGroup
    owner: str | None = None
    blocking: bool


class TopicExpect(Schema):
    state: GuideTopicState
    open: int
    proposals: int
    questions_open: int
    questions_total: int


class Case(Schema):
    name: str
    owners: list[OwnerIn]
    answer_all: QuestionnaireAnswerValue | None = None
    answers: list[AnswerIn] = Field(default_factory=list)
    rows: list[RowIn] = Field(default_factory=list)
    points: list[PointIn] = Field(default_factory=list)
    proposals: list[ProposalIn] = Field(default_factory=list)
    expect: dict[GuideTopicKind, TopicExpect]
    # per topic, each owner's facts to check (a subset of GuideTopicOwner's fields)
    owner_facts: dict[GuideTopicKind, list[dict[str, object]]] = Field(default_factory=dict)
    household_rows: dict[GuideTopicKind, dict[str, int]] = Field(default_factory=dict)

    def owner_ids(self) -> list[UUID]:
        return [ident(o.id) for o in self.owners]

    def answer_rows(self) -> list[QuestionnaireAnswer]:
        """The given answers, and every other slot answered `answer_all` when set."""
        questionnaire = registry.questionnaire(TAX_YEAR)
        given = {(a.key, a.owner): a for a in self.answers}
        for key, _ in given:
            if questionnaire.question(key) is None:
                raise ValueError(f"{key}: no question of the 2026 questionnaire")
        out: list[QuestionnaireAnswer] = []
        for question, owner, prop in slots(questionnaire, self.owner_ids(), []):
            if prop is not None:
                continue
            label = next((o.id for o in self.owners if ident(o.id) == owner), None)
            mine = given.get((question.key, label))
            if mine is not None:
                answer, choice = mine.answer, mine.choice
            else:
                answer, choice = self.answer_all, None
            if answer is None:
                continue
            out.append(
                QuestionnaireAnswer(
                    id=ident(f"answer:{question.key}:{label}"),
                    workspace_id=WORKSPACE,
                    recorded_at=RECORDED,
                    question_key=question.key,
                    tax_year=TAX_YEAR,
                    owner_id=owner,
                    answer=answer,
                    choice=choice,
                )
            )
        return out

    def guide_points(self) -> list[GuidePoint]:
        out = []
        for p in self.points:
            proposal = None
            if p.proposal is not None:
                proposal = BookedProposal.model_construct(
                    group=p.proposal,
                    owner_id=None if p.owner is None else ident(p.owner),
                    blocking=p.blocking,
                )
            out.append(
                GuidePoint(
                    step=p.step,
                    form="anlage_n" if p.owner else "return",
                    code=p.code,
                    owner_id=None if p.owner is None else ident(p.owner),
                    refused=p.refused,
                    blocking=p.blocking,
                    proposal=proposal,
                )
            )
        return out

    def booked(self) -> list[BookedProposal]:
        return [
            BookedProposal.model_construct(
                group=p.group,
                owner_id=None if p.owner is None else ident(p.owner),
                blocking=p.blocking,
            )
            for p in self.proposals
        ]


class AngabenTopicsFixture(Schema):
    cases: list[Case]
