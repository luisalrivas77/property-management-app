"""Pre-fill proposals (Vorschläge): what a document would create or patch, value by value.

Spec: docs/phases/phase-2.md §4.2, §6. A proposal is built by pure code
(rules.prefill.build_proposal) from an extraction and the workspace's existing records, and
nothing is written to a domain table until the user applies it (D14). Proposal values are
personal data at rest: `fields` and `decisions` are nulled 90 days after the decision and at
once by tenant erasure (D19).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .ai import ClassifyOutput, ExtractedValue
from .common import Fraction, MutableRecord, NonNegativeMoney, PositiveMoney, Schema, Year
from .enums import (
    AcquisitionCostKind,
    EntityType,
    ExtractionKind,
    PropertyKind,
    ProposalStatus,
    ProposalTargetMode,
)
from .property import Address

RecordEntity = (
    EntityType
    | Literal[
        "acquisition_cost",
        "price_split",
        "rent_schedule",
        "typing",
        "link",
        "ownership",
        "deposit_received",  # Kautionsnachweis (step 2): tenancy.deposit_received via the service
        "tax_input",  # phase 3 §9: ruecklage_spent / interest_certified via services/tax_inputs
        "return_input",  # phase 4b §8.2: wage_certificate / payslip via services/return_inputs
        "income_input",  # phase 4b §8.4 (D129): bescheid_zve via services/income_inputs
    ]
)


class ProposalRecord(Schema):
    """One record the proposal would create or patch.

    key: "property", "unit", "cost:notary", "loan", "tenant:1", "tenancy", "rent", "typing",
      "price_split", "ownership", "link:property", "correction:notarized_on", ...
    fields: by the *Create / *Patch field name; a nested field is dotted ("address.city").
      Values the apply step supplies itself (the document as Beleg or evidence, the ids of
      records created in the same apply, a split's source) are not fields.
    current: the existing record's values of the same fields, when target_id is set.
    """

    key: str = Field(min_length=1)
    entity_type: RecordEntity
    mode: Literal["create", "patch", "correction", "unchanged"]
    target_id: UUID | None = None
    fields: dict[str, ExtractedValue] = Field(default_factory=dict)
    current: dict[str, JsonValue] | None = None
    notes: list[str] = Field(default_factory=list)  # e.g. the Übergang clause, quoted

    @model_validator(mode="after")
    def _target(self) -> Self:
        if self.mode in ("patch", "correction", "unchanged") and self.target_id is None:
            raise ValueError(f"mode {self.mode} needs target_id")
        if (self.current is not None) and self.target_id is None:
            raise ValueError("current is only set for an existing record")
        return self


class Proposal(MutableRecord):
    """Read shape. kind is null until the document is classified (the row is created
    `queued` at upload); records is empty until `ready`, and again after the D19 scrub."""

    document_id: UUID
    kind: ExtractionKind | None
    status: ProposalStatus
    error_code: str | None
    target_mode: ProposalTargetMode
    contains_tenant_data: bool = False  # D16: set at upload or by the classifier; sticky
    records: list[ProposalRecord] = Field(default_factory=list)
    extraction_id: UUID | None
    ai_call_id: UUID | None
    decided_at: datetime | None


class FieldDecision(StrEnum):
    """What the user did with one proposed value. Recorded per field on apply, without the
    value (proposal.applied event, eval record)."""

    ACCEPTED = "accepted"
    EDITED = "edited"
    CLEARED = "cleared"
    SKIPPED = "skipped"


class ProposalApplyRecord(Schema):
    key: str = Field(min_length=1)
    skip: bool = False  # do not create / patch this record
    # The final values, validated by the matching *Create / *Patch model in the service.
    values: dict[str, JsonValue] = Field(default_factory=dict)
    decisions: dict[str, FieldDecision] = Field(default_factory=dict)


class ProposalApplyBody(Schema):
    """POST /proposals/{id}/apply."""

    records: list[ProposalApplyRecord]
    # For milestone dates routed through correct_* (§6.5), keyed by record key.
    effective_date_corrections: dict[str, date] = Field(default_factory=dict)


# --- Input of rules.prefill.build_proposal: the workspace's records as plain facts ------


class ExistingProperty(Schema):
    id: UUID
    kind: PropertyKind
    address: Address
    build_year: Year
    notarized_on: date
    purchase_date: date
    purchase_price: PositiveMoney


class ExistingUnit(Schema):
    id: UUID
    property_id: UUID
    label: str
    mea: Fraction | None = None


class ExistingOwner(Schema):
    id: UUID
    full_name: str


class ExistingCost(Schema):
    id: UUID
    property_id: UUID
    kind: AcquisitionCostKind
    amount: PositiveMoney
    document_id: UUID | None = None  # the Beleg; null = a row "without a Beleg"
    note: str | None = None


class ExistingLoan(Schema):
    id: UUID
    property_id: UUID
    lender: str
    contract_number: str | None = None
    # So a loan contract's interest-only clause proposes the flag only when it changes it.
    interest_only_before_first_payment: bool = False


class ExistingDepositReceipt(Schema):
    """A Kaution payment already recorded on the tenancy (tenancy.deposit_received, the
    latest of its chain)."""

    amount: PositiveMoney
    received_on: date


class ExistingTenancy(Schema):
    id: UUID
    unit_id: UUID
    start_date: date
    # A Kautionsnachweis is matched to its tenancy by these (D42).
    deposit_amount: NonNegativeMoney | None = None
    deposit_receipts: list[ExistingDepositReceipt] = Field(default_factory=list)


class ExistingTenant(Schema):
    id: UUID
    full_name: str | None  # null after erasure: never matched


class ExistingPayRow(Schema):
    """A current return_input row of a pay document (phase 4b §8.2): what a new certificate
    or payslip of the same owner would supersede (rules.prefill.replaced_pay_row). The
    employer's name and the dates only: no figure, no person's name."""

    id: UUID
    owner_id: UUID
    kind: Literal["wage_certificate", "payslip"]
    tax_year: int
    employer: str
    period_month: date | None = None  # a payslip's month
    period_from: date | None = None  # a certificate's period (Nr. 1)
    period_to: date | None = None
    document_id: UUID | None = None


class ExistingBescheidZve(Schema):
    """A current household bescheid_zve row (phase 4b D129, §8.4): what an
    Einkommensteuerbescheid for the same year would supersede. The year, the Bescheid and its
    date only: no figure."""

    id: UUID
    tax_year: int
    document_id: UUID | None = None
    document_date: date | None = None  # the earlier Bescheid's date, for the note


class PrefillContext(Schema):
    """Everything build_proposal may look at. Lists are unordered: the result must not
    depend on their order (prefill.yaml runs every case shuffled too)."""

    document_id: UUID
    classification: ClassifyOutput
    properties: list[ExistingProperty] = Field(default_factory=list)
    units: list[ExistingUnit] = Field(default_factory=list)
    owners: list[ExistingOwner] = Field(default_factory=list)
    costs: list[ExistingCost] = Field(default_factory=list)
    loans: list[ExistingLoan] = Field(default_factory=list)
    tenancies: list[ExistingTenancy] = Field(default_factory=list)
    tenants: list[ExistingTenant] = Field(default_factory=list)
    # Phase 4b: the current certificate and payslip rows, for the note that one is replaced.
    pay_rows: list[ExistingPayRow] = Field(default_factory=list)
    # Phase 4b D129: the current bescheid_zve rows, for the note that one is replaced.
    bescheid_rows: list[ExistingBescheidZve] = Field(default_factory=list)
