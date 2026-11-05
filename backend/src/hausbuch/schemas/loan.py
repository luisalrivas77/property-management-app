"""Loan terms and the deterministic repayment schedule derived from them."""

from datetime import date
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from .common import IbanInput, MaskedIban, Money, MutableRecord, PositiveMoney, Rate, Schema


class LoanTerms(Schema):
    """Everything the schedule is computed from. Input to rules.loan_schedule."""

    principal: PositiveMoney
    nominal_rate: Rate  # p.a., fraction
    monthly_payment: PositiveMoney  # annuity (Zins + Tilgung)
    disbursed_on: date
    first_payment_on: date
    fixed_until: date | None = None  # end of Zinsbindung
    # Rate after fixed_until. Null = keep nominal_rate (the "today's rate" default).
    follow_up_rate: Rate | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.first_payment_on <= self.disbursed_on:
            raise ValueError("first_payment_on must be after disbursed_on")
        if self.fixed_until is not None and self.fixed_until < self.first_payment_on:
            raise ValueError("fixed_until must not be before first_payment_on")
        if self.follow_up_rate is not None and self.fixed_until is None:
            raise ValueError("follow_up_rate needs fixed_until")
        return self


class LoanCreate(LoanTerms):
    # Validation errors never echo input: IBANs must not reach a log or an API error.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    property_id: UUID
    lender: str = Field(min_length=1)
    contract_number: str | None = None
    account_iban: IbanInput | None = None


class Loan(MutableRecord):
    property_id: UUID
    lender: str
    contract_number: str | None
    account_iban_masked: MaskedIban | None
    terms: LoanTerms


class ScheduleEntry(Schema):
    """interest = opening_balance * (nominal * (interest_days - follow_up_days)
    + follow_up * follow_up_days) / 360, rounded half-up (principle 2: traceable)."""

    number: int = Field(ge=1)
    due_on: date
    rate: Rate  # the rate in force on the period's last day
    interest_days: int = Field(ge=0)  # 30E/360 days the period accrues
    # Of those, days after fixed_until, charged at the follow-up rate (which is the nominal
    # rate when no follow_up_rate is set). 0 before the switch, interest_days after it.
    follow_up_days: int = Field(ge=0)
    opening_balance: Money
    interest: Money  # Schuldzinsen: deductible
    principal: Money  # Tilgung: not deductible
    payment: Money
    closing_balance: Money

    @model_validator(mode="after")
    def _days(self) -> Self:
        if self.follow_up_days > self.interest_days:
            raise ValueError("follow_up_days cannot exceed interest_days")
        return self


class YearTotals(Schema):
    """Per calendar year of due_on. Interest here is what Anlage V will reconcile against."""

    year: int
    interest: Money
    principal: Money
    payment: Money
    closing_balance: Money


class LoanSchedule(Schema):
    entries: list[ScheduleEntry]
    years: list[YearTotals]
