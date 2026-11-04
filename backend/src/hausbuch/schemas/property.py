"""Owner, Property and Unit, plus the facts the AfA basis is built from."""

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .common import (
    Area,
    BuildingShare,
    Fraction,
    MutableRecord,
    PositiveMoney,
    Rooms,
    Schema,
    TaxRate,
    Year,
)
from .enums import AcquisitionCostKind, PriceSplitSource, PropertyKind

# --- Owner -------------------------------------------------------------------


class OwnerCreate(Schema):
    full_name: str = Field(min_length=1)
    # Reference into the secrets manager. The raw Steuer-ID is never stored in Postgres.
    tax_id_ref: str | None = None
    marginal_tax_rate: TaxRate | None = None  # user-set


class Owner(OwnerCreate, MutableRecord):
    pass


# --- Property ----------------------------------------------------------------


class Address(Schema):
    street: str = Field(min_length=1)
    house_number: str = Field(min_length=1)
    postal_code: str = Field(pattern=r"^[0-9]{5}$")
    city: str = Field(min_length=1)
    country_code: str = Field(default="DE", pattern=r"^DE$")  # v1 is German tax only


class PropertyCreate(Schema):
    label: str = Field(min_length=1, max_length=100)  # e.g. "Wohnung Lindenstraße"
    kind: PropertyKind
    address: Address
    build_year: Year  # Fertigstellungsjahr; drives the AfA rate
    # Date the Kaufvertrag was notarized. Starts the §23 EStG Spekulationsfrist.
    notarized_on: date
    # Übergang von Besitz, Nutzen und Lasten. AfA starts in this month.
    purchase_date: date
    purchase_price: PositiveMoney

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        if self.purchase_date < self.notarized_on:
            raise ValueError("purchase_date must not be before notarized_on")
        return self


class Property(PropertyCreate, MutableRecord):
    selected_price_split_id: UUID | None = None
    ownership_ended_on: date | None = None  # set on sale; starts ownership-record retention


class OwnerShare(Schema):
    owner_id: UUID
    share: Fraction


class PropertyOwnership(Schema):
    """Shares per property must sum to exactly 1 (service-layer rule, see fixtures)."""

    property_id: UUID
    shares: list[OwnerShare] = Field(min_length=1)


class AcquisitionCostCreate(Schema):
    property_id: UUID
    kind: AcquisitionCostKind
    amount: PositiveMoney
    paid_on: date | None = None
    document_id: UUID | None = None
    note: str | None = None


class AcquisitionCost(AcquisitionCostCreate, MutableRecord):
    pass


class PriceSplitCreate(Schema):
    """One candidate building/land split. Rows are append-only; the property selects one."""

    property_id: UUID
    source: PriceSplitSource
    building_share: BuildingShare
    evidence_document_id: UUID | None = None
    # BMF Arbeitshilfe inputs exactly as entered. Typed properly in the phase 3 spec PR.
    inputs: dict[str, JsonValue] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        if self.source is PriceSplitSource.APPRAISAL and self.evidence_document_id is None:
            raise ValueError("an appraisal split needs the Gutachten as evidence_document_id")
        if (self.source is PriceSplitSource.BMF_TOOL) != (self.inputs is not None):
            raise ValueError("inputs are required for, and only allowed on, a bmf_tool split")
        return self


class PriceSplit(PriceSplitCreate):
    id: UUID
    workspace_id: UUID
    recorded_at: datetime


# --- Unit --------------------------------------------------------------------


class UnitCreate(Schema):
    property_id: UUID
    label: str = Field(min_length=1, max_length=50)  # e.g. "WE 3"
    floor_area_m2: Area
    rooms: Rooms | None = None
    mea: Fraction | None = None  # Miteigentumsanteil in the WEG


class Unit(UnitCreate, MutableRecord):
    pass
