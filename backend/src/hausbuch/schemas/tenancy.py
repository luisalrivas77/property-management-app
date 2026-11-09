"""Tenant, Tenancy and the append-only rent schedule."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from .common import (
    IbanInput,
    MaskedIban,
    MutableRecord,
    NonNegativeMoney,
    Schema,
)
from .enums import DepositForm, OperatingCostsTerms, RentChangeReason, RentModel, TenancyRole

# --- Tenant (personal data) --------------------------------------------------


class TenantCreate(Schema):
    # Validation errors never echo input: IBANs must not reach a log or an API error.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    full_name: str = Field(min_length=1)
    email: str | None = None
    phone: str | None = None
    iban: IbanInput | None = None  # write-only; stored as iban_masked + keyed HMAC


class Tenant(MutableRecord):
    """Read shape. After erasure every personal field is null and erased_at is set."""

    full_name: str | None
    email: str | None
    phone: str | None
    iban_masked: MaskedIban | None
    erased_at: datetime | None = None


# --- Tenancy -----------------------------------------------------------------

# Phase 3 D67: the day of the month the rent is due (§556b Abs. 1 BGB's default is the third
# working day; the lease may say otherwise). The ten-day rule of §11 EStG needs it (D54).
RentDueDay = Annotated[int, Field(ge=1, le=28)]


class TenancyParty(Schema):
    tenant_id: UUID
    role: TenancyRole


class TenancyCreate(Schema):
    # Validation errors never echo input: IBANs must not reach a log or an API error.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    unit_id: UUID
    parties: list[TenancyParty] = Field(min_length=1)  # exactly one primary (service rule)
    signed_on: date | None = None
    start_date: date  # move-in
    end_date: date | None = None  # move-out; null while running
    fixed_term_until: date | None = None  # Zeitmietvertrag; null = unbefristet
    rent_model: RentModel
    deposit_amount: NonNegativeMoney = Decimal("0")
    deposit_form: DepositForm = DepositForm.NONE
    deposit_bank: str | None = None
    deposit_iban: IbanInput | None = None
    rent_due_day: RentDueDay = 3  # phase 3 D67
    operating_costs_terms: OperatingCostsTerms = OperatingCostsTerms.PREPAYMENT  # phase 3 D72

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if self.signed_on is not None and self.signed_on > self.start_date:
            raise ValueError("signed_on must not be after start_date")
        if self.fixed_term_until is not None and self.fixed_term_until < self.start_date:
            raise ValueError("fixed_term_until must not be before start_date")
        if (self.deposit_form is DepositForm.NONE) != (self.deposit_amount == 0):
            raise ValueError("deposit_form 'none' exactly when deposit_amount is 0")
        return self


class Tenancy(MutableRecord):
    unit_id: UUID
    parties: list[TenancyParty]
    signed_on: date | None
    start_date: date
    end_date: date | None
    fixed_term_until: date | None
    rent_model: RentModel
    deposit_amount: NonNegativeMoney
    deposit_form: DepositForm
    deposit_bank: str | None
    deposit_iban_masked: MaskedIban | None
    notice_given_on: date | None = None
    rent_due_day: RentDueDay = 3  # phase 3 D67 (migration 0013)
    operating_costs_terms: OperatingCostsTerms = OperatingCostsTerms.PREPAYMENT  # D72


# --- Rent schedule (append-only) ---------------------------------------------


class RentScheduleCreate(Schema):
    """A dated rent row. Never edited: a wrong row is superseded by a correction row."""

    tenancy_id: UUID
    effective_from: date
    cold_rent: NonNegativeMoney  # Kaltmiete per month
    operating_costs_prepayment: NonNegativeMoney  # NK-Vorauszahlung per month
    reason: RentChangeReason
    supersedes_id: UUID | None = None
    document_id: UUID | None = None

    @model_validator(mode="after")
    def _correction(self) -> Self:
        if (self.reason is RentChangeReason.CORRECTION) != (self.supersedes_id is not None):
            raise ValueError("reason 'correction' exactly when supersedes_id is set")
        return self


class RentScheduleEntry(RentScheduleCreate):
    id: UUID
    workspace_id: UUID
    recorded_at: datetime


class InitialRent(Schema):
    """The first rent row, recorded with the tenancy (reason 'initial')."""

    cold_rent: NonNegativeMoney
    operating_costs_prepayment: NonNegativeMoney = Decimal("0")
    effective_from: date | None = None  # default: the tenancy's start_date
    document_id: UUID | None = None
