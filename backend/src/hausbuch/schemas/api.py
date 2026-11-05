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

from datetime import date
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from .common import (
    Area,
    Fraction,
    IbanInput,
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
    ManualDocumentFields,
    RetentionResult,
)
from .enums import LinkTarget, PropertyKind
from .loan import LoanCreate
from .property import Address, OwnerShare, reject_raw_tax_number
from .tenancy import InitialRent, TenancyCreate

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


class DateCorrection(Schema):
    """Corrects a milestone date: purchase date (Übergang) or move-in."""

    new_date: date


class OwnershipBody(Schema):
    shares: list[OwnerShare] = Field(min_length=1)  # must sum to exactly 1 (service rule)
    effective_date: date | None = None


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
    typing: DocumentTyping
    fields: ManualDocumentFields | None = None


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
    retention: RetentionResult
