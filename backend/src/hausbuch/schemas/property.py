"""Owner, Property and Unit, plus the facts the AfA basis is built from."""

import re
from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from .common import (
    Area,
    BuildingShare,
    Fraction,
    LivingArea,
    MutableRecord,
    PositiveMoney,
    Rooms,
    Schema,
    Year,
)
from .enums import AcquisitionCostKind, PriceSplitSource, PropertyKind, StateCode

# --- Owner -------------------------------------------------------------------


def reject_raw_tax_number(value: str | None) -> str | None:
    """Reject any value containing what looks like a Steuer-ID (11 digits) or Steuernummer
    (10-13 digits), labelled or not ("IdNr. 12345678901"): it would otherwise land
    permanently in the append-only event log. Separators inside the number are ignored.
    Shared by OwnerCreate and the API's OwnerPatch."""
    if value is not None and re.search(r"(?<!\d)\d{10,13}(?!\d)", re.sub(r"[\s/.-]", "", value)):
        raise ValueError("tax_id_ref must be a secrets-manager reference, not a tax number")
    return value


class OwnerCreate(Schema):
    # Validation errors never echo input: a mistyped Steuer-ID must not reach a log.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    full_name: str = Field(min_length=1)
    # Reference into the secrets manager. The raw Steuer-ID is never stored in Postgres.
    tax_id_ref: str | None = None
    # Phase 4 D90: church tax and its Bundesland replace the typed marginal_tax_rate (dropped
    # by migration 0016; a typed rate is now a per-year income_input row).
    church_tax: bool = False
    tax_state: StateCode | None = None

    @field_validator("tax_id_ref")
    @classmethod
    def _not_a_raw_tax_number(cls, value: str | None) -> str | None:
        return reject_raw_tax_number(value)

    @model_validator(mode="after")
    def _state_with_church(self) -> Self:
        if self.church_tax and self.tax_state is None:
            raise ValueError("church tax needs the Bundesland (tax_state)")
        return self


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
    # Phase 3 D71: the flat's Wohnfläche (purchase contract or Wohnflächenberechnung) for the
    # Anlage V's Z11 and its one Z13 row per Wohnung. Null: the draft sums the units' m² and
    # warns, since rooms let separately leave out kitchen, bath and hall.
    living_area_m2: LivingArea | None = None

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
