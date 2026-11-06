"""Document checklist per property and tax year (spec amendment 3, 6 Nov 2026).

ChecklistFacts is what rules.document_checklist reads: the property, its selected split, its
cost rows, loans, units and tenancies, and the documents that could satisfy a requirement. It
holds no tenant personal data (D5): a tenancy is its unit, dates and deposit form only.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import PositiveMoney, Schema, TaxYear
from .enums import (
    AcquisitionCostKind,
    DepositForm,
    DocumentSubtype,
    DocumentType,
    LinkTarget,
    PriceSplitSource,
    PropertyKind,
)

# Not stored in any column, so these are not in enums.py (whose sets mirror CHECKs).


class RequirementCode(StrEnum):
    """One kind of paperwork the return needs. German labels and hints live in the frontend."""

    PURCHASE_CONTRACT = "purchase_contract"
    PRICE_SPLIT = "price_split"
    ACQUISITION_COST_EVIDENCE = "acquisition_cost_evidence"
    GRUNDSTEUERBESCHEID = "grundsteuerbescheid"
    LOAN_CONTRACT = "loan_contract"
    ZINSBESCHEINIGUNG = "zinsbescheinigung"
    LEASE = "lease"
    HANDOVER_PROTOCOL = "handover_protocol"
    DEPOSIT_RECORD = "deposit_record"
    WIRTSCHAFTSPLAN = "wirtschaftsplan"
    HAUSGELDABRECHNUNG = "hausgeldabrechnung"


class ChecklistStatus(StrEnum):
    PRESENT = "present"  # a matching document is linked
    MISSING = "missing"  # none, and it is due
    EXPECTED = "expected"  # none, but not due yet (due_on > today)
    INCOMPLETE = "incomplete"  # the right document is linked but has no tax_year


class ChecklistScopeType(StrEnum):
    """What one item is about: the property, one cost row, one loan or one tenancy."""

    PROPERTY = "property"
    ACQUISITION_COST = "acquisition_cost"
    LOAN = "loan"
    TENANCY = "tenancy"


# --- Facts (rule input) ---------------------------------------------------------------


class ChecklistLink(Schema):
    target_type: LinkTarget
    target_id: UUID


class ChecklistDocument(Schema):
    """A document as the checklist sees it: its typing and every target it is linked to."""

    id: UUID
    type: DocumentType | None = None  # null = untyped; matches nothing
    subtype: DocumentSubtype | None = None
    tax_year: TaxYear | None = None
    received_at: datetime  # "oldest first" among several matches
    links: list[ChecklistLink] = Field(default_factory=list)


class ChecklistPriceSplit(Schema):
    """The property's selected building/land split."""

    id: UUID
    source: PriceSplitSource
    evidence_document_id: UUID | None = None

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        if self.source is PriceSplitSource.APPRAISAL and self.evidence_document_id is None:
            raise ValueError("an appraisal split needs evidence_document_id")
        return self


class ChecklistCost(Schema):
    id: UUID
    kind: AcquisitionCostKind
    amount: PositiveMoney
    document_id: UUID | None = None


class ChecklistLoan(Schema):
    id: UUID
    lender: str
    disbursed_on: date


class ChecklistUnit(Schema):
    id: UUID
    label: str


class ChecklistTenancy(Schema):
    id: UUID
    unit_id: UUID
    start_date: date
    end_date: date | None = None
    deposit_form: DepositForm = DepositForm.NONE


class ChecklistFacts(Schema):
    """Everything build_checklist reads for one property."""

    property_id: UUID
    kind: PropertyKind
    purchase_date: date  # Übergang (D7); the property counts from this day
    ownership_ended_on: date | None = None
    selected_price_split: ChecklistPriceSplit | None = None
    acquisition_costs: list[ChecklistCost] = Field(default_factory=list)
    loans: list[ChecklistLoan] = Field(default_factory=list)
    units: list[ChecklistUnit] = Field(default_factory=list)
    tenancies: list[ChecklistTenancy] = Field(default_factory=list)  # of these units
    documents: list[ChecklistDocument] = Field(default_factory=list)

    @model_validator(mode="after")
    def _tenancies_on_units(self) -> Self:
        unit_ids = {u.id for u in self.units}
        if any(t.unit_id not in unit_ids for t in self.tenancies):
            raise ValueError("every tenancy must be on one of the units")
        return self


# --- Items (rule output) ---------------------------------------------------------------


class ChecklistScope(Schema):
    """The item's subject and its non-personal context (no tenant names, D5)."""

    type: ChecklistScopeType
    id: UUID  # the property, cost row, loan or tenancy
    lender: str | None = None  # loan
    unit_label: str | None = None  # tenancy
    tenancy_start: date | None = None  # tenancy
    cost_kind: AcquisitionCostKind | None = None  # acquisition_cost
    cost_amount: PositiveMoney | None = None  # acquisition_cost


class _ChecklistItemBase(Schema):
    code: RequirementCode
    status: ChecklistStatus
    scope: ChecklistScope
    due_on: date | None = None  # null = due as soon as the item exists ("always")


class ChecklistItem(_ChecklistItemBase):
    """build_checklist's output. documents: the satisfying documents (present) or the ones
    lacking a tax year (incomplete), oldest first; empty otherwise."""

    documents: list[UUID] = Field(default_factory=list)


# --- API -------------------------------------------------------------------------------


class ChecklistDocumentRef(Schema):
    """A satisfying document, without its title or filename (either can name a tenant, D5):
    the frontend labels it by type and date and links to the document."""

    id: UUID
    type: DocumentType | None
    subtype: DocumentSubtype | None
    tax_year: TaxYear | None
    document_date: date | None
    received_at: datetime


class DocumentChecklistItem(_ChecklistItemBase):
    documents: list[ChecklistDocumentRef]


class DocumentChecklist(Schema):
    """GET /properties/{id}/checklist?tax_year=Y."""

    property_id: UUID
    tax_year: TaxYear
    # Purchase year (2000 at the earliest) through the current year, ending with the year
    # ownership ended.
    available_years: list[TaxYear]
    items: list[DocumentChecklistItem]
