"""Bookings that propose a tax input (phase 10 §7.8 (a), D293): the shapes of
rules/tax/booked_inputs.py.

A confirmed private line whose household code proposes a return input (the taxonomy's
`proposes`: Lohnersatzleistung, donation, childcare, §35a) is evidence; the rule compares the
year's lines with the current rows of the proposed kind and proposes the difference. A proposal
is never stored: the person confirms it through `POST /return-inputs` (principle 3), and a row
typed by hand covers the bookings the same way.
"""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .allocation import AllocationCategory
from .common import Money, NonNegativeMoney, Schema, TaxYear
from .employment import BookedBenefit
from .enums import ReturnInputKind

# What a proposal stands for; the guide point's code is BOOKED_POINT_CODES[group].
BookedGroup = Literal["benefit", "donation", "childcare", "household_services"]

# A field the card asks for before „Übernehmen“ can write the row: the bank line does not say it.
# owner: a joint account's benefit; benefit: the lines name no one kind; child: not exactly one
# child under the age limit; labour_cost and service_class: a §35a invoice's labour share and
# its kind, never read from the bank line; recipient: a donation line without a payee name.
BookedNeed = Literal["owner", "benefit", "child", "labour_cost", "service_class", "recipient"]


class BookedLine(BookedBenefit):
    """One confirmed allocation on a private account in a code that proposes a return input.
    `amount` is signed as on the account (a benefit is a credit, a donation a debit; a payment
    back has the other sign). `payee` is the line's counterparty name and `purpose` its purpose
    text: a proposal shows the payee only, never the purpose. Internal to the rule."""

    category: AllocationCategory
    payee: str | None = None
    purpose: str | None = None


class ChildChoice(Schema):
    """A child the childcare card may name: its return_input row and birth date (a child is
    stored as a birth date only, phase 4b D103)."""

    id: UUID
    born_on: date


class BookedPayment(Schema):
    """One payment a proposal sums, for „Zahlungen ansehen“: its booking date, the amount as on
    the account (signed) and the payee's name, never the purpose text. Filled by the service
    (services/return_inputs.booked_input_proposals); the rule leaves it empty."""

    allocation_id: UUID
    booking_date: date
    amount: Money
    payee: str | None = None


class BookedProposal(Schema):
    """One proposed return input from the year's bookings.

    `booked` is the group's year sum as a positive amount (clamped at zero), `typed` what the
    current rows cover of it, `difference` = booked − typed, the amount the row would carry
    (for §35a the bill's sum, shown only: the row carries the labour share the person types).
    `payload` is the `POST /return-inputs` body with every field the bookings say; the card
    adds the fields in `needs`. `blocking`: whether the guide counts it as open (a benefit
    does, as its to-do did; the other groups are proposals the amounts alone cannot close, see
    rules/tax/booked_inputs.py)."""

    group: BookedGroup
    kind: ReturnInputKind
    tax_year: TaxYear
    owner_id: UUID | None = None
    payee: str | None = None
    booked: NonNegativeMoney
    typed: Money
    difference: NonNegativeMoney
    payments: int = Field(ge=1)
    allocation_ids: list[UUID]
    # The payments behind allocation_ids, for display only (filled by the service).
    lines: list[BookedPayment] = Field(default_factory=list)
    payload: dict[str, Any]
    needs: list[BookedNeed] = Field(default_factory=list)
    benefit: str | None = None  # the benefit kind the lines name (benefit group)
    child_choices: list[ChildChoice] = Field(default_factory=list)
    receipt_needed: bool = False  # a donation above 300 € in one payment (§50 Abs. 4 EStDV)
    blocking: bool = False
