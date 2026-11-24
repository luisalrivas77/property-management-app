"""Recurring payments and single payments marked one-off (docs/phases/phase-10.md §7.9 (b),
(c), (f); D296, D297).

Every figure is computed on read by rules/recurring from the confirmed private lines; only a
person's answers are stored, one insert-only `spending_mark` row each (migration 0056), the
newest answer per target counting. Amounts are what was paid, positive.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Money, NonNegativeMoney, Schema
from .health import GroupPayment, Window, YearMonth


class RecurringInterval(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RecurringState(StrEnum):
    """`active`: at most one interval without a payment; `maybe_ended`: two („Vielleicht
    beendet“). Three or more: the series is left out of every list."""

    ACTIVE = "active"
    MAYBE_ENDED = "maybe_ended"


class SpendingMarkAnswer(StrEnum):
    """A person's answer (§7.9 (f)): `one_off` and `regular` on a single payment (an
    allocation, (c)); `hidden` („kein Abo“) and `shown` („Wieder anzeigen“) on a series (its
    payee key and interval, (b))."""

    ONE_OFF = "one_off"
    REGULAR = "regular"
    HIDDEN = "hidden"
    SHOWN = "shown"


ALLOCATION_ANSWERS = frozenset({SpendingMarkAnswer.ONE_OFF, SpendingMarkAnswer.REGULAR})
SERIES_ANSWERS = frozenset({SpendingMarkAnswer.HIDDEN, SpendingMarkAnswer.SHOWN})

# A series' payee key (rules.matching.payee_key): upper-case words, never empty.
SeriesKey = Annotated[str, Field(min_length=1, max_length=500)]


class RecurringSeries(Schema):
    """One payee paid at one interval (D296). `change` is the newest amount less the one the
    series held before, with `since` the month of the first payment at the new amount; both
    None without a price change. `sources` are the allocations behind it, oldest first;
    `lines` the same payments for the panel (filled by the service)."""

    key: SeriesKey
    label: str
    interval: RecurringInterval
    state: RecurringState
    last_amount: NonNegativeMoney
    last_on: date
    yearly_total: NonNegativeMoney
    change: Money | None = None
    since: YearMonth | None = None
    payments: int = Field(ge=2)
    sources: list[UUID] = Field(default_factory=list)
    lines: list[GroupPayment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _change(self) -> Self:
        if (self.change is None) != (self.since is None):
            raise ValueError("a price change carries its month, and only a change does")
        return self


class RecurringPayments(Schema):
    """GET /household/recurring (§7.9 (b) 5). `series`: the shown ones, price changes first;
    `hidden`: the ones a person marked „kein Abo“. `count` and `yearly_total` are over the
    shown active series (a series that may have ended is not counted). `yearly_detectable`
    is false while the history is shorter than 350 days: no yearly series can be found yet."""

    as_of: date
    history_start: date | None = None
    yearly_detectable: bool
    count: int = Field(ge=0)
    yearly_total: NonNegativeMoney
    series: list[RecurringSeries] = Field(default_factory=list)
    hidden: list[RecurringSeries] = Field(default_factory=list)


class OneOffCandidate(Schema):
    """One payment that may be one-off (D297): its line, what was paid (`payment.amount`,
    positive), its code's median month over the window, and the newest answer when it has
    one (None for a candidate)."""

    payment: GroupPayment
    code_median: Money  # negative only for a code whose refunds outweigh it most months
    answer: SpendingMarkAnswer | None = None


class OneOffCandidates(Schema):
    """GET /household/one-off-candidates (§7.9 (c) 1): the payments not answered yet that
    may be one-off, and the window's payments a person answered, with their newest answer,
    so an answer can be changed. Nothing while the window has fewer than three closed
    months."""

    as_of: date
    window: Window
    candidates: list[OneOffCandidate] = Field(default_factory=list)
    answered: list[OneOffCandidate] = Field(default_factory=list)


class OneOffMarkRequest(Schema):
    """POST /household/one-off-marks: a person's answer on one confirmed private payment."""

    allocation_id: UUID
    answer: SpendingMarkAnswer

    @model_validator(mode="after")
    def _answer(self) -> Self:
        if self.answer not in ALLOCATION_ANSWERS:
            raise ValueError("a payment is answered one_off or regular")
        return self


class SeriesMarkRequest(Schema):
    """POST /household/recurring-marks: „kein Abo“ (`hidden`) or „Wieder anzeigen“
    (`shown`) on a series, by its payee key and interval."""

    key: SeriesKey
    interval: RecurringInterval
    answer: SpendingMarkAnswer

    @model_validator(mode="after")
    def _answer(self) -> Self:
        if self.answer not in SERIES_ANSWERS:
            raise ValueError("a series is answered hidden or shown")
        return self


class SpendingMarkRecorded(Schema):
    """The answer as stored: the row's id and when."""

    id: UUID
    answer: SpendingMarkAnswer
    created_at: datetime
