"""GET /tax/{tax_year}/guide (phase 10 §7.6, D290): Steuern as one guided path of five steps.

A composition of reads that exist already (the Anlage V and Anlage N drafts, the document
checklists, the questionnaire's progress and the whole-return estimate); it computes no tax
figure of its own and stores nothing. Each form's result and the estimate's result are copied
from their drafts as they are.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from .checklist import DocumentChecklistItem
from .common import Money, Schema, TaxYear
from .tax import DraftWarning

GuideStepKind = Literal["angaben", "property", "questions", "documents", "review"]
# done: „erledigt“ (the review step: ready to file); open: „N offen“; waiting: „ab <Monat>“.
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
    done: int | None = Field(default=None, ge=0)  # questions answered, Belege there
    total: int | None = Field(default=None, ge=0)  # question slots, Belege expected
    # The first month a waiting Beleg usually comes (never before this month).
    waiting_from: date | None = None


class GuidePoint(Schema):
    """One open point: a draft's to-do warning, an owner without any pay source
    (`anlage_n.no_pay`), or a checklist gap that is a value to type (price_split,
    market_rent)."""

    step: Literal["angaben", "property", "review"]
    form: GuideForm
    code: str
    property_id: UUID | None = None
    owner_id: UUID | None = None
    warning: DraftWarning | None = None
    item: DocumentChecklistItem | None = None


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
    open: int = Field(ge=0)


class GuideEstimate(Schema):
    """The whole-return estimate's result (phase 4b D101): positive a back-payment, negative a
    refund, as GET /tax/return-estimate states it."""

    result: Money
    status: Literal["estimate", "certified"]


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
