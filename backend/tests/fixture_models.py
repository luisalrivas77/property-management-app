"""Shapes of the YAML rule fixtures, built from the spec schemas.

Loading a fixture through these models is itself a test (tests/test_fixtures_valid.py): a
fixture that does not match the schemas fails before any rule is implemented.
"""

from datetime import date
from functools import cache
from pathlib import Path
from typing import Self
from uuid import UUID

import yaml
from pydantic import Field, model_validator

from hausbuch.schemas.checklist import ChecklistFacts, ChecklistItem
from hausbuch.schemas.common import Fraction, Schema, TaxYear
from hausbuch.schemas.document import (
    DocumentRetentionInput,
    RetentionPolicy,
    RetentionResult,
    TenantRetentionInput,
)
from hausbuch.schemas.enums import TenancyRole
from hausbuch.schemas.events import Event, EventRef, TimelineEntry, TimelineScope
from hausbuch.schemas.loan import LoanTerms, ScheduleEntry, YearTotals
from hausbuch.schemas.tenancy import RentScheduleEntry

FIXTURES = Path(__file__).parent / "fixtures" / "phase1"


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class Lenient(Schema):
    """Fixture wrappers: strict about fields, but documentation keys are allowed."""

    id: str
    description: str | None = None


# --- loan_schedule.yaml ------------------------------------------------------


class RuleErrorExpect(Schema):
    code: str
    at_number: int | None = None


class LoanExpect(Schema):
    entry_count: int | None = None
    entries: list[ScheduleEntry] = Field(default_factory=list)
    years: list[YearTotals] = Field(default_factory=list)
    error: RuleErrorExpect | None = None

    @model_validator(mode="after")
    def _either(self) -> Self:
        if (self.error is None) == (self.entry_count is None):
            raise ValueError("a case expects either a schedule (entry_count) or an error")
        return self


class LoanCase(Lenient):
    terms: LoanTerms
    expect: LoanExpect


class LoanFixture(Schema):
    cases: list[LoanCase]


# --- rent_at.yaml ------------------------------------------------------------


class RentCase(Lenient):
    at: date
    expect: UUID | None


class RentTieBreak(Schema):
    rows: list[dict]
    cases: list[RentCase]


class RentFixture(Schema):
    tenancy_id: UUID
    workspace_id: UUID
    rows: list[dict]
    cases: list[RentCase]
    tie_break: RentTieBreak

    def entries(self, rows: list[dict]) -> list[RentScheduleEntry]:
        ctx = {"tenancy_id": self.tenancy_id, "workspace_id": self.workspace_id}
        return [RentScheduleEntry.model_validate(ctx | r) for r in rows]


# --- timeline.yaml -----------------------------------------------------------


class TimelineExpect(Schema):
    """TimelineEntry minus payload (the payload must equal the event_id event's payload)."""

    event_id: UUID
    root_event_id: UUID
    type: str
    entity_type: str
    entity_id: UUID
    effective_date: date
    corrected: bool
    original_effective_date: date | None = None


class TimelineCase(Lenient):
    scope: TimelineScope
    expect: list[TimelineExpect]


class TimelineFixture(Schema):
    workspace_id: UUID
    ids: dict[str, UUID]
    events: list[Event]
    cases: list[TimelineCase]

    @model_validator(mode="after")
    def _expectations_are_real_entries(self) -> Self:
        by_id = {e.id: e for e in self.events}
        for case in self.cases:
            for exp in case.expect:
                TimelineEntry.model_validate(
                    exp.model_dump() | {"payload": by_id[exp.event_id].payload}
                )
        return self


# --- retention.yaml ----------------------------------------------------------


class RetentionCase(Lenient):
    policy: RetentionPolicy = Field(default_factory=RetentionPolicy)
    input: DocumentRetentionInput
    expect: RetentionResult


class TenantRetentionCase(Lenient):
    policy: RetentionPolicy = Field(default_factory=RetentionPolicy)
    input: TenantRetentionInput
    expect: RetentionResult


class RetentionFixture(Schema):
    default_policy: RetentionPolicy
    cases: list[RetentionCase]
    tenant_cases: list[TenantRetentionCase]


# --- iban.yaml ---------------------------------------------------------------


class IbanExpect(Schema):
    normalized: str | None = None
    masked: str | None = None
    hmac_hex: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _either(self) -> Self:
        ok = None not in (self.normalized, self.masked, self.hmac_hex)
        if ok == (self.error is not None):
            raise ValueError("expect either normalized/masked/hmac_hex or an error")
        return self


class IbanCase(Lenient):
    raw: str
    expect: IbanExpect


class IbanFixture(Schema):
    hmac_key_utf8: str
    cases: list[IbanCase]


# --- validation.yaml ---------------------------------------------------------


class SharesCase(Lenient):
    shares: list[Fraction]
    expect: list[str]


class PartiesCase(Lenient):
    roles: list[TenancyRole]
    expect: list[str]


class RentRowCase(Lenient):
    tenancy_start: date
    tenancy_end: date | None
    effective_from: date
    expect: list[str]


class CorrectionCase(Lenient):
    original: EventRef
    correction: EventRef
    expect: list[str]


class ValidationFixture(Schema):
    ownership_shares: list[SharesCase]
    tenancy_parties: list[PartiesCase]
    rent_rows: list[RentRowCase]
    corrections: list[CorrectionCase]


# --- document_checklist.yaml ---------------------------------------------------


class ChecklistCase(Lenient):
    tax_year: TaxYear
    today: date
    facts: ChecklistFacts
    expect: list[ChecklistItem]


class ChecklistFixture(Schema):
    ids: dict[str, UUID]
    bases: dict[str, dict]  # YAML anchors merged into the cases' facts
    cases: list[ChecklistCase]


MODELS: dict[str, type[Schema]] = {
    "loan_schedule": LoanFixture,
    "rent_at": RentFixture,
    "timeline": TimelineFixture,
    "retention": RetentionFixture,
    "iban": IbanFixture,
    "validation": ValidationFixture,
    "document_checklist": ChecklistFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
