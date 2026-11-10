"""Shared value types. Money is Decimal (EUR, 2 places), never float.

Spec amendment 2 (docs/phases/phase-1.md): the decimal types below accept a decimal string or
a Decimal and nothing else. A float (a JSON number such as 1400.0) is refused, because it may
already have been rounded before it arrived, and so is an int, so that a client sending JSON
numbers fails on every value rather than only on the ones with cents. Their JSON schema is
`type: string`, which is what the generated TypeScript types follow.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema, model_validator


class Schema(BaseModel):
    """Base for every schema: unknown fields are rejected, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _decimal_string_only(value: Any) -> Any:
    """Refuse JSON numbers before Pydantic's lax Decimal coercion sees them. The message is a
    fixed string: it never echoes the value."""
    if isinstance(value, float | int):  # bool is an int too
        raise ValueError("must be a decimal string, not a number")
    return value


_STRING_ONLY = BeforeValidator(_decimal_string_only)


def _string_schema(decimal_places: int, *, signed: bool = False) -> WithJsonSchema:
    """JSON schema `string` with the canonical decimal form. The pattern is for clients; the
    server validates with Pydantic's Decimal rules (digits, places, bounds)."""
    sign = "-?" if signed else ""
    return WithJsonSchema(
        {"type": "string", "pattern": rf"^{sign}[0-9]+(\.[0-9]{{1,{decimal_places}}})?$"}
    )


# numeric(14,2) in Postgres. v1 is EUR only.
Money = Annotated[
    Decimal, Field(max_digits=14, decimal_places=2), _STRING_ONLY, _string_schema(2, signed=True)
]
NonNegativeMoney = Annotated[
    Decimal, Field(ge=0, max_digits=14, decimal_places=2), _STRING_ONLY, _string_schema(2)
]
PositiveMoney = Annotated[
    Decimal, Field(gt=0, max_digits=14, decimal_places=2), _STRING_ONLY, _string_schema(2)
]

# numeric(7,6): a rate as a fraction, 0.036 = 3.6 % p.a.
Rate = Annotated[
    Decimal, Field(ge=0, lt=1, max_digits=7, decimal_places=6), _STRING_ONLY, _string_schema(6)
]
# numeric(5,4): marginal income tax rate incl. Soli/church tax, 0.4431 = 44.31 %.
TaxRate = Annotated[
    Decimal, Field(ge=0, lt=1, max_digits=5, decimal_places=4), _STRING_ONLY, _string_schema(4)
]
# A yearly return as a signed fraction, 6 places, above −100 %: a CAGR or an IRR (phase 4
# D89). Not stored; a projection may lose money, so unlike Rate it may be negative.
ReturnRate = Annotated[
    Decimal,
    Field(gt=-1, max_digits=9, decimal_places=6),
    _STRING_ONLY,
    _string_schema(6, signed=True),
]
# Vacancy months per year in a projection's levers: 0–12, one decimal (phase 4 D87).
VacancyMonths = Annotated[
    Decimal,
    Field(ge=0, le=12, max_digits=3, decimal_places=1),
    _STRING_ONLY,
    _string_schema(1),
]
# A coefficient of the §32a EStG formula (rules/tax/tariffs/<year>.yaml, phase 4 D81), e.g.
# 914.51 or 11135.63: two places, as the law prints them. Not stored.
TariffCoefficient = Annotated[
    Decimal,
    Field(max_digits=9, decimal_places=2),
    _STRING_ONLY,
    _string_schema(2, signed=True),
]
# numeric(7,6), open interval: building share of the purchase price.
BuildingShare = Annotated[
    Decimal, Field(gt=0, lt=1, max_digits=7, decimal_places=6), _STRING_ONLY, _string_schema(6)
]

# numeric(4,3), closed interval: a match confidence (allocation.confidence, phase 2). Always a
# number code computed (deterministic 1.0, document match 0.95, the model's high/medium/low
# mapped to 0.9/0.6/0.3), never a number a model reported (D15).
Confidence = Annotated[
    Decimal, Field(ge=0, le=1, max_digits=4, decimal_places=3), _STRING_ONLY, _string_schema(3)
]

# numeric(8,2) and numeric(4,1)
Area = Annotated[
    Decimal, Field(gt=0, max_digits=8, decimal_places=2), _STRING_ONLY, _string_schema(2)
]
# numeric(7,2): a flat's Wohnfläche (property.living_area_m2, migration 0013, phase 3 D71).
LivingArea = Annotated[
    Decimal, Field(gt=0, max_digits=7, decimal_places=2), _STRING_ONLY, _string_schema(2)
]
# A share shown to one decimal, e.g. the 15 % tracker's 14.2 (phase 3 §5.3); not stored.
Percent = Annotated[
    Decimal, Field(ge=0, max_digits=7, decimal_places=1), _STRING_ONLY, _string_schema(1)
]
Rooms = Annotated[
    Decimal, Field(gt=0, max_digits=4, decimal_places=1), _STRING_ONLY, _string_schema(1)
]
# The Lohnsteuerbescheinigung's "Zahl der Kinderfreibeträge" (phase 4b §5.1): half steps,
# informational; not a column (it lives in return_input.value).
ChildAllowanceCount = Annotated[
    Decimal,
    Field(ge=0, le=20, max_digits=3, decimal_places=1, multiple_of=Decimal("0.5")),
    _STRING_ONLY,
    _string_schema(1),
]

TAX_YEAR_MIN = 2000  # also the floor of document.tax_year's CHECK
TaxYear = Annotated[int, Field(ge=TAX_YEAR_MIN, le=2100)]
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
