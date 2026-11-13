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
from typing import Any, Literal, Self
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
from .bank_sync import (
    PRIMARY_SLOT,
    BankCode,
    BankParameters,
    CredentialSlot,
    LoginMasked,
    SyncStatus,
    SyncTrigger,
)
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
    AccountKind,
    AccountPurpose,
    AcquisitionCostKind,
    ActorType,
    BalanceSource,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    DuplicateStatus,
    EntityType,
    ExtractionKind,
    IncomeInputKind,
    LinkTarget,
    PriceSplitSource,
    PropertyKind,
    QuestionnaireAnswerValue,
    RentChangeReason,
    RentModel,
    ReturnInputKind,
    StateCode,
)
from .household import (
    AssetKind,
    HoldingSource,
    MovementImportFormat,
    MovementKind,
    MovementSource,
    PriceSourceKind,
    TransferLinkKind,
    TransferMatchedBy,
)
from .performance import INCOME_INPUT_VALUES, IncomeInputValue, Levers, income_value_by_kind
from .property import OwnerShare
from .proposal import FieldDecision, RecordEntity
from .reminders import (
    NotificationChannel,
    ReminderAckAction,
    ReminderId,
    ReminderKind,
    ReminderScope,
)
from .tax import (
    TAX_INPUT_VALUES,
    FormStatus,
    PrivateCostValue,
    TaxExportKind,
    TaxInputKind,
    TaxInputValue,
    value_by_kind,
)
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
    """owner.created. church_tax and tax_state since migration 0016 (phase 4 D90).
    marginal_tax_rate is legacy: events written before 0016 carry the typed rate, and the event
    log keeps it (the rate moved to an income_input row). exclude=True keeps it out of every
    new payload, while an old payload still validates."""

    church_tax: bool = False
    tax_state: StateCode | None = None
    marginal_tax_rate: TaxRate | None = Field(default=None, exclude=True)


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
    """Records the basis of an erasure, never any of the erased values. The counts are what
    the erasure scrub cleared (phase-2.md D42 (3)); they default to 0 so events written
    before the scrub existed still read."""

    basis: ErasureBasis
    proposals_scrubbed: int = Field(default=0, ge=0)
    ai_calls_scrubbed: int = Field(default=0, ge=0)
    rules_disabled: int = Field(default=0, ge=0)


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
    # Added 8 Nov 2026; events stored before it lack the key and read as false.
    interest_only_before_first_payment: bool = False


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
    """The workspace's boolean settings: the two columns migration 0005 adds (D16, D26),
    phase 5's `reminder_emails` (migration 0019, D123) and phase 6's `ai_private_accounts`
    (migration 0025, D168)."""

    AI_TENANT_DOCUMENTS = "ai_tenant_documents"
    AUTO_ACCEPT_LLM = "auto_accept_llm"
    REMINDER_EMAILS = "reminder_emails"
    AI_PRIVATE_ACCOUNTS = "ai_private_accounts"


class WorkspaceSettingsChanged(Schema):
    """Changed setting names and their new values (booleans only). Phase 6 D168: switching
    `ai_private_accounts` on records the member who consented (absent before 0025)."""

    changes: dict[WorkspaceSetting, bool] = Field(min_length=1)
    consented_by: UUID | None = None


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
    """Phase 6 (D156): a depot or an exchange has no IBAN, and every account a kind and a
    purpose; events written before migration 0025 carry neither (the defaults)."""

    label: str
    bank: str
    iban_masked: MaskedIban | None = None
    opening_balance: Money
    opening_balance_on: date
    is_owner_account: bool = False
    kind: AccountKind = AccountKind.BANK_CURRENT
    purpose: AccountPurpose | None = None
    provider: str | None = None
    # Phase 6 D176: a credit card's customer number, masked, when typed at creation
    external_ref_masked: str | None = None


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
    # Phase 6 D173 (3): the balance the file states beside its lines (DKB's „Kontostand
    # vom“), the pre-fill of the closing the user confirms; absent for every other format.
    stated_closing: Money | None = None
    stated_closing_on: date | None = None
    # Phase 6 D176: a card statement's card (its last four digits only, never more of the
    # number) and the payment IBAN it prints, masked: the proposal the account page offers to
    # confirm (D176 (4)); absent for every other format.
    card_last4: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    payment_iban_masked: MaskedIban | None = None
    # Phase 6 step 4: the IBAN a depot's cash statement prints, masked: the proposal the
    # account page offers to confirm (D156 amended); absent for every other format.
    account_iban_masked: MaskedIban | None = None


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
    # D51, as_last_split's kind; default None, so events before it still read
    categories: list[AllocationCategory] | None = None
    property_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    recurring: bool = False
    created_from_allocation_id: UUID | None = None
    same_amount_only: bool = False  # D49; default false, so events before it still read

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


# Phase 3 (docs/phases/phase-3.md §6). Amounts and codes only: never a note (free text).


class PrivateCostRecorded(Schema):
    """A private_cost value as the event log keeps it (phase 4b D111): amounts and codes only.
    The label and the trip dates stay in the tax_input row; the payload drops them, whatever
    the caller passes. The category of a bill's part paid from another account (phase 2
    D135) is a code and is kept; older events have none."""

    cost_class: str
    category: AllocationCategory | None = None
    amount: NonNegativeMoney | None = None
    trips: int | None = None
    km_round_trip: int | None = None
    share: Fraction

    @classmethod
    def of(cls, value: PrivateCostValue) -> "PrivateCostRecorded":
        return cls(
            cost_class=value.cost_class,
            category=value.category,
            amount=value.amount,
            trips=value.trips,
            km_round_trip=value.km_round_trip,
            share=value.share,
        )


class TaxInputRecorded(Schema):
    """property.tax_input_recorded: one tax_input row as written (a correction or withdrawal
    is a new row, so a new event with supersedes_id; D58). A private_cost value is recorded as
    PrivateCostRecorded (no free text)."""

    tax_input_id: UUID
    kind: TaxInputKind
    tax_year: TaxYear | None = None
    unit_id: UUID | None = None
    loan_id: UUID | None = None
    allocation_id: UUID | None = None
    document_id: UUID | None = None
    value: TaxInputValue | PrivateCostRecorded
    supersedes_id: UUID | None = None
    carried_from_id: UUID | None = None  # phase 4b D110
    withdrawn: bool = False

    @model_validator(mode="before")
    @classmethod
    def _value_by_kind(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("kind") == TaxInputKind.PRIVATE_COST:
            value = data.get("value")
            if isinstance(value, dict) and "label" in value:
                value = PrivateCostValue.model_validate(value)
            if isinstance(value, PrivateCostValue):
                value = PrivateCostRecorded.of(value)
            elif isinstance(value, dict):
                value = PrivateCostRecorded.model_validate(value)
            return data | {"value": value}
        return value_by_kind(data)

    @model_validator(mode="after")
    def _value_matches_kind(self) -> Self:
        expected = (
            PrivateCostRecorded
            if self.kind is TaxInputKind.PRIVATE_COST
            else TAX_INPUT_VALUES[self.kind]
        )
        if type(self.value) is not expected:
            raise ValueError("value does not match the kind")
        return self


class AnlageVExported(Schema):
    """property.anlage_v_exported: what was handed over (D65, D68)."""

    tax_export_id: UUID
    tax_year: TaxYear
    kind: TaxExportKind
    rule_set_version: str
    form_year: TaxYear
    form_status: FormStatus
    file_sha256: Sha256Hex
    size_bytes: int = Field(gt=0)
    result: Money  # the cent result (Z85)
    document_count: int | None = Field(default=None, ge=0)  # archive: the originals inside


# Phase 4 (docs/phases/phase-4.md §6). Amounts, codes and ids only: never a note (free text)
# and never a scenario's name.


class IncomeInputRecorded(Schema):
    """owner.income_input_recorded (expected_gross_pay, deductions_override; entity = the
    owner) and workspace.income_input_recorded (bescheid_zve, marginal_rate_override; entity =
    the workspace): one income_input row as written (D83)."""

    income_input_id: UUID
    kind: IncomeInputKind
    tax_year: TaxYear
    owner_id: UUID | None = None
    document_id: UUID | None = None
    value: IncomeInputValue
    supersedes_id: UUID | None = None
    withdrawn: bool = False

    @model_validator(mode="before")
    @classmethod
    def _value_by_kind(cls, data: Any) -> Any:
        return income_value_by_kind(data)

    @model_validator(mode="after")
    def _value_matches_kind(self) -> Self:
        if type(self.value) is not INCOME_INPUT_VALUES[self.kind]:
            raise ValueError("value does not match the kind")
        return self


class ScenarioRecorded(Schema):
    """property.scenario_recorded: one projection_scenario row as written (D87). The name is
    the user's free text and stays in the row, not in the log."""

    scenario_id: UUID
    is_default: bool
    levers: Levers
    supersedes_id: UUID | None = None
    withdrawn: bool = False


# Phase 4b (docs/phases/phase-4b.md §6). Amounts, codes, months and ids only: never an
# employer, a workplace, an item label, a recipient, a note, or a child's date of birth.


class ReturnInputRecorded(Schema):
    """owner.return_input_recorded (the owner kinds; entity = the owner) and
    workspace.return_input_recorded (the household kinds; entity = the workspace): one
    return_input row as written (D97). amounts: the value's money fields by name (a payslip's
    pairs as "gross.month" / "gross.ytd"), see employment.return_input_amounts. months_in_year:
    a child's months in tax_year (D109), never the birth date."""

    return_input_id: UUID
    kind: ReturnInputKind
    tax_year: TaxYear
    document_id: UUID | None = None
    supersedes_id: UUID | None = None
    carried_from_id: UUID | None = None
    withdrawn: bool = False
    amounts: dict[str, Money] = Field(default_factory=dict)
    months_in_year: int | None = Field(default=None, ge=0, le=12)

    @model_validator(mode="after")
    def _child_months(self) -> Self:
        if (self.months_in_year is not None) != (self.kind is ReturnInputKind.CHILD):
            raise ValueError("months_in_year is set exactly for a child")
        return self


class QuestionnaireAnswered(Schema):
    """workspace.questionnaire_answered: one questionnaire_answer row as written (D110)."""

    questionnaire_answer_id: UUID
    question_key: str = Field(max_length=64)
    tax_year: TaxYear
    answer: QuestionnaireAnswerValue
    choice: str | None = Field(default=None, max_length=32)
    owner_id: UUID | None = None
    property_id: UUID | None = None
    supersedes_id: UUID | None = None
    carried_from_id: UUID | None = None
    withdrawn: bool = False


class AnlageNExported(Schema):
    """owner.anlage_n_exported: one owner's Anlage N sheet as handed over (D104)."""

    tax_export_id: UUID
    tax_year: TaxYear
    kind: TaxExportKind
    rule_set_version: str
    form_year: TaxYear
    form_status: FormStatus
    file_sha256: Sha256Hex
    size_bytes: int = Field(gt=0)
    result: Money  # the Anlage N result


# Phase 5 (docs/phases/phase-5.md §4.3, §5.4, §6.3, §8). Counts, codes, ids and dates only:
# never a PIN, a TAN, the login in clear, the FinTS URL, the bank's message text, an amount,
# a mail address, a subject or a tenant's name (D119, D121, D122). §8 names the bank events
# `bank.*`, but an event's type prefix must equal its entity type and the entity is the
# account, so they are `account.*` (step 0 finding); the `bank.*` strings stay error codes.


class BankConnectionRecorded(Schema):
    """account.connection_created and account.connection_updated (D119): the BLZ, the masked
    login and the switch; `changed` names the fields an update changed (never their values
    beyond these)."""

    connection_id: UUID
    account_id: UUID
    bank_code: BankCode
    login_masked: LoginMasked
    # Phase 6 D163: the slot's name, a label (never the PIN); absent before 0025 = primary.
    credential_slot: CredentialSlot = PRIMARY_SLOT
    auto_sync: bool
    fetch_statements: bool = False  # D154 (7); absent from events written before 0024
    changed: list[
        Literal[
            "auto_sync",
            "credential_slot",
            "fetch_statements",
            "fints_url",
            "login_name",
            "tan_mechanism",
        ]
    ] = Field(default_factory=list)


class BankStatementFetched(Schema):
    """account.statement_fetched (D154 (5)): one PDF statement the bank delivered and the app
    stored, by its number and year as the bank numbers them. Never the PDF, its size or the
    bank's Quittungscode."""

    sync_id: UUID
    document_id: UUID
    year: int = Field(ge=1000, le=9999)
    number: int = Field(ge=1, le=99999)


class BankParametersRead(BankParameters):
    """account.bank_parameters_read (phase 6 D173 (7)): what the bank's parameters said in a
    run, recorded when they differ from the connection's last record. The bank's public
    parameters only: never a login, system id, IBAN, PIN or product id."""

    connection_id: UUID
    sync_id: UUID


class BankSyncStarted(Schema):
    sync_id: UUID
    connection_id: UUID
    trigger: SyncTrigger
    requested_from: date
    requested_to: date


class BankSyncApprovalNeeded(Schema):
    sync_id: UUID
    trigger: SyncTrigger


class BankSyncCompleted(Schema):
    """account.sync_completed (§4.3): done or nothing_new, with the counts (D127)."""

    sync_id: UUID
    status: Literal[SyncStatus.DONE, SyncStatus.NOTHING_NEW]
    import_id: UUID | None = None  # set exactly for done
    lines_total: int = Field(ge=0)
    lines_new: int = Field(ge=0)
    balance_source: BalanceSource | None = None
    balance_checked: bool
    reconciled: bool | None = None  # the import's reconciliation; None for nothing_new

    @model_validator(mode="after")
    def _import(self) -> Self:
        if (self.status is SyncStatus.DONE) != (self.import_id is not None):
            raise ValueError("import_id is set exactly for a run that imported")
        if self.status is SyncStatus.NOTHING_NEW and self.lines_new != 0:
            raise ValueError("nothing_new has no new line (D127)")
        return self


class BankSyncFailed(Schema):
    """account.sync_failed. `response_code` (step 1) is the four digits of a 9xxx answer for
    bank.refused, never the bank's text (§4.3); or 3938 (access temporarily locked), the one
    warning python-fints ends a run on, when the bank sent no 9xxx code (D151)."""

    sync_id: UUID
    error_code: str = Field(pattern=r"^(bank|statement)\.[a-z_]+$", max_length=64)
    response_code: str | None = Field(default=None, pattern=r"^(9[0-9]{3}|3938)$")


class DocumentReceivedByEmail(Schema):
    """document.received_by_email (§8): which inbound_mail row brought it, and whether it was
    imported as a statement."""

    inbound_mail_id: UUID
    imported: bool


# The poll-level outcomes of email-in that a person must fix (D121, §5.5): the app password
# refused, the label missing, the server not reached for several polls. Codes only (D5).
InboxPollCode = Literal["email.auth_rejected", "email.label_missing", "email.unreachable"]


class InboxPollFailed(Schema):
    """workspace.inbox_poll_failed: written when the poll's outcome changes to this code,
    never once per poll."""

    code: InboxPollCode


class InboxPollRecovered(Schema):
    """workspace.inbox_poll_recovered: the first completed poll after a failure."""

    previous_code: InboxPollCode


class ReminderAcknowledged(Schema):
    """workspace.reminder_acknowledged (§6.3)."""

    reminder_key: ReminderKind
    scope_type: ReminderScope
    scope_id: UUID | None = None
    due_on: date | None = None
    action: ReminderAckAction
    snoozed_until: date | None = None


class NotificationSent(Schema):
    """workspace.notification_sent (§8): how many members a digest line reached, never who."""

    reminder_id: ReminderId
    lead_days: int
    channel: NotificationChannel
    recipient_count: int = Field(ge=1)


# --- Phase 6 payloads (docs/phases/phase-6.md §11) -----------------------------------
#
# §11 names some events by a table (`movement_import.*`, `holding_statement.*`) or by a thing
# that is not an entity (`price.*`, `valuation.*`). An event's type prefix is its entity type
# (the event_entity_type_check of migration 0005), so, as phase 5 did with `bank.*`, they are
# carried on the entity they belong to: the import's and the holdings' events on the account
# (`account.movements_imported`, `account.holdings_reconciled`, ...), a failing price source
# on the asset (`asset.price_source_failed`), and the daily price run and the month-end
# snapshot on the workspace (`workspace.prices_fetched`,
# `workspace.valuation_snapshot_written`). The second owner's consent of D168 is
# `workspace.ai_consent_recorded`. No payload carries an amount of a private line, an
# ownership reason (the household's own words) or a balance.


class AccountKindSet(Schema):
    """account.kind_set (D156): kind, purpose or provider changed."""

    kind: AccountKind
    purpose: AccountPurpose
    provider: str | None = None


class AccountShare(Schema):
    owner_id: UUID
    share_numerator: int = Field(gt=0)
    share_denominator: int = Field(gt=0)


class AccountOwnershipSet(Schema):
    """account.ownership_set (D157): the full new set; the reason stays in the rows."""

    effective_from: date
    shares: list[AccountShare] = Field(min_length=1)
    superseded: list[UUID] = Field(default_factory=list)  # the account_ownership rows replaced


class MovementImportRecorded(Schema):
    """account.movements_imported (D166): the statement.imported of an exchange export."""

    import_id: UUID
    document_id: UUID
    format: MovementImportFormat
    rows_total: int = Field(ge=0)
    rows_new: int = Field(ge=0)
    rows_skipped: int = Field(ge=0)


class MovementImportChecked(Schema):
    """account.movements_reconciled and account.movements_unreconciled (D166)."""

    import_id: UUID
    assets_checked: int = Field(ge=0)
    assets_mismatched: int = Field(ge=0)


class HoldingsChecked(Schema):
    """account.holdings_confirmed, account.holdings_reconciled, account.holdings_mismatch
    (D165 (3)): ids and counts; the units stay in holding_statement."""

    holding_statement_ids: list[UUID] = Field(min_length=1)
    as_of: date
    source: HoldingSource
    document_id: UUID | None = None
    assets_mismatched: int = Field(default=0, ge=0)


class HoldingCorrected(Schema):
    """account.holding_corrected (step 4, 0027): a report read again with other figures; the
    new position supersedes the old one of the same asset and date. Ids only."""

    holding_statement_id: UUID
    superseded_by: UUID
    asset_id: UUID
    as_of: date
    document_id: UUID | None = None


class MovementRecorded(Schema):
    """movement.proposed and movement.confirmed (D165, D166)."""

    account_id: UUID
    asset_id: UUID
    kind: MovementKind
    source: MovementSource
    document_id: UUID | None = None


class MovementSuperseded(Schema):
    """movement.superseded: the row that replaces it (D23 pattern)."""

    superseded_by: UUID


class TransferLinked(Schema):
    """transfer.linked (D162): the two legs, by id only."""

    kind: TransferLinkKind
    matched_by: TransferMatchedBy
    out_transaction_id: UUID | None = None
    out_movement_id: UUID | None = None
    in_transaction_id: UUID | None = None
    in_movement_id: UUID | None = None


class TransferUnlinked(Schema):
    """transfer.unlinked (D162): a person undid a link; a replacing link when there is one."""

    superseded_by: UUID | None = None


class AssetRecorded(Schema):
    """asset.created (D167)."""

    kind: AssetKind
    symbol: str | None = None
    isin: str | None = None
    name: str


class PriceRecorded(Schema):
    """asset.price_recorded: a price typed by a person or read from a document (D167)."""

    price_id: UUID
    on: date
    source: PriceSourceKind
    document_id: UUID | None = None


class PriceSourceFailed(Schema):
    """asset.price_source_failed (D174): one per asset when the card is raised."""

    source: PriceSourceKind
    failed_days: int = Field(ge=1)
    last_price_on: date | None = None


class PricesFetched(Schema):
    """workspace.prices_fetched: one per daily run (§11). Step 5b adds the failed assets' ids
    (never a price or an amount): D174's card counts an asset's failed days from these
    events, on read, so no table holds a fetch's state."""

    sources: list[PriceSourceKind] = Field(min_length=1)
    assets: int = Field(ge=0)
    rows_new: int = Field(ge=0)
    assets_failed: int = Field(ge=0)
    failed_asset_ids: list[UUID] = Field(default_factory=list)


class ValuationSnapshotWritten(Schema):
    """workspace.valuation_snapshot_written (D167): one month-end run."""

    net_worth_run_id: UUID
    as_of: date
    rows: int = Field(ge=1)


class AiConsentRecorded(Schema):
    """workspace.ai_consent_recorded (D168): the second owner's own click, by the member it
    concerns; the first consent is the settings_changed event's consented_by."""

    setting: WorkspaceSetting
    consented_by: UUID


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
    OWNER_INCOME_INPUT_RECORDED = "owner.income_input_recorded"  # phase 4
    OWNER_RETURN_INPUT_RECORDED = "owner.return_input_recorded"  # phase 4b
    OWNER_ANLAGE_N_EXPORTED = "owner.anlage_n_exported"  # phase 4b

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
    # Phase 3
    PROPERTY_TAX_INPUT_RECORDED = "property.tax_input_recorded"
    PROPERTY_ANLAGE_V_EXPORTED = "property.anlage_v_exported"
    # Phase 4
    PROPERTY_SCENARIO_RECORDED = "property.scenario_recorded"

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
    DOCUMENT_RECEIVED_BY_EMAIL = "document.received_by_email"  # phase 5

    WORKSPACE_SETTINGS_CHANGED = "workspace.settings_changed"
    WORKSPACE_INCOME_INPUT_RECORDED = "workspace.income_input_recorded"  # phase 4
    WORKSPACE_RETURN_INPUT_RECORDED = "workspace.return_input_recorded"  # phase 4b
    WORKSPACE_QUESTIONNAIRE_ANSWERED = "workspace.questionnaire_answered"  # phase 4b
    # Phase 5. §8's `workspace.reminder_emails_changed` is not added: the switch is a member of
    # WorkspaceSetting and changes through workspace.settings_changed like the other two.
    WORKSPACE_REMINDER_ACKNOWLEDGED = "workspace.reminder_acknowledged"
    WORKSPACE_NOTIFICATION_SENT = "workspace.notification_sent"
    WORKSPACE_INBOX_POLL_FAILED = "workspace.inbox_poll_failed"
    WORKSPACE_INBOX_POLL_RECOVERED = "workspace.inbox_poll_recovered"
    # Phase 6 (see the payloads' comment for the names)
    WORKSPACE_AI_CONSENT_RECORDED = "workspace.ai_consent_recorded"
    WORKSPACE_PRICES_FETCHED = "workspace.prices_fetched"
    WORKSPACE_VALUATION_SNAPSHOT_WRITTEN = "workspace.valuation_snapshot_written"

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
    # Phase 5 (§8 names them bank.*; see the payloads' comment)
    ACCOUNT_CONNECTION_CREATED = "account.connection_created"
    ACCOUNT_CONNECTION_UPDATED = "account.connection_updated"
    ACCOUNT_SYNC_STARTED = "account.sync_started"
    ACCOUNT_SYNC_APPROVAL_NEEDED = "account.sync_approval_needed"
    ACCOUNT_SYNC_COMPLETED = "account.sync_completed"
    ACCOUNT_SYNC_FAILED = "account.sync_failed"
    ACCOUNT_STATEMENT_FETCHED = "account.statement_fetched"  # D154, step 2b
    ACCOUNT_BANK_PARAMETERS_READ = "account.bank_parameters_read"  # phase 6 D173 (7), step 3
    # Phase 6
    ACCOUNT_KIND_SET = "account.kind_set"
    ACCOUNT_OWNERSHIP_SET = "account.ownership_set"
    ACCOUNT_MOVEMENTS_IMPORTED = "account.movements_imported"
    ACCOUNT_MOVEMENTS_RECONCILED = "account.movements_reconciled"
    ACCOUNT_MOVEMENTS_UNRECONCILED = "account.movements_unreconciled"
    ACCOUNT_HOLDINGS_CONFIRMED = "account.holdings_confirmed"
    ACCOUNT_HOLDINGS_RECONCILED = "account.holdings_reconciled"
    ACCOUNT_HOLDINGS_MISMATCH = "account.holdings_mismatch"
    ACCOUNT_HOLDING_CORRECTED = "account.holding_corrected"  # step 4, 0027

    ASSET_CREATED = "asset.created"  # phase 6
    ASSET_UPDATED = "asset.updated"
    ASSET_PRICE_RECORDED = "asset.price_recorded"
    ASSET_PRICE_SOURCE_FAILED = "asset.price_source_failed"

    MOVEMENT_PROPOSED = "movement.proposed"  # phase 6
    MOVEMENT_CONFIRMED = "movement.confirmed"
    MOVEMENT_SUPERSEDED = "movement.superseded"

    TRANSFER_LINKED = "transfer.linked"  # phase 6
    TRANSFER_UNLINKED = "transfer.unlinked"

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
    E.OWNER_INCOME_INPUT_RECORDED: EventSpec(
        IncomeInputRecorded, "forbidden", "Expected gross pay or deductions recorded"
    ),
    E.OWNER_RETURN_INPUT_RECORDED: EventSpec(
        ReturnInputRecorded, "forbidden", "Employment fact or pay document recorded"
    ),
    E.OWNER_ANLAGE_N_EXPORTED: EventSpec(AnlageNExported, "forbidden", "Anlage N sheet exported"),
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
    E.PROPERTY_TAX_INPUT_RECORDED: EventSpec(
        TaxInputRecorded, "forbidden", "Tax decision or certificate figure recorded"
    ),
    E.PROPERTY_ANLAGE_V_EXPORTED: EventSpec(
        AnlageVExported, "forbidden", "Anlage V draft exported"
    ),
    E.PROPERTY_SCENARIO_RECORDED: EventSpec(
        ScenarioRecorded, "forbidden", "Projection scenario recorded"
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
    # Phase 5
    E.DOCUMENT_RECEIVED_BY_EMAIL: EventSpec(
        DocumentReceivedByEmail, "forbidden", "Received as a forwarded mail's attachment"
    ),
    E.WORKSPACE_SETTINGS_CHANGED: EventSpec(
        WorkspaceSettingsChanged, "forbidden", "Workspace settings changed"
    ),
    E.WORKSPACE_INCOME_INPUT_RECORDED: EventSpec(
        IncomeInputRecorded, "forbidden", "Bescheid zvE or typed tax rate recorded"
    ),
    E.WORKSPACE_RETURN_INPUT_RECORDED: EventSpec(
        ReturnInputRecorded, "forbidden", "Child or household tax fact recorded"
    ),
    E.WORKSPACE_QUESTIONNAIRE_ANSWERED: EventSpec(
        QuestionnaireAnswered, "forbidden", "Yearly tax question answered"
    ),
    # Phase 5
    E.WORKSPACE_REMINDER_ACKNOWLEDGED: EventSpec(
        ReminderAcknowledged, "forbidden", "Reminder done, snoozed, muted or unmuted"
    ),
    E.WORKSPACE_NOTIFICATION_SENT: EventSpec(
        NotificationSent, "forbidden", "Reminder sent by email"
    ),
    E.WORKSPACE_INBOX_POLL_FAILED: EventSpec(
        InboxPollFailed, "forbidden", "Email-in cannot log in, find its label or reach Gmail"
    ),
    E.WORKSPACE_INBOX_POLL_RECOVERED: EventSpec(
        InboxPollRecovered, "forbidden", "Email-in works again"
    ),
    # Phase 6
    E.WORKSPACE_AI_CONSENT_RECORDED: EventSpec(
        AiConsentRecorded, "forbidden", "Second owner's consent to the model recorded"
    ),
    E.WORKSPACE_PRICES_FETCHED: EventSpec(PricesFetched, "forbidden", "Daily prices fetched"),
    E.WORKSPACE_VALUATION_SNAPSHOT_WRITTEN: EventSpec(
        ValuationSnapshotWritten, "forbidden", "Month-end net worth snapshot written"
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
    # Phase 5
    E.ACCOUNT_CONNECTION_CREATED: EventSpec(
        BankConnectionRecorded, "forbidden", "Bank connection (FinTS) recorded"
    ),
    E.ACCOUNT_CONNECTION_UPDATED: EventSpec(
        BankConnectionRecorded, "forbidden", "Bank connection (FinTS) changed"
    ),
    E.ACCOUNT_SYNC_STARTED: EventSpec(BankSyncStarted, "forbidden", "Bank sync started"),
    E.ACCOUNT_SYNC_APPROVAL_NEEDED: EventSpec(
        BankSyncApprovalNeeded, "forbidden", "Bank sync waits for a pushTAN approval"
    ),
    E.ACCOUNT_SYNC_COMPLETED: EventSpec(BankSyncCompleted, "forbidden", "Bank sync finished"),
    E.ACCOUNT_SYNC_FAILED: EventSpec(BankSyncFailed, "forbidden", "Bank sync failed"),
    E.ACCOUNT_STATEMENT_FETCHED: EventSpec(
        BankStatementFetched, "forbidden", "Bank PDF statement fetched (FinTS)"
    ),
    E.ACCOUNT_BANK_PARAMETERS_READ: EventSpec(
        BankParametersRead, "forbidden", "Bank parameters read (FinTS)"
    ),
    # Phase 6
    E.ACCOUNT_KIND_SET: EventSpec(AccountKindSet, "forbidden", "Account kind or purpose set"),
    E.ACCOUNT_OWNERSHIP_SET: EventSpec(
        AccountOwnershipSet, "optional", "Account ownership shares set"
    ),
    E.ACCOUNT_MOVEMENTS_IMPORTED: EventSpec(
        MovementImportRecorded, "optional", "Exchange export imported (date = last movement)"
    ),
    E.ACCOUNT_MOVEMENTS_RECONCILED: EventSpec(
        MovementImportChecked, "forbidden", "Exchange export reconciled to its balances"
    ),
    E.ACCOUNT_MOVEMENTS_UNRECONCILED: EventSpec(
        MovementImportChecked, "forbidden", "Exchange export does not match its balances"
    ),
    E.ACCOUNT_HOLDINGS_CONFIRMED: EventSpec(
        HoldingsChecked, "optional", "Holdings confirmed (date = as of)"
    ),
    E.ACCOUNT_HOLDINGS_RECONCILED: EventSpec(
        HoldingsChecked, "optional", "Holdings match the movements (date = as of)"
    ),
    E.ACCOUNT_HOLDINGS_MISMATCH: EventSpec(
        HoldingsChecked, "optional", "Holdings differ from the movements (date = as of)"
    ),
    E.ACCOUNT_HOLDING_CORRECTED: EventSpec(
        HoldingCorrected, "optional", "A report's position corrected (date = as of)"
    ),
    E.ASSET_CREATED: EventSpec(AssetRecorded, "forbidden", "Asset recorded"),
    E.ASSET_UPDATED: EventSpec(Updated, "forbidden", "Asset name or price ids changed"),
    E.ASSET_PRICE_RECORDED: EventSpec(PriceRecorded, "optional", "Price recorded"),
    E.ASSET_PRICE_SOURCE_FAILED: EventSpec(
        PriceSourceFailed, "forbidden", "Price source failing for this asset"
    ),
    E.MOVEMENT_PROPOSED: EventSpec(MovementRecorded, "optional", "Movement proposed"),
    E.MOVEMENT_CONFIRMED: EventSpec(MovementRecorded, "optional", "Movement confirmed"),
    E.MOVEMENT_SUPERSEDED: EventSpec(MovementSuperseded, "forbidden", "Movement corrected"),
    E.TRANSFER_LINKED: EventSpec(TransferLinked, "forbidden", "Transfer legs linked"),
    E.TRANSFER_UNLINKED: EventSpec(TransferUnlinked, "forbidden", "Transfer link undone"),
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
