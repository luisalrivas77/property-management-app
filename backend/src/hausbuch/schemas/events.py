"""Event envelope, the event catalog (phase 1, plus phase 2's in docs/phases/phase-2.md
§3.5), and the History timeline shapes.

Principle 4: domain changes are events; corrections are new events, never overwrites.
The domain tables hold current state and are written in the same transaction as the event.

Rules every event obeys (checked by Event's validator and by the DB):
- type is "<entity_type>.<verb>", and its prefix equals entity_type.
- payload validates against the payload model registered for that type.
- effective_date is required, optional or forbidden per type (EVENT_CATALOG).
- A correction carries corrects_event_id, repeats the same type and entity, and its payload is
  the full corrected payload (not a diff). An event is corrected at most once; chains go forward.
- Tenant personal data never appears in a payload: events outlive GDPR erasure. Phase 2
  payloads carry ids, counts, amounts, hashes, types and field *names*; never a counterparty
  name, a purpose text, a tenant name or a model's text (§3.5).
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .ai import ConfidenceLevel
from .allocation import (
    HMAC_PATTERN,
    AllocationCategory,
    AllocationMethod,
    HmacHex,
    MatchingRuleCreate,
    SplitMethod,
)
from .bank import StatementFormat
from .common import (
    Area,
    BuildingShare,
    Confidence,
    Fraction,
    MaskedIban,
    Money,
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
    BalanceSource,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    DuplicateStatus,
    EntityType,
    ExtractionKind,
    LinkTarget,
    PriceSplitSource,
    PropertyKind,
    RentChangeReason,
    RentModel,
)
from .property import OwnerShare
from .proposal import FieldDecision, RecordEntity
from .tenancy import TenancyParty

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


class AcquisitionCostUpdated(Schema):
    """A Nebenkosten row's note changed. The note is free text that may name people, so, as
    on create, its value is never recorded: note_changed only says that it changed. A change
    of a traced field (kind, amount, paid_on, document_id) is recorded as a correcting
    property.acquisition_cost_recorded instead (F2, decided 6 Nov 2026), so the service
    leaves `changes` empty."""

    cost_id: UUID
    changes: dict[str, FieldChange] = Field(default_factory=dict)
    note_changed: bool = False

    @model_validator(mode="after")
    def _something_changed(self) -> Self:
        if not self.changes and not self.note_changed:
            raise ValueError("an acquisition cost update must change something")
        return self


class AcquisitionCostRemoved(Schema):
    """A Nebenkosten row entered by mistake was deleted; this keeps what it said (not its
    note, see AcquisitionCostUpdated)."""

    cost_id: UUID
    kind: AcquisitionCostKind
    amount: PositiveMoney
    paid_on: date | None = None
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


class TenancyPartiesSet(Schema):
    """The whole party list after a change (a tenant added or removed, another primary):
    tenant ids and roles only, never names (D5)."""

    parties: list[TenancyParty] = Field(min_length=1)


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


# --- Phase 2 payloads (docs/phases/phase-2.md §3.5) ------------------------------


class WorkspaceSetting(StrEnum):
    """The workspace columns migration 0005 adds (D16, D26)."""

    AI_TENANT_DOCUMENTS = "ai_tenant_documents"
    AUTO_ACCEPT_LLM = "auto_accept_llm"


class WorkspaceSettingsChanged(Schema):
    """Changed setting names and their new values (booleans only)."""

    changes: dict[WorkspaceSetting, bool] = Field(min_length=1)


class DocumentClassified(Schema):
    ai_call_id: UUID
    type: DocumentType
    subtype: DocumentSubtype | None = None
    confidence: ConfidenceLevel
    contains_tenant_data: bool


class DocumentExtracted(Schema):
    """Field names only: the values may quote tenant data and stay in the extraction."""

    ai_call_id: UUID
    extraction_version: int = Field(ge=1)
    kind: ExtractionKind
    field_names: list[str]


class DocumentReadFailed(Schema):
    error_code: str = Field(min_length=1)
    attempts: int = Field(ge=1)


class ProposalNoted(Schema):
    """proposal.created / .superseded / .rejected. kind is null while unclassified."""

    document_id: UUID
    kind: ExtractionKind | None = None
    record_keys: list[str] = Field(default_factory=list)


class AppliedRecord(Schema):
    """One record of an applied proposal: what it became, and per field what the user did,
    never the value. entity_id is null for a skipped record."""

    key: str
    mode: Literal["create", "patch", "correction", "unchanged"]
    skipped: bool = False
    entity_type: RecordEntity
    entity_id: UUID | None = None
    decisions: dict[str, FieldDecision] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _entity(self) -> Self:
        if not self.skipped and self.entity_id is None:
            raise ValueError("an applied record names the entity it created or patched")
        return self


class ProposalApplied(Schema):
    document_id: UUID
    kind: ExtractionKind | None = None
    records: list[AppliedRecord] = Field(min_length=1)


class UserSignedIn(Schema):
    passkey_id: UUID


class UserSessionsRevoked(Schema):
    count: int = Field(ge=0)


class UserPasskeyEnrolled(Schema):
    passkey_id: UUID
    label: str = Field(min_length=1, max_length=100)


class UserPasskeyRef(Schema):
    """user.passkey_renamed / .passkey_removed: the passkey's id only."""

    passkey_id: UUID


class UserInvited(Schema):
    invite_id: UUID
    invited_user_id: UUID


class UserEnrolled(Schema):
    invite_id: UUID


class AccountCreated(Schema):
    label: str
    bank: str
    iban_masked: MaskedIban
    opening_balance: Money
    opening_balance_on: date
    is_owner_account: bool = False


class StatementImported(Schema):
    account_id: UUID
    document_id: UUID
    format: StatementFormat
    lines_total: int = Field(ge=0)
    lines_new: int = Field(ge=0)
    lines_skipped: int = Field(ge=0)
    first_booking_on: date | None = None
    last_booking_on: date | None = None
    transaction_ids: list[UUID] = Field(default_factory=list)  # the new lines


class StatementBalanceChecked(Schema):
    """statement.reconciled / .unreconciled. difference = declared_closing - computed_closing;
    opening_difference = declared_opening - computed_opening (null without a declared one)."""

    declared_opening: Money | None = None
    computed_opening: Money
    declared_closing: Money | None = None
    computed_closing: Money
    difference: Money | None = None
    opening_difference: Money | None = None
    balance_source: BalanceSource


class StatementDuplicateResolved(Schema):
    possible_duplicate_id: UUID
    transaction_id: UUID
    other_transaction_id: UUID
    status: DuplicateStatus

    @model_validator(mode="after")
    def _decided(self) -> Self:
        if self.status is DuplicateStatus.OPEN:
            raise ValueError("a resolution is kept_both or merged")
        return self


class AllocatedPart(Schema):
    allocation_id: UUID
    category: AllocationCategory
    amount: Money
    property_id: UUID | None = None
    unit_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    tax_year: TaxYear
    service_period_start: date | None = None
    service_period_end: date | None = None
    recurring: bool


class TransactionAllocated(Schema):
    """transaction.allocated; auto = confirmed without a person (rule or matcher)."""

    allocation_ids: list[UUID] = Field(min_length=1)
    allocations: list[AllocatedPart] = Field(min_length=1)
    method: AllocationMethod
    confidence: Confidence
    rule_id: UUID | None = None
    ai_call_id: UUID | None = None
    auto: bool


class TransactionReallocated(TransactionAllocated):
    supersedes: list[UUID] = Field(min_length=1)


class RuleRecorded(Schema):
    """rule.created / .disabled. Never an amount (D25), and never a pattern: a pattern is
    free text the user typed or the model inferred (it can name a person), and events are
    kept for good. The full matcher stays on the matching_rule row; the event records the
    IBAN hash, the sign and which patterns exist."""

    counterparty_iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    amount_sign: Literal["credit", "debit"] | None = None
    has_name_pattern: bool
    has_purpose_pattern: bool
    split_method: SplitMethod
    category: AllocationCategory | None = None
    property_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    created_from_allocation_id: UUID | None = None

    @model_validator(mode="after")
    def _matches_something(self) -> Self:
        if not (self.counterparty_iban_hmac or self.has_name_pattern or self.has_purpose_pattern):
            raise ValueError("a rule matches on an IBAN hash, a name pattern or a purpose pattern")
        return self

    @classmethod
    def of(cls, rule: MatchingRuleCreate) -> Self:
        """The event payload for a rule: its matcher reduced to hash, sign and pattern flags."""
        m = rule.matcher
        return cls(
            counterparty_iban_hmac=m.counterparty_iban_hmac,
            amount_sign=m.amount_sign,
            has_name_pattern=m.counterparty_name_pattern is not None,
            has_purpose_pattern=m.purpose_pattern is not None,
            **rule.model_dump(exclude={"matcher"}),
        )


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
    PROPERTY_ACQUISITION_COST_UPDATED = "property.acquisition_cost_updated"
    PROPERTY_ACQUISITION_COST_REMOVED = "property.acquisition_cost_removed"
    PROPERTY_PRICE_SPLIT_RECORDED = "property.price_split_recorded"
    PROPERTY_PRICE_SPLIT_SELECTED = "property.price_split_selected"

    UNIT_CREATED = "unit.created"
    UNIT_UPDATED = "unit.updated"

    TENANT_CREATED = "tenant.created"
    TENANT_UPDATED = "tenant.updated"
    TENANT_ERASED = "tenant.erased"

    TENANCY_CREATED = "tenancy.created"
    TENANCY_UPDATED = "tenancy.updated"
    TENANCY_PARTIES_SET = "tenancy.parties_set"
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
    # Phase 2
    DOCUMENT_CLASSIFIED = "document.classified"
    DOCUMENT_EXTRACTED = "document.extracted"
    DOCUMENT_READ_FAILED = "document.read_failed"

    WORKSPACE_SETTINGS_CHANGED = "workspace.settings_changed"

    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_SUPERSEDED = "proposal.superseded"
    PROPOSAL_REJECTED = "proposal.rejected"
    PROPOSAL_APPLIED = "proposal.applied"

    USER_SIGNED_IN = "user.signed_in"
    USER_SIGNED_OUT = "user.signed_out"
    USER_SESSIONS_REVOKED = "user.sessions_revoked"
    USER_PASSKEY_ENROLLED = "user.passkey_enrolled"
    USER_PASSKEY_RENAMED = "user.passkey_renamed"
    USER_PASSKEY_REMOVED = "user.passkey_removed"
    USER_INVITED = "user.invited"
    USER_ENROLLED = "user.enrolled"

    ACCOUNT_CREATED = "account.created"
    ACCOUNT_UPDATED = "account.updated"

    STATEMENT_IMPORTED = "statement.imported"
    STATEMENT_RECONCILED = "statement.reconciled"
    STATEMENT_UNRECONCILED = "statement.unreconciled"
    STATEMENT_DUPLICATE_RESOLVED = "statement.duplicate_resolved"

    TRANSACTION_ALLOCATED = "transaction.allocated"
    TRANSACTION_REALLOCATED = "transaction.reallocated"

    RULE_CREATED = "rule.created"
    RULE_DISABLED = "rule.disabled"


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
    E.PROPERTY_ACQUISITION_COST_UPDATED: EventSpec(
        AcquisitionCostUpdated, "forbidden", "Anschaffungsnebenkosten item changed"
    ),
    E.PROPERTY_ACQUISITION_COST_REMOVED: EventSpec(
        AcquisitionCostRemoved, "optional", "Anschaffungsnebenkosten item removed (date = paid_on)"
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
    E.TENANCY_PARTIES_SET: EventSpec(
        TenancyPartiesSet, "optional", "Parties changed (date = from when, if known)"
    ),
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
    # Phase 2
    E.DOCUMENT_CLASSIFIED: EventSpec(DocumentClassified, "forbidden", "Classified by the model"),
    E.DOCUMENT_EXTRACTED: EventSpec(DocumentExtracted, "forbidden", "Fields read by the model"),
    E.DOCUMENT_READ_FAILED: EventSpec(DocumentReadFailed, "forbidden", "Could not be read"),
    E.WORKSPACE_SETTINGS_CHANGED: EventSpec(
        WorkspaceSettingsChanged, "forbidden", "Workspace settings changed"
    ),
    E.PROPOSAL_CREATED: EventSpec(ProposalNoted, "forbidden", "Pre-fill proposal created"),
    E.PROPOSAL_SUPERSEDED: EventSpec(ProposalNoted, "forbidden", "Proposal replaced by a re-run"),
    E.PROPOSAL_REJECTED: EventSpec(ProposalNoted, "forbidden", "Proposal rejected"),
    E.PROPOSAL_APPLIED: EventSpec(ProposalApplied, "forbidden", "Proposal applied"),
    E.USER_SIGNED_IN: EventSpec(UserSignedIn, "forbidden", "Signed in with a passkey"),
    E.USER_SIGNED_OUT: EventSpec(Empty, "forbidden", "Signed out"),
    E.USER_SESSIONS_REVOKED: EventSpec(UserSessionsRevoked, "forbidden", "Sessions revoked"),
    E.USER_PASSKEY_ENROLLED: EventSpec(UserPasskeyEnrolled, "forbidden", "Passkey added"),
    E.USER_PASSKEY_RENAMED: EventSpec(UserPasskeyRef, "forbidden", "Passkey renamed"),
    E.USER_PASSKEY_REMOVED: EventSpec(UserPasskeyRef, "forbidden", "Passkey removed"),
    E.USER_INVITED: EventSpec(UserInvited, "forbidden", "Enrollment link created"),
    E.USER_ENROLLED: EventSpec(UserEnrolled, "forbidden", "Enrolled through a link"),
    E.ACCOUNT_CREATED: EventSpec(AccountCreated, "forbidden", "Bank account recorded"),
    E.ACCOUNT_UPDATED: EventSpec(Updated, "forbidden", "Bank account fields changed"),
    E.STATEMENT_IMPORTED: EventSpec(
        StatementImported, "optional", "Statement imported (date = last booking day)"
    ),
    E.STATEMENT_RECONCILED: EventSpec(
        StatementBalanceChecked, "forbidden", "Statement reconciled to its balance"
    ),
    E.STATEMENT_UNRECONCILED: EventSpec(
        StatementBalanceChecked, "forbidden", "Statement does not match its balance"
    ),
    E.STATEMENT_DUPLICATE_RESOLVED: EventSpec(
        StatementDuplicateResolved, "forbidden", "Possible duplicate decided"
    ),
    E.TRANSACTION_ALLOCATED: EventSpec(
        TransactionAllocated, "required", "Transaction allocated (date = booking date)"
    ),
    E.TRANSACTION_REALLOCATED: EventSpec(
        TransactionReallocated, "required", "Allocation corrected (date = booking date)"
    ),
    E.RULE_CREATED: EventSpec(RuleRecorded, "forbidden", "Matching rule learned"),
    E.RULE_DISABLED: EventSpec(RuleRecorded, "forbidden", "Matching rule disabled"),
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
