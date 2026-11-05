"""Event envelope, the phase 1 event catalog, and the History timeline shapes.

Principle 4: domain changes are events; corrections are new events, never overwrites.
The domain tables hold current state and are written in the same transaction as the event.

Rules every event obeys (checked by Event's validator and by the DB):
- type is "<entity_type>.<verb>", and its prefix equals entity_type.
- payload validates against the payload model registered for that type.
- effective_date is required, optional or forbidden per type (EVENT_CATALOG).
- A correction carries corrects_event_id, repeats the same type and entity, and its payload is
  the full corrected payload (not a diff). An event is corrected at most once; chains go forward.
- Tenant personal data never appears in a payload: events outlive GDPR erasure.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .common import (
    Area,
    BuildingShare,
    Fraction,
    NonNegativeMoney,
    PositiveMoney,
    Rate,
    Rooms,
    Schema,
    Sha256Hex,
    TaxRate,
    TaxYear,
    Year,
)
from .enums import (
    AcquisitionCostKind,
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    EntityType,
    LinkTarget,
    PriceSplitSource,
    PropertyKind,
    RentChangeReason,
    RentModel,
)
from .property import OwnerShare

# --- Payloads ----------------------------------------------------------------


class Empty(Schema):
    """The event itself is the fact (e.g. move-in on effective_date)."""


class FieldChange(Schema):
    old: JsonValue
    new: JsonValue


class Updated(Schema):
    """Non-milestone field changes. Milestone dates change only through a correcting event."""

    changes: dict[str, FieldChange] = Field(min_length=1)
    reason: str | None = None


class OwnerCreated(Schema):
    marginal_tax_rate: TaxRate | None = None


class PropertyCreated(Schema):
    label: str
    kind: PropertyKind
    build_year: Year
    purchase_price: PositiveMoney


class PurchaseNotarized(Schema):
    purchase_price: PositiveMoney


class OwnershipSet(Schema):
    shares: list[OwnerShare] = Field(min_length=1)


class AcquisitionCostRecorded(Schema):
    cost_id: UUID
    kind: AcquisitionCostKind
    amount: PositiveMoney
    document_id: UUID | None = None


class PriceSplitRecorded(Schema):
    split_id: UUID
    source: PriceSplitSource
    building_share: BuildingShare
    evidence_document_id: UUID | None = None


class PriceSplitSelected(Schema):
    split_id: UUID


class UnitCreated(Schema):
    label: str
    floor_area_m2: Area
    rooms: Rooms | None = None
    mea: Fraction | None = None


class TenantField(StrEnum):
    FULL_NAME = "full_name"
    EMAIL = "email"
    PHONE = "phone"
    IBAN = "iban"


class ErasureBasis(StrEnum):
    """Why a tenant's personal data could be erased (decided by Luis, 5 Nov 2026)."""

    RETENTION_EXPIRED = "retention_expired"  # the 4-year default after the last tenancy passed
    NO_TENANCY = "no_tenancy"  # an applicant who never had a tenancy
    # The tenant asked (GDPR Art. 17) and the landlord chose to honour it after move-out, inside
    # the claims period. Never possible while a tenancy runs: the data performs the lease.
    DATA_SUBJECT_REQUEST = "data_subject_request"


class TenantErased(Schema):
    """Records the basis of an erasure, never any of the erased values."""

    basis: ErasureBasis


class TenantUpdated(Schema):
    """Names the fields that changed, never their values."""

    changed_fields: list[TenantField] = Field(min_length=1)


class TenancyCreated(Schema):
    """Move-in and move-out are their own milestones (tenancy.started / tenancy.ended), so a
    corrected move-in date never leaves a stale copy here."""

    tenant_ids: list[UUID] = Field(min_length=1)
    rent_model: RentModel
    fixed_term_until: date | None = None
    deposit_amount: NonNegativeMoney


class RentScheduled(Schema):
    rent_schedule_id: UUID
    effective_from: date
    cold_rent: NonNegativeMoney
    operating_costs_prepayment: NonNegativeMoney
    reason: RentChangeReason
    supersedes_id: UUID | None = None


class DepositReceived(Schema):
    amount: PositiveMoney


class NoticeGiven(Schema):
    given_by: Literal["tenant", "landlord"]
    ends_on: date


class LoanCreated(Schema):
    lender: str
    principal: PositiveMoney
    nominal_rate: Rate
    monthly_payment: PositiveMoney
    first_payment_on: date
    fixed_until: date | None = None
    follow_up_rate: Rate | None = None


class LoanDisbursed(Schema):
    amount: PositiveMoney


class DocumentUploaded(Schema):
    sha256: Sha256Hex
    media_type: str
    size_bytes: int = Field(gt=0)
    source: DocumentSource


class DocumentField(StrEnum):
    TYPE = "type"
    SUBTYPE = "subtype"
    TITLE = "title"
    DOCUMENT_DATE = "document_date"
    TAX_YEAR = "tax_year"


class DocumentTyped(Schema):
    """The typing as it now stands, and which fields changed. The title's value is never
    recorded: it often names the tenant (D5)."""

    type: DocumentType
    subtype: DocumentSubtype | None = None
    document_date: date | None = None  # a retention input, so its value is traced
    tax_year: TaxYear | None = None
    changed_fields: list[DocumentField] = Field(default_factory=list)
    extraction_version: int | None = Field(default=None, ge=1)


class DocumentLinked(Schema):
    target_type: LinkTarget
    target_id: UUID


# --- Catalog -----------------------------------------------------------------

EffectiveDate = Literal["required", "optional", "forbidden"]


@dataclass(frozen=True)
class EventSpec:
    payload: type[Schema]
    effective_date: EffectiveDate
    meaning: str


class EventType(StrEnum):
    OWNER_CREATED = "owner.created"
    OWNER_UPDATED = "owner.updated"

    PROPERTY_CREATED = "property.created"
    PROPERTY_UPDATED = "property.updated"
    PROPERTY_PURCHASE_NOTARIZED = "property.purchase_notarized"
    PROPERTY_POSSESSION_TRANSFERRED = "property.possession_transferred"
    PROPERTY_OWNERSHIP_SET = "property.ownership_set"
    PROPERTY_ACQUISITION_COST_RECORDED = "property.acquisition_cost_recorded"
    PROPERTY_PRICE_SPLIT_RECORDED = "property.price_split_recorded"
    PROPERTY_PRICE_SPLIT_SELECTED = "property.price_split_selected"

    UNIT_CREATED = "unit.created"
    UNIT_UPDATED = "unit.updated"

    TENANT_CREATED = "tenant.created"
    TENANT_UPDATED = "tenant.updated"
    TENANT_ERASED = "tenant.erased"

    TENANCY_CREATED = "tenancy.created"
    TENANCY_UPDATED = "tenancy.updated"
    TENANCY_STARTED = "tenancy.started"
    TENANCY_RENT_SCHEDULED = "tenancy.rent_scheduled"
    TENANCY_DEPOSIT_RECEIVED = "tenancy.deposit_received"
    TENANCY_NOTICE_GIVEN = "tenancy.notice_given"
    TENANCY_ENDED = "tenancy.ended"

    LOAN_CREATED = "loan.created"
    LOAN_UPDATED = "loan.updated"
    LOAN_DISBURSED = "loan.disbursed"

    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_TYPED = "document.typed"
    DOCUMENT_LINKED = "document.linked"
    DOCUMENT_UNLINKED = "document.unlinked"


E = EventType
EVENT_CATALOG: dict[EventType, EventSpec] = {
    E.OWNER_CREATED: EventSpec(OwnerCreated, "forbidden", "Owner recorded"),
    E.OWNER_UPDATED: EventSpec(Updated, "forbidden", "Owner fields changed"),
    E.PROPERTY_CREATED: EventSpec(PropertyCreated, "forbidden", "Property record created"),
    E.PROPERTY_UPDATED: EventSpec(Updated, "forbidden", "Property fields changed"),
    E.PROPERTY_PURCHASE_NOTARIZED: EventSpec(
        PurchaseNotarized, "required", "Kaufvertrag notarized (Spekulationsfrist starts)"
    ),
    E.PROPERTY_POSSESSION_TRANSFERRED: EventSpec(
        Empty, "required", "Besitz, Nutzen und Lasten transferred (AfA starts)"
    ),
    E.PROPERTY_OWNERSHIP_SET: EventSpec(OwnershipSet, "optional", "Ownership shares set"),
    E.PROPERTY_ACQUISITION_COST_RECORDED: EventSpec(
        AcquisitionCostRecorded, "optional", "Anschaffungsnebenkosten item recorded"
    ),
    E.PROPERTY_PRICE_SPLIT_RECORDED: EventSpec(
        PriceSplitRecorded, "forbidden", "Building/land split candidate recorded"
    ),
    E.PROPERTY_PRICE_SPLIT_SELECTED: EventSpec(
        PriceSplitSelected, "forbidden", "Split chosen as the AfA basis"
    ),
    E.UNIT_CREATED: EventSpec(UnitCreated, "forbidden", "Unit recorded"),
    E.UNIT_UPDATED: EventSpec(Updated, "forbidden", "Unit fields changed"),
    E.TENANT_CREATED: EventSpec(Empty, "forbidden", "Tenant recorded"),
    E.TENANT_UPDATED: EventSpec(TenantUpdated, "forbidden", "Tenant contact data changed"),
    E.TENANT_ERASED: EventSpec(TenantErased, "forbidden", "Tenant personal data erased"),
    E.TENANCY_CREATED: EventSpec(TenancyCreated, "optional", "Lease signed (date = signed_on)"),
    E.TENANCY_UPDATED: EventSpec(Updated, "forbidden", "Tenancy fields changed"),
    E.TENANCY_STARTED: EventSpec(Empty, "required", "Move-in"),
    E.TENANCY_RENT_SCHEDULED: EventSpec(
        RentScheduled, "required", "Rent row added (date = effective_from)"
    ),
    E.TENANCY_DEPOSIT_RECEIVED: EventSpec(DepositReceived, "required", "Kaution received"),
    E.TENANCY_NOTICE_GIVEN: EventSpec(NoticeGiven, "required", "Notice given (date = given on)"),
    E.TENANCY_ENDED: EventSpec(Empty, "required", "Move-out"),
    E.LOAN_CREATED: EventSpec(LoanCreated, "optional", "Loan contract recorded (date = signed)"),
    E.LOAN_UPDATED: EventSpec(Updated, "forbidden", "Loan fields changed"),
    E.LOAN_DISBURSED: EventSpec(LoanDisbursed, "required", "Loan paid out"),
    E.DOCUMENT_UPLOADED: EventSpec(DocumentUploaded, "forbidden", "Original stored"),
    E.DOCUMENT_TYPED: EventSpec(DocumentTyped, "forbidden", "Type and fields set"),
    E.DOCUMENT_LINKED: EventSpec(DocumentLinked, "forbidden", "Linked to an entity"),
    E.DOCUMENT_UNLINKED: EventSpec(DocumentLinked, "forbidden", "Link removed"),
}
del E


# --- Envelope ----------------------------------------------------------------


class EventRef(Schema):
    """The identity of an event, as far as correction rules care."""

    type: EventType
    entity_type: EntityType
    entity_id: UUID


class Event(Schema):
    id: UUID
    workspace_id: UUID
    seq: int = Field(ge=1)  # global recording order
    type: EventType
    entity_type: EntityType
    entity_id: UUID
    # Scope for History views. property_id is set on every event of a property, its units,
    # tenancies and loans; unit_id additionally on unit and tenancy events.
    property_id: UUID | None = None
    unit_id: UUID | None = None
    effective_date: date | None = None  # domain date (move-in day); null = not a milestone
    recorded_at: datetime
    actor_type: ActorType
    actor_id: str  # user id, job name, or model id
    correlation_id: UUID  # groups the events of one command
    corrects_event_id: UUID | None = None
    payload: dict[str, JsonValue]
    payload_version: int = 1

    @model_validator(mode="after")
    def _well_formed(self) -> Self:
        if self.type.split(".", 1)[0] != self.entity_type:
            raise ValueError(f"type {self.type} does not belong to entity_type {self.entity_type}")
        spec = EVENT_CATALOG[self.type]
        if spec.effective_date == "required" and self.effective_date is None:
            raise ValueError(f"{self.type} needs effective_date")
        if spec.effective_date == "forbidden" and self.effective_date is not None:
            raise ValueError(f"{self.type} must not carry effective_date")
        if self.corrects_event_id == self.id:
            raise ValueError("an event cannot correct itself")
        spec.payload.model_validate(self.payload)
        return self


# --- History timeline (rules.timeline) ---------------------------------------


class TimelineScope(Schema):
    """property_id alone = the property's history; with unit_id = that unit's history,
    which also includes property-level events (unit_id null), e.g. purchase and loans."""

    property_id: UUID
    unit_id: UUID | None = None


class TimelineEntry(Schema):
    """One milestone. For a correction chain A → B → C only one entry appears, showing C."""

    event_id: UUID  # latest event in the chain (C)
    root_event_id: UUID  # first event in the chain (A); equals event_id when uncorrected
    type: EventType
    entity_type: EntityType
    entity_id: UUID
    effective_date: date
    corrected: bool
    original_effective_date: date | None = None  # A's date, when corrected
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def _corrected(self) -> Self:
        if self.corrected != (self.event_id != self.root_event_id):
            raise ValueError("corrected exactly when event_id differs from root_event_id")
        if not self.corrected and self.original_effective_date is not None:
            raise ValueError("original_effective_date only on corrected entries")
        return self
