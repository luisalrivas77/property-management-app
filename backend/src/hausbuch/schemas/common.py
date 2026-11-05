"""Shared value types. Money is Decimal (EUR, 2 places), never float."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    """Base for every schema: unknown fields are rejected, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# numeric(14,2) in Postgres. v1 is EUR only.
Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]

# numeric(7,6): a rate as a fraction, 0.036 = 3.6 % p.a.
Rate = Annotated[Decimal, Field(ge=0, lt=1, max_digits=7, decimal_places=6)]
# numeric(5,4): marginal income tax rate incl. Soli/church tax, 0.4431 = 44.31 %.
TaxRate = Annotated[Decimal, Field(ge=0, lt=1, max_digits=5, decimal_places=4)]
# numeric(7,6), open interval: building share of the purchase price.
BuildingShare = Annotated[Decimal, Field(gt=0, lt=1, max_digits=7, decimal_places=6)]

# numeric(8,2) and numeric(4,1)
Area = Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=2)]
Rooms = Annotated[Decimal, Field(gt=0, max_digits=4, decimal_places=1)]

TaxYear = Annotated[int, Field(ge=2000, le=2100)]
Year = Annotated[int, Field(ge=1800, le=2100)]

# Display form only: country + check digits + last four, e.g. "DE89 **** 3000".
MaskedIban = Annotated[str, Field(pattern=r"^[A-Z]{2}[0-9]{2} \*{4} [A-Z0-9]{4}$")]
# Plaintext IBAN accepted on input only; stored as MaskedIban + keyed HMAC, never returned.
IbanInput = Annotated[str, Field(min_length=15, max_length=42)]

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Fraction(Schema):
    """An exact share: ownership (1/2) or Miteigentumsanteil (85/1000)."""

    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)

    @model_validator(mode="after")
    def _at_most_whole(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("a share cannot exceed 1")
        return self


class Record(Schema):
    """Columns every stored row has."""

    id: UUID
    workspace_id: UUID
    created_at: datetime


class MutableRecord(Record):
    updated_at: datetime
