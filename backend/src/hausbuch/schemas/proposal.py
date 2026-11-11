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
from .common import (
    Fraction,
    LivingArea,
    Money,
    MutableRecord,
    NonNegativeMoney,
    PositiveMoney,
    Schema,
    Year,
)
from .document import HandField, HandFields
from .enums import (
    AcquisitionCostKind,
    EntityType,
    ExtractionKind,
    OperatingCostsTerms,
    PropertyKind,
    ProposalStatus,
    ProposalTargetMode,
)
from .property import Address
from .tenancy import RentDueDay

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


class CarriedField(Schema):
    """A field of another record of the same proposal (D132): its record key and field name,
    dotted as the apply body names it ("tax_input:interest_certified", "value.amount")."""

    key: str = Field(min_length=1)
    field: str = Field(min_length=1)


class FieldOption(Schema):
    """A value the review offers for one field with one click (D139): the field's value as
    the apply body sends it and its label, e.g. "61.40" and "WE 3 · 2. OG links · 61,40 m²"
    for a Wohnflächenberechnung's dwelling the code could not pick by itself."""

    value: str = Field(min_length=1)
    label: str = Field(min_length=1)


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
    # Typing only (D132). hand_from: a hand field left off this record because another record
    # asks for its value (an invoice's Betrag is the Nebenkosten row's amount); the apply
    # writes what the user applied there into the document's reading. Empty on a proposal
    # stored before 10 Nov 2026, which then writes no such value.
    hand_from: dict[HandField, CarriedField] = Field(default_factory=dict)
    # hand_version: the extraction version the hand fields were read from when the proposal
    # has no reading of its own (0: none), pinned under the document's lock; the apply's
    # stale guard compares it with the current version. None with a reading of its own (its
    # extraction_id anchors it) and on a proposal stored before 10 Nov 2026.
    hand_version: int | None = Field(default=None, ge=0)
    # D139: values the review offers for a field with one click, beside its own input (which
    # stays editable, D132). Empty on every record but a Wohnflächenberechnung's property
    # patch whose dwelling the code could not pick, and on a proposal stored before it.
    options: dict[str, list[FieldOption]] = Field(default_factory=dict)

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
    # Phase 3 D71; compared and proposed by a Wohnflächenberechnung or a Kaufvertrag (D139).
    living_area_m2: LivingArea | None = None
    # D139: what the property's Kaufvertrag reading states (a purchase_contract document
    # linked to the property, its current reading of that kind), to pick the household's row
    # of a Wohnflächenberechnung. None when not stated, or when two contracts disagree.
    contract_mea: Fraction | None = None
    contract_unit_label: str | None = None


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
    # A catalogue document of the tenancy group is linked to the tenancy its date falls in
    # (D131); null while the tenancy runs.
    end_date: date | None = None
    # A Kautionsnachweis is matched to its tenancy by these (D42).
    deposit_amount: NonNegativeMoney | None = None
    deposit_receipts: list[ExistingDepositReceipt] = Field(default_factory=list)
    # A re-read lease proposes these only when they differ from the stored ones (D152); the
    # defaults are the tenancy's own (phase 3 D67, D72).
    rent_due_day: RentDueDay = 3
    operating_costs_terms: OperatingCostsTerms = OperatingCostsTerms.PREPAYMENT


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


class ExistingPrivateCost(Schema):
    """Phase 3 D143: a private_cost tax input in force whose document is the bill a payment
    proof was uploaded for. The year, the label and the gross amount (value.amount)."""

    id: UUID
    tax_year: int
    label: str
    amount: Money | None = None


class ExistingProofBill(Schema):
    """Phase 3 D143: the bill a payment proof is linked to: the acquisition cost rows that
    carry it as Beleg and its private_cost rows in force, to compare the proof's amount with
    and to move the rows to the year paid."""

    document_id: UUID
    acquisition_costs: list[ExistingCost] = Field(default_factory=list)
    private_costs: list[ExistingPrivateCost] = Field(default_factory=list)
    # D149: how the card names the bill ("<title> vom <date>, <amount>"): its typing's title
    # (else the file's name) and date; None when the bill has neither.
    title: str | None = None
    document_date: date | None = None
    # D150: the amounts of the cost lines the bill's current reading states (empty without a
    # reading or lines); rules.proof_match uses them only when they sum to the bill's total.
    parts: list[Money] = Field(default_factory=list)


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
    # Phase 2 D132: the document's hand-entry fields as its typing form shows them now
    # (services.documents.hand_fields), for a proposal with no reading of its own (typing
    # only, or blocked by the tenant gate), whose typing record then carries them.
    hand_fields: HandFields | None = None
    # Phase 2 D135: a line of a rental account pays this bill (an allocation carries the
    # document, or a line matches it by amount, date and IBAN or reference;
    # services.matching.paid_from_rental_account), so no cost paid from another account is
    # proposed for it.
    paid_from_rental_account: bool = False
    # Phase 2 D153: the one tenancy the document is linked to (a lease confirmed before);
    # a re-read lease patches it, whatever unit label or start date the reading states.
    linked_tenancy_id: UUID | None = None
    # Phase 3 D143: for a payment proof, the bill it was uploaded for (its document link).
    proof_bill: ExistingProofBill | None = None
    # Phase 3 D149: for a payment proof linked to no bill, the bills that still wait for a
    # proof (the draft's payment_proof.missing, rules.tax.draft.bills_without_proof) of its
    # property (of every property when it is linked to none), over every year the draft can
    # be asked for; the pre-fill proposes the one its amount pays.
    proof_candidates: list[ExistingProofBill] = Field(default_factory=list)
