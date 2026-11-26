"""GET /tax/{tax_year}/guide (phase 10 §7.6, D290): Steuern as one guided path of four steps
(five until §7.8 (b), D294, folded the Jahresfragen into Eure Angaben and the Immobilie steps).

A composition of reads that exist already (the Anlage V and Anlage N drafts, the document
checklists, the questionnaire's progress and the whole-return estimate); it computes no tax
figure of its own and stores nothing. Each form's result and the estimate's result are copied
from their drafts as they are.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from .booked_inputs import BookedProposal
from .checklist import DocumentChecklistItem
from .common import Money, Schema, TaxYear
from .employment import QuestionTopic
from .tax import DraftWarning

GuideStepKind = Literal["angaben", "property", "documents", "review"]
# done: „erledigt“ (the review step: ready to file, only when every other step is done);
# open: „N offen“; waiting: „ab <Monat>“, or „noch nicht“ without a month (the review step
# while another step is not done).
GuideStepState = Literal["done", "open", "waiting"]
# Who sends a Beleg; the frontend holds the words.
GuideSender = Literal[
    "employer",
    "bank",
    "management",
    "municipality",
    "notary",
    "issuer",
    "lease_parties",
    "tenant",
]
GuideForm = Literal["anlage_v", "anlage_n", "return"]


class GuideStep(Schema):
    kind: GuideStepKind
    property_id: UUID | None = None  # the Immobilie step's property
    state: GuideStepState
    open: int = Field(ge=0)  # what is to do now
    waiting: int = Field(default=0, ge=0)  # Belege not issued yet
    done: int | None = Field(default=None, ge=0)  # Belege there
    total: int | None = Field(default=None, ge=0)  # Belege expected
    # The first month a waiting Beleg usually comes (never before this month).
    waiting_from: date | None = None


class GuidePoint(Schema):
    """One open point: a draft's to-do warning, an owner without any pay source
    (`anlage_n.no_pay`), an owner whose wage-replacement benefit, announced in the
    Jahresfragen, is not typed yet (`anlage_n.benefit_letter`), a checklist gap that is a
    value to type (price_split, market_rent), a booking that proposes a tax input
    (`return.benefit_booked`, `return.donation_booked`, `return.childcare_booked`,
    `return.services_booked`: `proposal` holds the POST /return-inputs payload and the
    allocations; phase 10 §7.8 (a), D293), a note of a form that carries an action (`note`
    true, the warning's code), or an input the rules refuse (`return_input.refused`, the
    rule's error code in `refused`).

    `blocking` false: listed, but not counted in a step's or a form's open points and never
    holding the year back from „bereit“ (a note; a donation, Kita or §35a proposal, which a
    person may rightly lower or leave out)."""

    step: Literal["angaben", "property", "review"]
    form: GuideForm
    code: str
    property_id: UUID | None = None
    owner_id: UUID | None = None
    warning: DraftWarning | None = None
    item: DocumentChecklistItem | None = None
    proposal: BookedProposal | None = None
    note: bool = False
    refused: str | None = None
    blocking: bool = True
    # The topic of Eure Angaben the point belongs to (rules/angaben_topics.topic_of_point;
    # None: a point of another step, or one no topic lists), so the screen shows it there.
    topic: QuestionTopic | None = None


class GuideDocument(Schema):
    """One Beleg of the Belege step: a checklist item (kind = its requirement code, `item`
    the row) or an owner's Lohnsteuerbescheinigung (kind wage_certificate)."""

    kind: str
    status: Literal["present", "missing", "expected"]
    property_id: UUID | None = None
    owner_id: UUID | None = None
    item: DocumentChecklistItem | None = None
    due_on: date | None = None
    sender: GuideSender | None = None
    # The months it usually comes in (the first day of each); None: no particular month.
    usual_from: date | None = None
    usual_to: date | None = None


class GuideFormResult(Schema):
    """One card of „Prüfen und abgeben“: the form's result as its draft states it (Anlage V:
    totals.result; Anlage N: result = gross − Werbungskosten), None when no draft exists for
    the year (`reason` the draft's error code)."""

    form: Literal["anlage_v", "anlage_n"]
    property_id: UUID | None = None
    owner_id: UUID | None = None
    result: Money | None = None
    status: str | None = None  # Anlage N: certified / estimate / none
    reason: str | None = None
    # Its points, its Belege not there yet and the open Jahresfragen about its owner or property.
    open: int = Field(ge=0)


class GuideEstimate(Schema):
    """The whole-return estimate's result (phase 4b D101): positive a back-payment, negative a
    refund, as GET /tax/return-estimate states it."""

    result: Money
    status: Literal["estimate", "certified"]


# --- The topics of Eure Angaben (phase 10 §7.8 (b), D294, rules/angaben_topics.py) -----------

GuideTopicKind = QuestionTopic
# open: something to answer, type or confirm (a proposal counts, even one that does not hold the
# step); not_needed: every question of the topic answered „nein“ (or with a choice that means
# none) and no row of its kinds in the year; done: otherwise.
GuideTopicState = Literal["done", "open", "not_needed"]


class GuideTopicOwner(Schema):
    """One owner in an owner topic: what is open for them and the facts the topic shows. Only
    the facts of the topic are set; the browser words them and computes nothing."""

    owner_id: UUID
    open: int = Field(ge=0)
    open_questions: list[str] = Field(default_factory=list)  # their unanswered question keys
    rows: dict[str, int] = Field(default_factory=dict)  # the year's rows by input kind
    # couple
    church_tax: bool | None = None
    tax_state: str | None = None  # None in couple: no Bundesland recorded (open)
    tax_class: str | None = None  # the Jahresfragen's choice code
    # pay: the Anlage N's status and the gross pay it counts (None: no draft for the year)
    pay_status: Literal["certified", "estimate", "none"] | None = None
    gross_source: Literal["certificate", "payslip", "expected", "declared_none"] | None = None
    gross: Money | None = None
    # insurance and benefits: the Jahresfragen's choice codes
    insurance_status: str | None = None
    benefit: str | None = None


class GuideTopic(Schema):
    """One topic of Eure Angaben. `open`: its unanswered questions, its blocking points that are
    no proposal and, for couple, each owner without a Bundesland. `proposals`: the bookings that
    propose an input of the topic (§7.8 (a)), blocking or not. `owners`: one per owner in an owner
    topic (couple, pay, work_costs, insurance, benefits); `open_questions` and `rows` the
    household's own in children and household."""

    topic: GuideTopicKind
    state: GuideTopicState
    open: int = Field(ge=0)
    proposals: int = Field(default=0, ge=0)
    questions_open: int = Field(ge=0)
    questions_total: int = Field(ge=0)
    owners: list[GuideTopicOwner] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    rows: dict[str, int] = Field(default_factory=dict)


class TaxGuide(Schema):
    tax_year: TaxYear
    filing_deadline: date
    steps: list[GuideStep]
    steps_done: int = Field(ge=0)
    # „Jetzt dran“: the index of the first step with something to do today; the last step
    # when everything is done; None while only Belege not issued yet are left (waiting_from).
    next_step: int | None = None
    waiting_from: date | None = None
    ready: bool  # every step done: the ELSTER-Ausfüllhilfe can be made without open points
    estimate: GuideEstimate | None = None
    forms: list[GuideFormResult] = Field(default_factory=list)
    points: list[GuidePoint] = Field(default_factory=list)
    documents: list[GuideDocument] = Field(default_factory=list)
    # A source that could not be read for the year, as "<source>:<error code>" (e.g.
    # "estimate:tax.no_rule_set"); the rest of the guide is still served.
    unavailable: list[str] = Field(default_factory=list)
    # Eure Angaben by topic, in order (D294). None: the year's questionnaire could not be read.
    topics: list[GuideTopic] | None = None
