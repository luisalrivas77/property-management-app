"""The plan's explanation: facts with slots, the model's output, the validator's verdict and
the stored explanation (docs/phases/phase-9.md §8, §9, §10, §11, §12; migration 0048).

Phase 9 step 0 writes the shapes; step 4 fills them (rules/advice/, services/advice.py). The
model writes sentences with slots and code writes every figure and every name (D252). The
request carries figures, kinds, classes and opaque keys only: the labels travel beside the
facts and are excluded from every dump, so they can never reach a request (D253).
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Schema
from .enums import AccountKind
from .household import NetWorthClass
from .plan import PlanClass

ADVICE_MODE_INVALID = "advice.mode_invalid"


class AdviceMode(StrEnum):
    """workspace.advice_mode (D251)."""

    OWN_USE = "own_use"
    GENERIC = "generic"


class FigureType(StrEnum):
    MONEY = "money"  # whole euros on screen
    PERCENT = "percent"  # a ratio, shown with one decimal of a percent
    MONTHS = "months"  # one decimal
    MONTH = "month"  # "2027-05", shown as „Mai 2027“
    COUNT = "count"
    # Added in step 4a: `debt_to_income` and its limits are a multiple („3,2 mal so hoch“, as
    # rules/health/bands.py formats them); none of the five types of §8.1 renders one.
    MULTIPLE = "multiple"


# --- Facts (D252–D254, §8.1) ------------------------------------------------------------

# The closed list of figure keys (§8.1). Per path `path_<key>_monthly` and
# `path_<key>_reached`, the confirmed limits `band_<figure>_<green|red>`.
FIGURE_KEYS = frozenset(
    {
        "net_worth",
        "net_worth_change",
        "savings_rate",
        "savings_rate_regular",
        "saved_total",
        "principal_total",
        "reserve",
        "reserve_months",
        "monthly_need",
        "reserve_target",
        "reserve_target_months",
        "reserve_gap",
        "free_cash",
        "free_cash_median",
        "free_cash_lowest",
        "buffer",
        "investable",
        "already_invested",
        "investable_left",
        "ltv",
        "debt_ratio",
        "debt_service_ratio",
        "debt_to_income",
        "share_bank",
        "share_depot",
        "share_crypto",
        "share_property",
        "basis_months",
    }
)
# The projection's keys (§8.1): allowed only in a `projection` paragraph (check 12).
PROJECTION_KEYS = frozenset(
    {
        "assumed_change_depot",
        "assumed_change_crypto",
        "flat_value_growth",
        "projection_until",
        "projection_total",
        "projection_change",
        "projection_assumption_part",
    }
)
TYPED_RATE_KEYS = frozenset({"assumed_change_depot", "assumed_change_crypto"})
_PATH_KEY = re.compile(r"^path_(reserve_first|as_is|pause_p[0-9]+)_(monthly|reached)$")
_BAND_KEY = re.compile(
    r"^band_(reserve|savings_rate|ltv|debt_service|debt_ratio|debt_to_income)_(green|red)$"
)


def known_figure_key(key: str) -> bool:
    return (
        key in FIGURE_KEYS
        or key in PROJECTION_KEYS
        or bool(_PATH_KEY.match(key) or _BAND_KEY.match(key))
    )


FactKey = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]
AccountKey = Annotated[str, Field(pattern=r"^k[0-9]{1,4}$")]
PlanSlotKey = Annotated[str, Field(pattern=r"^p[0-9]{1,4}$")]
AssetKey = Annotated[str, Field(pattern=r"^a[0-9]{1,4}$")]

_VALUE_FORMS = {
    FigureType.MONEY: re.compile(r"^-?[0-9]+\.[0-9]{2}$"),
    FigureType.PERCENT: re.compile(r"^-?[0-9]+(\.[0-9]{1,4})?$"),
    # signed since step 4a: the reserve in months is negative when the cards' debt exceeds it
    FigureType.MONTHS: re.compile(r"^-?[0-9]+(\.[0-9])?$"),
    FigureType.MONTH: re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
    FigureType.COUNT: re.compile(r"^[0-9]+$"),
    FigureType.MULTIPLE: re.compile(r"^-?[0-9]+(\.[0-9]{1,2})?$"),
}


class Fact(Schema):
    """One figure: key, type and value as a string (a decimal string, or a month)."""

    key: FactKey
    type: FigureType
    value: str

    @model_validator(mode="after")
    def _value(self) -> Self:
        if not known_figure_key(self.key):
            raise ValueError(f"{self.key}: not in the closed list of figure keys (§8.1)")
        if not _VALUE_FORMS[self.type].match(self.value):
            raise ValueError(f"{self.key}: not a {self.type} value")
        if self.type is not FigureType.MONTH:
            try:
                Decimal(self.value)
            except InvalidOperation as error:  # pragma: no cover - the pattern refuses it
                raise ValueError(f"{self.key}: not a decimal") from error
        return self


class StateValue(StrEnum):
    BELOW = "below"
    REACHED = "reached"
    POSITIVE = "positive"
    ZERO = "zero"
    NEGATIVE = "negative"
    FULL = "full"
    SHORT = "short"
    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    SHOWN = "shown"
    ABSENT = "absent"
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    NONE = "none"


S = StateValue
_LIGHT = frozenset({S.GREEN, S.AMBER, S.RED, S.NONE})
STATES: dict[str, frozenset[StateValue]] = {
    "reserve_state": frozenset({S.BELOW, S.REACHED}),
    "investable_state": frozenset({S.POSITIVE, S.ZERO}),
    "left_state": frozenset({S.POSITIVE, S.NEGATIVE}),
    "basis_state": frozenset({S.FULL, S.SHORT}),
    "trend_state": frozenset({S.UP, S.DOWN, S.FLAT}),
    "projection_state": frozenset({S.SHOWN, S.ABSENT}),
    "light_reserve": _LIGHT,
    "light_savings_rate": _LIGHT,
    "light_ltv": _LIGHT,
    "light_debt_service_ratio": _LIGHT,
    "light_debt_ratio": _LIGHT,
    "light_debt_to_income": _LIGHT,
}


class ExplainLabels(Schema):
    """key → stored label, for rendering only (§8.1). Never serialised into a request."""

    accounts: dict[AccountKey, str] = Field(default_factory=dict)
    plans: dict[PlanSlotKey, str] = Field(default_factory=dict)
    assets: dict[AssetKey, str] = Field(default_factory=dict)


class ExplainFacts(Schema):
    """The request's facts (§8.1). `labels` is excluded from every dump: the request is
    `model_dump_json()`, and the labels are stored separately in plan_explanation.labels.
    The facts' hash is `plan_explanation.facts_sha256`, computed over that dump."""

    mode: AdviceMode
    as_of: date
    figures: list[Fact] = Field(default_factory=list)
    states: dict[str, StateValue] = Field(default_factory=dict)
    accounts: dict[AccountKey, AccountKind] = Field(default_factory=dict)
    plans: dict[PlanSlotKey, PlanClass] = Field(default_factory=dict)
    assets: dict[AssetKey, NetWorthClass] = Field(default_factory=dict)
    classes: list[NetWorthClass] = Field(default_factory=lambda: list(NetWorthClass))
    labels: ExplainLabels = Field(default_factory=ExplainLabels, exclude=True)

    def figure(self, key: str) -> Fact | None:
        return next((f for f in self.figures if f.key == key), None)

    @model_validator(mode="after")
    def _closed(self) -> Self:
        keys = [f.key for f in self.figures]
        if len(keys) != len(set(keys)):
            raise ValueError("a figure key once")
        for name, value in self.states.items():
            if name not in STATES or value not in STATES[name]:
                raise ValueError(f"state {name} = {value} is not in the closed list")
        if self.mode is AdviceMode.GENERIC and (self.accounts or self.plans or self.assets):
            raise ValueError(f"{ADVICE_MODE_INVALID}: generic names no account, plan or asset")
        for key, held in self.assets.items():
            if held not in (NetWorthClass.BANK, NetWorthClass.DEPOT, NetWorthClass.CRYPTO):
                raise ValueError(f"{key}: an asset's class is bank, depot or crypto")
        labels = self.labels
        for slots, named in (
            (self.accounts, labels.accounts),
            (self.plans, labels.plans),
            (self.assets, labels.assets),
        ):
            if not set(named) <= set(slots):
                raise ValueError("a label for a key the facts do not hold")
        return self


# --- The model's output (§8.3) ----------------------------------------------------------


class Topic(StrEnum):
    SURPLUS = "surplus"
    RESERVE = "reserve"
    PATHS = "paths"
    SAVINGS_RATE = "savings_rate"
    DEBT = "debt"
    NET_WORTH = "net_worth"
    BASIS = "basis"
    PROJECTION = "projection"  # only while projection_state = shown


PARAGRAPH_MAX = 500
PARAGRAPHS_MAX = 4


class Paragraph(Schema):
    """A paragraph as the validator sees it: unbounded here, so that check 11 (shape) can
    reject a long one with its code rather than a parse error."""

    topic: Topic
    text: str


class ExplainOutput(Schema):
    """The model's structured output: 1 to 4 paragraphs, each at most 500 characters. The
    slot grammar is the validator's (§9), not the schema's."""

    paragraphs: list[Paragraph] = Field(min_length=1, max_length=PARAGRAPHS_MAX)

    @model_validator(mode="after")
    def _lengths(self) -> Self:
        if any(len(p.text) > PARAGRAPH_MAX for p in self.paragraphs):
            raise ValueError(f"a paragraph holds at most {PARAGRAPH_MAX} characters")
        return self


# --- The validator (§9) -----------------------------------------------------------------


class ViolationCode(StrEnum):
    """One code per check of §9.1, in its order."""

    CHARACTER = "character"  # 1
    SLOT = "slot"  # 2
    DIGIT = "digit"  # 3
    NUMBER_WORD = "number_word"  # 4
    ABBREVIATION = "abbreviation"  # 5
    LINK = "link"  # 6
    LEXICON = "lexicon"  # 7
    VOCABULARY = "vocabulary"  # 8
    PRODUCT_NOUN = "product_noun"  # 8b
    RECOMMENDATION = "recommendation"  # 9
    CONTRADICTION = "contradiction"  # 10
    SHAPE = "shape"  # 11
    PROJECTION = "projection"  # 12
    MARK = "mark"  # 13


class Violation(Schema):
    """Code and position, never the text (§11.4: the text is in ai_call.output)."""

    code: ViolationCode
    paragraph: int = Field(ge=0)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class ValidationResult(Schema):
    violations: list[Violation] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


# --- The stored explanation (D257, §11.4) -----------------------------------------------


class ExplanationStatus(StrEnum):
    MODEL = "model"
    TEMPLATE = "template"


class ExplanationReason(StrEnum):
    """Why the template text was used (plan_explanation.reason)."""

    REJECTED = "rejected"
    REFUSED = "refused"
    CONSENT_OFF = "consent_off"
    LIMIT = "limit"
    ERROR = "error"


class RenderedParagraph(Schema):
    topic: Topic
    text: str


class PlanExplanation(Schema):
    """GET /household-plan/explanation (§8.6): rendered from its own stored facts, with
    `stale` when today's facts differ from them. With no row stored for the scenario and mode,
    the template text rendered on read from today's facts: `id` and `created_at` are null,
    `stale` is false, and `reason` is `consent_off` while ai_private_accounts is off (no
    request is built then, D253), else null (none requested yet)."""

    id: UUID | None = None  # null: not stored, the template on read
    scenario_id: UUID | None = None
    mode: AdviceMode
    status: ExplanationStatus
    reason: ExplanationReason | None = None
    paragraphs: list[RenderedParagraph] = Field(default_factory=list)
    created_at: datetime | None = None
    stale: bool = False

    @model_validator(mode="after")
    def _reason(self) -> Self:
        if (self.id is None) != (self.created_at is None):
            raise ValueError("a stored explanation has an id and a date, one on read neither")
        if self.id is None:
            if self.status is not ExplanationStatus.TEMPLATE:
                raise ValueError("only the template text is rendered on read")
            if self.reason not in (None, ExplanationReason.CONSENT_OFF):
                raise ValueError("a text on read is the template for want of consent or a row")
        elif (self.status is ExplanationStatus.TEMPLATE) != (self.reason is not None):
            raise ValueError("a template text says why; a model text has no reason")
        return self


class PlanExplanationRequest(Schema):
    """POST /household-plan/explanation („Neu erklären“): the scenario, else the default."""

    scenario: UUID | None = None


class PlanExplanationQueued(Schema):
    """POST /household-plan/explanation, 202: whether explain_plan was queued. Never while
    ai_private_accounts is off (D253): the template text is then rendered on read."""

    scenario_id: UUID | None = None
    queued: bool
