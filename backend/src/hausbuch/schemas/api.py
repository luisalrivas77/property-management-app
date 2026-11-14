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

from .allocation import Allocation, AllocationCategory, AllocationSet, MatchingRule
from .bank import StatementFileImport, StatementFileOffer, Transaction
from .common import (
    Area,
    Fraction,
    IbanInput,
    LivingArea,
    Money,
    NonNegativeMoney,
    PositiveMoney,
    Rate,
    Rooms,
    Schema,
    Year,
)
from .document import (
    Document,
    DocumentExplanation,
    DocumentExtraction,
    DocumentLink,
    DocumentTyping,
    HandFields,
    ManualDocumentFields,
    RetentionResult,
)
from .enums import (
    AccountKind,
    AccountPurpose,
    AcquisitionCostKind,
    DepositForm,
    LinkTarget,
    OperatingCostsTerms,
    PropertyKind,
    ProposalStatus,
    RentModel,
    StateCode,
)
from .household import OpenTransfer
from .loan import LoanCreate
from .property import Address, OwnerShare, reject_raw_tax_number
from .proposal import Proposal, RecordEntity
from .tax import TaxInputCreate
from .tenancy import InitialRent, RentDueDay, TenancyCreate, TenancyParty

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
    church_tax: bool = Field(default=None)  # phase 4 D90; not nullable
    tax_state: StateCode | None = None  # null clears it (only without church tax)

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
    living_area_m2: LivingArea | None = None  # phase 3 D71; null clears it


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
    # Phase 6 D156: a bank account's kind stays a bank kind (its IBAN is its identity); a
    # change of purpose re-checks the waiting lines and is refused while the account holds
    # a booking of a category the new purpose does not allow (D159). provider: null clears.
    kind: AccountKind = Field(default=None)
    purpose: AccountPurpose = Field(default=None)
    provider: str | None = Field(default=None, min_length=1, max_length=100)


class LoanPatch(Patch):
    model_config = _HIDE_INPUT

    lender: str = Field(default=None, min_length=1)
    contract_number: str | None = None
    nominal_rate: Rate = Field(default=None)
    monthly_payment: PositiveMoney = Field(default=None)
    first_payment_on: date = Field(default=None)
    fixed_until: date | None = None
    follow_up_rate: Rate | None = None
    interest_only_before_first_payment: bool = Field(default=None)
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
    rent_due_day: RentDueDay = Field(default=None)  # phase 3 D67
    operating_costs_terms: OperatingCostsTerms = Field(default=None)  # phase 3 D72


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


class PaymentProofBody(Schema):
    """Phase 3 D149: the bill an existing payment proof pays (the to-do's „Vorhandenen
    Nachweis zuordnen“)."""

    bill_id: UUID


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
    # Phase 6 D177: a depot document in plain words (what it is, what it means for the
    # account, what Hausbuch does with it); None for any other document.
    explanation: DocumentExplanation | None = None
    # Phase 6 D176 (3), step 4: the statement imports this document is the original of (the
    # account it went to), and, for a card or depot statement uploaded through Dokumente that
    # no account holds yet, the accounts „Auf Konto importieren“ offers.
    statement_imports: list[StatementFileImport] = Field(default_factory=list)
    statement_offer: StatementFileOffer | None = None


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
    """GET /workspace/settings. All three default to false (D16, D26; phase 5 D123)."""

    ai_tenant_documents: bool
    auto_accept_llm: bool
    reminder_emails: bool
    # Phase 6 (migration 0025, D168): switching it on records the member's consent; the
    # other member consents by POST /workspace/settings/ai-consent. Active (private lines
    # reach the model) once two members consented since it was switched on.
    ai_private_accounts: bool = False
    ai_private_accounts_consented_by: list[UUID] = Field(default_factory=list)
    ai_private_accounts_active: bool = False
    # Not a workspace column: whether the process can send mail (TEM configured), set by the
    # route so the settings page can say when the switch has no effect yet.
    reminder_emails_available: bool = False


class WorkspaceSettingsPatch(Patch):
    ai_tenant_documents: bool = Field(default=None)
    auto_accept_llm: bool = Field(default=None)
    reminder_emails: bool = Field(default=None)
    ai_private_accounts: bool = Field(default=None)


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
    counterparty a matcher owns (D38), have one.
    same_amount_only (D49): the answer to "Gehören die auch zu …?" when other amounts from
    the same IBAN are waiting: true = the rule books only payments of this line's amount (the
    latest confirmed one of its category, read from the ledger); ignored without learn_rule
    and for a rule whose split method checks the amount itself.
    rebook_earlier (phase 6 D160, 14 Nov 2026): with learn_rule, when the name rule is refused
    only for the name's other confirmed lines (rule.conflicts, rule.too_broad), those lines
    are re-booked to this answer's category first, then the rule is learned; refused as
    before when one of them is not a single confirmed row of a learnable household code."""

    allocation_set: AllocationSet
    learn_rule: bool | None = None
    counterparty_name_pattern: str | None = Field(default=None, max_length=200)
    purpose_pattern: str | None = Field(default=None, max_length=200)
    same_amount_only: bool = False
    rebook_earlier: bool = False


class WaitingAmount(Schema):
    """An amount other lines from the same IBAN and sign are waiting with, and how many."""

    amount: Money
    count: int = Field(ge=1)


class NameCategoryCount(Schema):
    """How many earlier lines of the payee are confirmed under one category (phase 6 D160)."""

    category: AllocationCategory
    count: int = Field(ge=1)


class TransactionAllocations(Schema):
    """GET / PUT /transactions/{id}/allocations: the line, its current allocation rows
    (proposed or confirmed; none = not matched) and the rule checkbox.

    can_learn_rule: whether a single-category answer for this line can become a rule
    without a typed pattern (false without an IBAN, and for a counterparty a matcher owns,
    D38). learn_rule_default: the checkbox's default for the current rows (off when a rule
    cannot be learned from them, or a category never auto-confirms, review F6).
    no_default_rule_categories: the categories that never auto-confirm (threshold None in
    rules.review.THRESHOLDS), in taxonomy order, so the UI can default the checkbox for rows
    the person types (on unless one of these is among them, §8.1).
    waiting_other_amounts (D49): when a rule can be learned, the other amounts of the lines
    still waiting from the same IBAN hash and sign (reconciled import, not a duplicate, no
    confirmed row), each with its count, most frequent first, at most five; the panel then
    asks whether those belong to the same category (same_amount_only).
    unlearnable_categories: the categories the line's account takes for which a one-row
    answer can never become a rule, with or without a typed pattern (phase 6 D160 on a
    private account: the transfer, depot and tax-relevant codes and those without a
    threshold; empty on the rental account), in taxonomy order, so the UI greys the box and
    says why instead of letting rule.not_learnable refuse the answer.
    rule: the rule this PUT created, if any.
    name_history (phase 6 D160): for a private line whose rule would be on its name (no
    IBAN, or a shared one), the payee's other confirmed lines of the same sign by category,
    most frequent first: what rebook_earlier would re-book. rebooked: how many earlier lines
    this PUT re-booked."""

    transaction: Transaction
    allocations: list[Allocation]
    can_learn_rule: bool
    learn_rule_default: bool
    no_default_rule_categories: list[AllocationCategory]
    waiting_other_amounts: list[WaitingAmount] = Field(default_factory=list)
    unlearnable_categories: list[AllocationCategory] = Field(default_factory=list)
    rule: MatchingRule | None = None
    name_history: list[NameCategoryCount] = Field(default_factory=list)
    rebooked: int = 0


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
    # phase 4b D110: last year's Jahresfragen, from 1 January until every slot is answered
    QUESTIONNAIRE_OPEN = "questionnaire_open"
    # Phase 5 (§5.4, §6.2, D117, D120, D122); the screen groups them under "Automatisch".
    BANK_APPROVAL_NEEDED = "bank_approval_needed"  # a scheduled sync met a pushTAN demand
    BANK_SYNC_FAILED = "bank_sync_failed"  # a run failed and waits for a person
    BANK_BALANCE_DRIFT = "bank_balance_drift"  # HKSAL differs from the last reconciled closing
    # 14 Nov 2026: the bank refuses the PDF statement fetch for lack of permission (9370)
    BANK_STATEMENTS_REFUSED = "bank_statements_refused"
    EMAIL_RECEIVED = "email_received"  # attachments of forwarded mails, last 24 h
    EMAIL_FAILED = "email_failed"  # a forwarded mail failed three times
    EMAIL_INBOX_FAILED = "email_inbox_failed"  # the poll cannot log in, open its label or connect
    REMINDER = "reminder"  # an action reminder visible today
    # Phase 6 D162: a transfer leg to link (several counterparts fit, or one fits unlinked) or
    # one past its window without a counterpart; `transfer` carries the leg and candidates.
    TRANSFER_LINK = "transfer_link"
    TRANSFER_UNMATCHED = "transfer_unmatched"
    # Phase 6 D161: a private line paying a property bill that has no private_cost row yet
    # (path 2, `private_costs` as POST /transactions/{id}/private-costs records them), and a
    # private line booked hh_property_cost without a bill (path 3, „Beleg hochladen“).
    PROPERTY_COST_ROW = "property_cost_row"
    PROPERTY_COST_BILL = "property_cost_bill"
    # Review F2: the bill's private_cost rows are in another year than the confirmed private
    # line paid them; `tax_year` is the year paid (POST /transactions/{id}/private-costs/year).
    PROPERTY_COST_YEAR = "property_cost_year"
    # Review F4: the line's attached bill yields no row yet (not read, not a bill, no property,
    # no amount or date); `document_id` is the document to open.
    PROPERTY_COST_DOCUMENT = "property_cost_document"
    # Phase 6 step 4 (principle 7): a depot's latest report disagrees with the confirmed
    # movements to its date, re-checked on every read; `account_id` is the depot,
    # `document_id` the report.
    HOLDINGS_MISMATCH = "holdings_mismatch"
    # Phase 6 step 5b (D174 (4), D167 (1)), computed on read; `asset_id` is the asset:
    # an ETF whose issuer's NAV file failed on seven daily runs in a row („Kurs
    # eintragen“), and a held asset nothing can price („Kursquelle wählen“).
    PRICE_SOURCE_FAILED = "price_source_failed"
    PRICE_NO_SOURCE = "price_no_source"
    # Phase 6 step 5a (D166 amended, D178): an exchange import waiting for the balances the
    # exchange shows, or whose typed balances disagree (`import_id`, `account_id`); its
    # proposed coin withdrawals, answered all at once (`import_id`); one coin movement that
    # waits for a person, a crypto deposit or an unknown row (`movement_id`).
    EXCHANGE_PENDING_BALANCE = "exchange_pending_balance"
    EXCHANGE_UNRECONCILED = "exchange_unreconciled"
    EXCHANGE_WITHDRAWALS = "exchange_withdrawals"
    EXCHANGE_MOVEMENT = "exchange_movement"


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
    tax_year: int | None = None  # questionnaire_open's year; property_cost_year's year paid
    account_id: UUID | None = None  # phase 5: the bank_* kinds; phase 6: a private line
    sync_id: UUID | None = None  # phase 5: bank_approval_needed, bank_sync_failed
    reminder_id: str | None = Field(default=None, max_length=160)  # phase 5: reminder
    property_id: UUID | None = None  # phase 6: the property_cost_* kinds
    movement_id: UUID | None = None  # phase 6 step 5a: exchange_movement
    transfer: OpenTransfer | None = None  # phase 6: the transfer_* kinds
    asset_id: UUID | None = None  # phase 6 step 5b: the price_* kinds
    private_costs: list[TaxInputCreate] = Field(default_factory=list)  # property_cost_row
    # read_failed: the proposal's error code; proposal.bank_file_not_read (a card or depot
    # statement) is answered with „Auf Konto importieren“, not a new read (phase 6 D176 (3));
    # proposal.exchange_export_not_read (step 5a, D166) points to the exchange account's page
    error_code: str | None = None
    # allocation_proposed (phase 6 D159 amended 13 Nov 2026): the proposal's category when it
    # has one part, and whether „Alle bestätigen“ may confirm it with the others of that
    # category (a private line, one part, a plain household code, no question).
    category: AllocationCategory | None = None
    bulk_confirmable: bool = False


class AiRetryResult(Schema):
    """POST /inbox/ai-retry (D50): how many waiting lines were sent to the model again."""

    queued: int
    private_held_back: int


class ConfirmCategoryBody(Schema):
    """POST /inbox/confirm-category (phase 6 D159 amended 13 Nov 2026, „Alle bestätigen“):
    the lines the person saw in the list, and the category they were listed under. Never a
    category alone: only these lines are confirmed."""

    category: AllocationCategory
    transaction_ids: list[UUID] = Field(min_length=1, max_length=1000)


class ConfirmCategorySkip(Schema):
    """A listed line left as it is: its proposal is no longer a waiting one-part proposal of
    the category without a question (it changed since the list was shown), or the line no
    longer counts (merged as a duplicate, its import not reconciled)."""

    transaction_id: UUID
    reason: Literal["not_open", "not_waiting", "split", "category_changed", "question"]


class ConfirmCategoryResult(Schema):
    confirmed: int
    skipped: list[ConfirmCategorySkip]
