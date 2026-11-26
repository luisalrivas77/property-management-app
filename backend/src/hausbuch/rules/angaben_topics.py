"""Eure Angaben by topic (phase 10 §7.8 (b), D294): the guided tab's seven topics, each with its
state, what is open in it and the facts it shows. Pure: the service (services/tax_guide.py) hands
in the year's questionnaire, its current answers, the owners' facts, the year's return_input rows,
the guide's points (rules/tax_guide.py) and the booked proposals (§7.8 (a)); the browser words the
result and computes nothing.

The topics, in order: couple (Steuerklasse, Kirchensteuer, Bundesland), pay (the
Lohnsteuerbescheinigung or payslips), work_costs (the Werbungskosten), insurance, benefits (the
Lohnersatzleistungen), children, household (§35a, donations, other costs).

Where each thing belongs:
- A question by its `topic` in rules/tax/questionnaire/<year>.yaml (every owner and household
  question names one; a property question none, its slot counts in the property's step).
- A return_input row by TOPIC_KINDS.
- A point of Eure Angaben by POINT_TOPIC (a refused input by REFUSED_TOPIC, a proposal by
  PROPOSAL_TOPIC); tests/rules/test_phase10_angaben_topics.py keeps both maps complete.

The state of a topic:
- open: an unanswered question, a blocking point that is no proposal, a booked proposal (even
  one that does not hold the step: a donation, Kita or §35a proposal is still something to
  confirm), or, for couple, an owner without a Bundesland. So a topic can be open under an
  Eure Angaben step that reads done; the step's count stays the guide's own.
- not_needed: nothing open, every slot of the topic answered „nein“, „trifft nicht zu“ or with a
  choice that means none (NEGATIVE_CHOICES), and no row of its kinds in the year; never for
  couple and pay, which every owner has, unless there is no owner at all.
- done: otherwise.

Kept out of rules/health and rules/plan: services.health.rule_version() hashes those folders, and
this module changes no stored figure.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from hausbuch.rules.tax.questionnaire import _answer_of, slots
from hausbuch.rules.tax_guide import BENEFIT_LETTER_CODE, NO_PAY_CODE, REFUSED_CODE
from hausbuch.schemas.booked_inputs import BookedGroup
from hausbuch.schemas.employment import Questionnaire, QuestionnaireAnswer
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.tax_guide import (
    GuidePoint,
    GuideTopic,
    GuideTopicKind,
    GuideTopicOwner,
    GuideTopicState,
)

K = ReturnInputKind

TOPICS: tuple[GuideTopicKind, ...] = (
    "couple",
    "pay",
    "work_costs",
    "insurance",
    "benefits",
    "children",
    "household",
)
OWNER_TOPICS: frozenset[GuideTopicKind] = frozenset(
    {"couple", "pay", "work_costs", "insurance", "benefits"}
)
# Every owner has these: answered „nein“ they are still done, never „nicht nötig“.
NEVER_NOT_NEEDED: frozenset[GuideTopicKind] = frozenset({"couple", "pay"})

# The topic of each return_input kind; every kind has exactly one.
TOPIC_KINDS: dict[GuideTopicKind, frozenset[ReturnInputKind]] = {
    "couple": frozenset(),
    "pay": frozenset({K.WAGE_CERTIFICATE, K.PAYSLIP, K.NO_PAY}),
    "work_costs": frozenset(
        {
            K.COMMUTE,
            K.HOME_OFFICE,
            K.WORK_ROOM,
            K.WORK_EQUIPMENT,
            K.TELECOM,
            K.PROFESSIONAL_ASSOCIATION,
            K.TRAINING,
            K.OTHER_EXPENSE,
        }
    ),
    "insurance": frozenset({K.OTHER_INSURANCE, K.VORSORGE_OVERRIDE}),
    "benefits": frozenset({K.REPLACEMENT_INCOME}),
    "children": frozenset({K.CHILD, K.CHILDCARE_COST}),
    "household": frozenset({K.HOUSEHOLD_SERVICES, K.SPECIAL_EXPENSE}),
}
TOPIC_OF_KIND: dict[str, GuideTopicKind] = {
    kind.value: topic for topic, kinds in TOPIC_KINDS.items() for kind in kinds
}

# The booked proposals (§7.8 (a)) by group.
PROPOSAL_TOPIC: dict[BookedGroup, GuideTopicKind] = {
    "benefit": "benefits",
    "donation": "household",
    "household_services": "household",
    "childcare": "children",
}

# The topic of each point of Eure Angaben by its code: the to-do warnings and notes whose step is
# Eure Angaben (rules/tax_guide.STEP_OF_WARNING, NOTE_STEP), the guide's own points, and the
# proposal codes (a proposal point is counted as a proposal, never as open).
POINT_TOPIC: dict[str, GuideTopicKind] = {
    NO_PAY_CODE: "pay",
    "anlage_n.no_pay_overruled": "pay",
    BENEFIT_LETTER_CODE: "benefits",
    "return.progression_provisional": "benefits",
    "return.benefit_possible_double": "benefits",
    "return.benefit_booked": "benefits",
    "return.donation_booked": "household",
    "return.services_booked": "household",
    "return.childcare_booked": "children",
    "anlage_n.church_mismatch": "couple",
    "anlage_n.days_implausible": "work_costs",
    "return.kindergeld_apply": "children",
    # notes (never blocking, listed only)
    "anlage_n.work_room_centre": "work_costs",
    "anlage_n.work_room_partial": "work_costs",
    "anlage_n.first_workplace_check": "work_costs",
    "return.children_mismatch": "children",
    "return.child_over_18": "children",
    "return.childcare_until_14": "children",
}
# An input the rules refuse (return_input.refused) by the refusal's code.
REFUSED_TOPIC: dict[str, GuideTopicKind] = {
    "anlage_n.useful_life_missing": "work_costs",  # a work item without its useful life
    "anlage_n.work_room_excludes_day_rate": "work_costs",
    "return_input.scope_invalid": "children",  # a childcare cost naming a child not recorded
    "return.childcare_age": "children",
    "return.household_services_cash": "household",
}

# A choice answer that means „none“: the topic can be not_needed with it.
NEGATIVE_CHOICES: dict[str, frozenset[str]] = {
    "owner.replacement_income": frozenset({"none"}),
    "owner.insurance_status": frozenset({"statutory"}),
}
# The questions whose choice an owner topic shows.
CHOICE_FACT: dict[str, str] = {
    "owner.tax_class": "tax_class",
    "owner.insurance_status": "insurance_status",
    "owner.replacement_income": "benefit",
}


@dataclass(frozen=True)
class TopicOwner:
    """One owner: the owner record's church tax and Bundesland (None: not recorded), and the
    Anlage N draft's status, gross source and gross (all None: no draft for the year)."""

    owner_id: UUID
    church_tax: bool
    tax_state: str | None
    status: Literal["certified", "estimate", "none"] | None
    gross_source: Literal["certificate", "payslip", "expected", "declared_none"] | None
    gross: Decimal | None


class _Row(Protocol):
    @property
    def kind(self) -> object: ...
    @property
    def owner_id(self) -> UUID | None: ...


class _Proposal(Protocol):
    @property
    def group(self) -> BookedGroup: ...
    @property
    def owner_id(self) -> UUID | None: ...


def topic_of_point(point: GuidePoint) -> GuideTopicKind | None:
    if point.code == REFUSED_CODE:
        return REFUSED_TOPIC.get(point.refused or "")
    return POINT_TOPIC.get(point.code)


def _negative(key: str, answer: QuestionnaireAnswer) -> bool:
    if answer.answer in ("no", "not_applicable"):
        return True
    return answer.choice is not None and answer.choice in NEGATIVE_CHOICES.get(key, ())


def _kind(row: _Row) -> str:
    kind = row.kind
    return str(getattr(kind, "value", kind))


def topics(
    questionnaire: Questionnaire,
    owners: Sequence[TopicOwner],
    answers: Sequence[QuestionnaireAnswer],
    rows: Sequence[_Row],
    points: Sequence[GuidePoint],
    proposals: Sequence[_Proposal] | None,
) -> list[GuideTopic]:
    """The seven topics in order. `answers` are the year's current answers, `rows` the year's
    current return_input rows (owner and household), `points` the guide's points (only those of
    Eure Angaben count), `proposals` the booked proposals (None: not read)."""
    owner_ids = [o.owner_id for o in owners]
    all_slots = slots(questionnaire, owner_ids, [])
    row_counts: Counter[tuple[str, UUID | None]] = Counter((_kind(r), r.owner_id) for r in rows)
    angaben = [p for p in points if p.step == "angaben"]
    out: list[GuideTopic] = []
    for topic in TOPICS:
        # In questionnaire order; a property question has no topic.
        my_slots = [(q, o) for q, o, _ in all_slots if q.topic == topic]
        answered = [(q.key, o, _answer_of(answers, q.key, o, None)) for q, o in my_slots]
        open_slots = [(key, o) for key, o, a in answered if a is None]
        my_points = [
            p for p in angaben if p.blocking and p.proposal is None and topic_of_point(p) == topic
        ]
        my_proposals = [p for p in (proposals or ()) if PROPOSAL_TOPIC.get(p.group) == topic]
        kinds = {k.value for k in TOPIC_KINDS[topic]}

        def rows_of(owner_id: UUID | None, kinds=kinds) -> dict[str, int]:
            return {
                kind: n
                for (kind, o), n in sorted(row_counts.items(), key=lambda kv: kv[0][0])
                if kind in kinds and o == owner_id
            }

        has_rows = any(kind in kinds for kind, _ in row_counts)
        owner_facts: list[GuideTopicOwner] = []
        if topic in OWNER_TOPICS:
            for owner in owners:
                keys = [key for key, o in open_slots if o == owner.owner_id]
                n = len(keys) + sum(1 for p in my_points if p.owner_id == owner.owner_id)
                facts: dict[str, object] = {}
                if topic == "couple":
                    n += owner.tax_state is None
                    facts.update(church_tax=owner.church_tax, tax_state=owner.tax_state)
                if topic == "pay":
                    facts.update(
                        pay_status=owner.status, gross_source=owner.gross_source, gross=owner.gross
                    )
                for key, o, answer in answered:
                    fact = CHOICE_FACT.get(key)
                    if fact and o == owner.owner_id and answer is not None:
                        facts[fact] = answer.choice
                owner_facts.append(
                    GuideTopicOwner(
                        owner_id=owner.owner_id,
                        open=n,
                        open_questions=keys,
                        rows=rows_of(owner.owner_id),
                        **facts,
                    )
                )
        no_state = sum(o.tax_state is None for o in owners) if topic == "couple" else 0
        open_count = len(open_slots) + len(my_points) + no_state
        state: GuideTopicState
        if open_count or my_proposals:
            state = "open"
        elif not has_rows and (
            not my_slots
            or (
                topic not in NEVER_NOT_NEEDED
                and all(a is not None and _negative(key, a) for key, _, a in answered)
            )
        ):
            state = "not_needed"
        else:
            state = "done"
        out.append(
            GuideTopic(
                topic=topic,
                state=state,
                open=open_count,
                proposals=len(my_proposals),
                questions_open=len(open_slots),
                questions_total=len(my_slots),
                owners=owner_facts,
                open_questions=[key for key, o in open_slots if o is None],
                rows=rows_of(None),
            )
        )
    return out
