"""API-only models: the error envelope, PATCH bodies, action bodies and composite reads.

Spec: docs/phases/phase-1-step-3.md §3.5 (errors) and §4 (endpoints). Request bodies that
create an entity reuse the *Create schemas verbatim; these models cover the rest. Like every
schema they reject unknown fields and are frozen, and they import nothing from the services.

PATCH semantics (§4.1): a field absent from the JSON is unchanged; null clears it where the
field is nullable. A non-nullable field (a name, a label) has `Field(default=None)` only so
that it can be absent: the default is never validated, but an explicit null fails.
Patch.changes() is exactly the keyword arguments the matching update_* service takes (the
services' UNSET convention).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from .allocation import Allocation, AllocationSet, MatchingRule
from .bank import Transaction
from .common import (
    Area,
    Fraction,
    IbanInput,
    Money,
    NonNegativeMoney,
    PositiveMoney,
    Rate,
    Rooms,
    Schema,
    TaxRate,
    Year,
)
from .document import (
    Document,
    DocumentExtraction,
    DocumentLink,
    DocumentTyping,
    HandFields,
    ManualDocumentFields,
    RetentionResult,
)
from .enums import (
    AcquisitionCostKind,
    DepositForm,
    LinkTarget,
    PropertyKind,
    ProposalStatus,
    RentModel,
)
from .loan import LoanCreate
from .property import Address, OwnerShare, reject_raw_tax_number
from .proposal import Proposal, RecordEntity
from .tenancy import InitialRent, TenancyCreate, TenancyParty

# Validation errors never echo input (spec amendment 1): bodies that carry a tax reference or
# an IBAN hide it from the error's text, like their Create models.
_HIDE_INPUT = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

# --- Errors (§3.5) ---------------------------------------------------------------


class ErrorKind(StrEnum):
    """ErrorResponse.error: the stable string the frontend switches on."""

    VALIDATION = "validation"  # 422: body/query/path failed validation; see detail
    RULE_VIOLATION = "rule_violation"  # 422: a rule or invariant code; see codes / code
    INVALID = "invalid"  # 422: a service refused the request; see message
    UNAUTHENTICATED = "unauthenticated"  # 401
    FORBIDDEN_ORIGIN = "forbidden_origin"  # 403: failed same-origin check (§6.6)
    NOT_FOUND = "not_found"  # 404: see entity_type, entity_id
    CONFLICT = "conflict"  # 409: see code ("tenancy.overlap"), message, constraint
    RETENTION_HOLD = "retention_hold"  # 409: see result
    PAYLOAD_TOO_LARGE = "payload_too_large"  # 413
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"  # 415
    SHUTTING_DOWN = "shutting_down"  # 503, with Retry-After
    INTERNAL = "internal"  # 500: request_id only; details go to the log


class FieldError(Schema):
    """One validation error, whitelisted to these three keys: never input, ctx or url (§3.6)."""

    loc: list[str | int]
    msg: str
    type: str


class ErrorResponse(Schema):
    """Every non-2xx body. Only the fields that apply to `error` are set (see ErrorKind)."""

    error: ErrorKind
    message: str | None = None
    detail: list[FieldError] | None = None
    codes: list[str] | None = None
    code: str | None = None
    at_number: int | None = None
    entity_type: str | None = None  # an EntityType, or a row name such as "rent_schedule"
    entity_id: UUID | None = None
    constraint: str | None = None  # the database constraint behind a conflict, when known
    result: RetentionResult | None = None
    request_id: UUID | None = None


# --- PATCH bodies ------------------------------------------------------------------


class Patch(Schema):
    """Base for partial updates. Absent = unchanged, null = clear (where nullable)."""

    def changes(self) -> dict[str, Any]:
        """The fields the client sent, as keyword arguments for the update_* service."""
        return {name: getattr(self, name) for name in sorted(self.model_fields_set)}


class OwnerPatch(Patch):
    model_config = _HIDE_INPUT

    full_name: str = Field(default=None, min_length=1)
    tax_id_ref: str | None = None
    marginal_tax_rate: TaxRate | None = None

    @field_validator("tax_id_ref")
    @classmethod
    def _not_a_raw_tax_number(cls, value: str | None) -> str | None:
        return reject_raw_tax_number(value)


class PropertyPatch(Patch):
    """Non-milestone fields only: the purchase dates and price change through corrections."""

    label: str = Field(default=None, min_length=1, max_length=100)
    kind: PropertyKind = Field(default=None)
    address: Address = Field(default=None)
    build_year: Year = Field(default=None)


class AcquisitionCostPatch(Patch):
    """PATCH /acquisition-costs/{id}: null clears paid_on, the Beleg (document_id) or the note."""

    kind: AcquisitionCostKind = Field(default=None)
    amount: PositiveMoney = Field(default=None)
    paid_on: date | None = None
    document_id: UUID | None = None
    note: str | None = None


class UnitPatch(Patch):
    label: str = Field(default=None, min_length=1, max_length=50)
    floor_area_m2: Area = Field(default=None)
    rooms: Rooms | None = None
    mea: Fraction | None = None


class TenantPatch(Patch):
    model_config = _HIDE_INPUT

    full_name: str = Field(default=None, min_length=1)
    email: str | None = None
    phone: str | None = None
    iban: IbanInput | None = None  # write-only; null removes it


class AccountPatch(Patch):
    """PATCH /accounts/{id}. The IBAN cannot change: a different IBAN is another account.
    Moving the anchor (opening_balance, opening_balance_on) re-checks every import."""

    model_config = _HIDE_INPUT

    label: str = Field(default=None, min_length=1, max_length=100)
    bank: str = Field(default=None, min_length=1)
    opening_balance: Money = Field(default=None)
    opening_balance_on: date = Field(default=None)
    is_owner_account: bool = Field(default=None)


class LoanPatch(Patch):
    model_config = _HIDE_INPUT

    lender: str = Field(default=None, min_length=1)
    contract_number: str | None = None
    nominal_rate: Rate = Field(default=None)
    monthly_payment: PositiveMoney = Field(default=None)
    first_payment_on: date = Field(default=None)
    fixed_until: date | None = None
    follow_up_rate: Rate | None = None
    account_iban: IbanInput | None = None  # write-only; null removes it


class TenancyPatch(Patch):
    """PATCH /tenancies/{id}: the non-milestone fields. Move-in, move-out, notice and contract
    date change through their corrections, the parties through PUT .../parties."""

    model_config = _HIDE_INPUT

    fixed_term_until: date | None = None
    rent_model: RentModel = Field(default=None)
    deposit_amount: NonNegativeMoney = Field(default=None)
    deposit_form: DepositForm = Field(default=None)
    deposit_bank: str | None = None
    deposit_iban: IbanInput | None = None  # write-only; null removes it


class RentRowCorrection(Patch):
    """POST /rent-schedule/{row_id}/corrections: the correction row's values; an absent field
    keeps the wrong row's value (correct_rent_row)."""

    effective_from: date = Field(default=None)
    cold_rent: NonNegativeMoney = Field(default=None)
    operating_costs_prepayment: NonNegativeMoney = Field(default=None)
    document_id: UUID | None = None


# --- Action bodies -----------------------------------------------------------------


class NotarizedOnCorrection(Schema):
    new_date: date
    purchase_price: PositiveMoney | None = None  # None = keep the recorded price


class DisbursementCorrection(Schema):
    """POST /loans/{id}/corrections/disbursement: the payout date and/or the principal; an
    absent (or null) field keeps the recorded value."""

    new_date: date | None = None
    principal: PositiveMoney | None = None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if self.new_date is None and self.principal is None:
            raise ValueError("give new_date, principal or both")
        return self


class DateCorrection(Schema):
    """Corrects a milestone date: purchase date (Übergang) or move-in."""

    new_date: date


class OwnershipBody(Schema):
    shares: list[OwnerShare] = Field(min_length=1)  # must sum to exactly 1 (service rule)
    effective_date: date | None = None


class TenancyPartiesBody(Schema):
    """PUT /tenancies/{id}/parties: the whole list (exactly one primary; service rule)."""

    parties: list[TenancyParty] = Field(min_length=1)
    effective_date: date | None = None  # from when, if known


class DepositReceivedBody(Schema):
    amount: PositiveMoney
    received_on: date


class NoticeBody(Schema):
    given_by: Literal["tenant", "landlord"]
    given_on: date
    ends_on: date


class EndTenancyBody(Schema):
    end_date: date


class ErasureBody(Schema):
    on_request: bool = False  # the tenant asked and the landlord honours it (D5)


class DocumentTypingBody(Schema):
    """PUT /documents/{id}/typing. `shown_version` is the extraction version the hand fields
    were shown from (0: none yet), required with `fields`; a save from a version that is no
    longer current is refused (document.extraction_stale, phase 2 D37)."""

    typing: DocumentTyping
    fields: ManualDocumentFields | None = None
    shown_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _version_with_fields(self) -> Self:
        if self.fields is not None and self.shown_version is None:
            raise ValueError("shown_version is required with fields")
        return self


class LinkBody(Schema):
    target_type: LinkTarget
    target_id: UUID


# --- Create requests that carry one extra argument of the service -------------------


class TenancyCreateRequest(TenancyCreate):
    """POST /tenancies: TenancyCreate plus the first rent row (create_tenancy's initial_rent)."""

    initial_rent: InitialRent | None = None

    def as_create(self) -> TenancyCreate:
        """The TenancyCreate the service takes, without initial_rent."""
        return TenancyCreate.model_validate(self.model_dump(exclude={"initial_rent"}))


class LoanCreateRequest(LoanCreate):
    """POST /loans: LoanCreate plus the contract date (create_loan's signed_on)."""

    signed_on: date | None = None

    def as_create(self) -> LoanCreate:
        """The LoanCreate the service takes, without signed_on."""
        return LoanCreate.model_validate(self.model_dump(exclude={"signed_on"}))


# --- Composite reads -----------------------------------------------------------------


class DocumentDetail(Schema):
    """GET /documents/{id}: one read transaction over the document and its layers."""

    document: Document
    links: list[DocumentLink]
    extraction: DocumentExtraction | None  # the current (highest) version, if typed fields
    # The typing form's "Felder (von Hand)", read from `extraction` through D37's mapping, so
    # the client never repeats it.
    hand_fields: HandFields
    retention: RetentionResult
    media_type: str  # the file's, as uploaded (document_file.media_type)
    # Whether GET /documents/{id}/original?disposition=inline shows it in the browser (§4.7:
    # PDF and raster images only); otherwise the original is always a download.
    opens_inline: bool


class LinkTargetRef(Schema):
    """One entity a document is linked to (a document_link row without its own id)."""

    target_type: LinkTarget
    target_id: UUID


class RelatedDocument(Schema):
    """GET /properties/{id}/documents and GET /units/{id}/documents: a document linked to the
    target or to one of its children (units, tenancies, loans), with every such link."""

    document: Document
    # The direct link (target_type property or unit) first, then unit, tenancy and loan
    # links, each group by target_id.
    via: list[LinkTargetRef]


class DepositReceipt(Schema):
    """GET /tenancies/{id}/deposits: one Kaution payment received (a tenancy.deposit_received
    event; the latest of its chain, should one ever be corrected)."""

    event_id: UUID
    amount: PositiveMoney
    received_on: date  # the event's effective date
    recorded_at: datetime


# --- Phase 2 (docs/phases/phase-2.md §6.1, §8.3) --------------------------------------
# Bodies that belong to one module live there: ProposalApplyBody (proposal.py),
# AccountCreate, DeclareBalanceBody and DuplicateDecisionBody (bank.py).


class WorkspaceSettings(Schema):
    """GET /workspace/settings. Both default to false (D16, D26)."""

    ai_tenant_documents: bool
    auto_accept_llm: bool


class WorkspaceSettingsPatch(Patch):
    ai_tenant_documents: bool = Field(default=None)
    auto_accept_llm: bool = Field(default=None)


class DocumentUploadResult(Schema):
    """POST /documents with ai = true (§6.1): the document plus the queued proposal to
    poll. proposal_id is null when the upload did not ask for a proposal."""

    document: Document
    proposal_id: UUID | None = None
    proposal_status: ProposalStatus | None = None


class ProposalRerunBody(Schema):
    """POST /documents/{id}/proposals (optional body): read the document again. The
    tenant-data flag is copied from the document's latest proposal; true adds it, false
    never removes it (D16)."""

    contains_tenant_data: bool = False


class AppliedEntity(Schema):
    """One record of an applied proposal and the entity it created or patched (for a link,
    the entity the document was linked to). entity_id is null for a skipped record."""

    key: str
    entity_type: RecordEntity
    mode: Literal["create", "patch", "correction", "unchanged"]
    skipped: bool = False
    entity_id: UUID | None = None


class ProposalApplyResult(Schema):
    """POST /proposals/{id}/apply: the applied proposal and what each record became."""

    proposal: Proposal
    applied: list[AppliedEntity]


class ConfirmAllocationsBody(Schema):
    """PUT /transactions/{id}/allocations: the user's allocation set and the "Künftig immer
    so zuordnen" checkbox.

    Only the set's allocations are read: the rows are written with method `user` and
    confidence 1, whatever method, confidence, rule_id or ai_call_id the body carries. A set
    equal to the current proposed rows confirms those rows as they are (the proposer's method
    stays); one equal to the current confirmed rows changes nothing.

    learn_rule: null = the server default (TransactionAllocations.learn_rule_default for this
    set); true for a set no rule can be learned from is refused with rule.not_learnable.
    The patterns (case-insensitive regular expressions, refused with rule.invalid_pattern
    when they do not compile) narrow the rule, and let a line without an IBAN, or a
    counterparty a matcher owns (D38), have one."""

    allocation_set: AllocationSet
    learn_rule: bool | None = None
    counterparty_name_pattern: str | None = Field(default=None, max_length=200)
    purpose_pattern: str | None = Field(default=None, max_length=200)


class TransactionAllocations(Schema):
    """GET / PUT /transactions/{id}/allocations: the line, its current allocation rows
    (proposed or confirmed; none = not matched) and the rule checkbox.

    can_learn_rule: whether a single-category answer for this line can become a rule
    without a typed pattern (false without an IBAN, and for a counterparty a matcher owns,
    D38). learn_rule_default: the checkbox's default for the current rows (off when a rule
    cannot be learned from them, or a category never auto-confirms, review F6).
    rule: the rule this PUT created, if any."""

    transaction: Transaction
    allocations: list[Allocation]
    can_learn_rule: bool
    learn_rule_default: bool
    rule: MatchingRule | None = None


class InboxItemKind(StrEnum):
    """GET /inbox (§8.3): one list, newest first."""

    PROPOSAL = "proposal"  # a ready proposal to review
    READ_FAILED = "read_failed"  # "Konnte nicht gelesen werden"
    BLOCKED_TENANT_DATA = "blocked_tenant_data"
    IMPORT_UNRECONCILED = "import_unreconciled"
    IMPORT_PENDING_BALANCE = "import_pending_balance"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    ALLOCATION_PROPOSED = "allocation_proposed"  # a proposed allocation set to confirm
    UNMATCHED = "unmatched"  # "Nicht zugeordnet": no allocation set at all


class InboxItem(Schema):
    """One thing to act on, with a one-line reasoning and one question. Exactly the ids its
    action endpoint needs are set."""

    kind: InboxItemKind
    created_at: datetime
    reasoning: str | None = None
    question: str | None = None
    document_id: UUID | None = None
    proposal_id: UUID | None = None
    import_id: UUID | None = None
    possible_duplicate_id: UUID | None = None
    transaction_id: UUID | None = None
