"""Document checklist per property and tax year (spec amendment 3, 6 Nov 2026).

ChecklistFacts is what rules.document_checklist reads: the property, its selected split, its
cost rows, loans, units and tenancies, and the documents that could satisfy a requirement. It
holds no tenant personal data (D5): a tenancy is its unit, dates and deposit form only.

Phase 3 (docs/phases/phase-3.md §8, D64) adds two data entries: `cost_receipt` per confirmed
allocation that needs a Beleg, and `market_rent` per Wohnung let in the year; their facts are
`allocations` and `market_rents` (empty and None for a phase 1 caller).

Phase 3 D142 (11 Nov 2026): a tenancy's `deposit_record` is also proven by the confirmed
`deposit_in` allocations linked to it (`allocations` carries them, with their tenancy and bank
line), and a tenancy that ended before the handover has no such item unless a deposit line is
linked to it (§566a BGB binds the buyer only for tenancies existing at the transfer). A missing
item offers the property's unlinked deposit lines, each with the tenancies it could be split to.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from .allocation import AllocationCategory
from .common import Money, PositiveMoney, Schema, TaxYear
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
    # Phase 3 (§8, D64)
    COST_RECEIPT = "cost_receipt"  # the Beleg of a repairs/renovation/fees allocation
    MARKET_RENT = "market_rent"  # the ortsübliche Miete for §21 Abs. 2 EStG (D61)


class ChecklistStatus(StrEnum):
    PRESENT = "present"  # a matching document is linked
    MISSING = "missing"  # none, and it is due
    EXPECTED = "expected"  # none, but not due yet (due_on > today)
    INCOMPLETE = "incomplete"  # the right document is linked but has no tax_year


class ChecklistScopeType(StrEnum):
    """What one item is about: property, cost row, loan, tenancy, allocation or unit."""

    PROPERTY = "property"
    ACQUISITION_COST = "acquisition_cost"
    LOAN = "loan"
    TENANCY = "tenancy"
    ALLOCATION = "allocation"
    UNIT = "unit"


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
    # D142: what a linked deposit line must reach. The column is never null (default 0); 0 or
    # None = any positive sum proves it.
    deposit_amount: Money | None = None


class ChecklistAllocation(Schema):
    """A confirmed allocation of the property (phase 3 §8). tax_year is the year §5.1 assigns
    it (the ten-day rule), resolved before the rule runs (by the checklist service, or by draft
    when it builds the checklist facts from DraftFacts); a 0.00 row is passed
    through and never creates an item (D70). D142: `deposit_in` rows come too, with their
    tenancy (null = not yet linked to one) and bank line; the receipt entry ignores them."""

    id: UUID
    category: AllocationCategory
    amount: Money
    booking_date: date
    tax_year: TaxYear
    document_id: UUID | None = None
    tenancy_id: UUID | None = None  # D142
    transaction_id: UUID | None = None  # D142: the bank line, for the offer and the link


class ChecklistMarketRent(Schema):
    """A `market_rent` tax input (D61), every row of its history: a row another row
    supersedes, and a withdrawn row, are not in force."""

    id: UUID
    unit_id: UUID | None = None  # null = the property's flat (ETW, EFH); set = an MFH's unit
    valid_from: date
    supersedes_id: UUID | None = None
    withdrawn: bool = False

    @model_validator(mode="after")
    def _withdrawn_supersedes(self) -> Self:
        if self.withdrawn and self.supersedes_id is None:
            raise ValueError("a withdrawn row supersedes the row it withdraws")
        return self


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
    allocations: list[ChecklistAllocation] = Field(default_factory=list)  # phase 3
    # Phase 3. None = a phase 1 caller: the market_rent entry does not apply (otherwise every
    # let unit would gain an item and phase 1's outcomes would change); [] = none recorded.
    market_rents: list[ChecklistMarketRent] | None = None

    @model_validator(mode="after")
    def _tenancies_on_units(self) -> Self:
        unit_ids = {u.id for u in self.units}
        if any(t.unit_id not in unit_ids for t in self.tenancies):
            raise ValueError("every tenancy must be on one of the units")
        if any(
            m.unit_id is not None and m.unit_id not in unit_ids for m in self.market_rents or []
        ):
            raise ValueError("a unit's market rent must be on one of the units")
        return self


# --- Items (rule output) ---------------------------------------------------------------


class ChecklistScope(Schema):
    """The item's subject and its non-personal context (no tenant names, D5)."""

    type: ChecklistScopeType
    id: UUID  # the property, cost row, loan, tenancy, allocation or unit
    lender: str | None = None  # loan
    unit_label: str | None = None  # tenancy, unit
    tenancy_start: date | None = None  # tenancy
    cost_kind: AcquisitionCostKind | None = None  # acquisition_cost
    cost_amount: PositiveMoney | None = None  # acquisition_cost
    category: AllocationCategory | None = None  # allocation (phase 3)
    booking_date: date | None = None  # allocation (phase 3)


class ChecklistBankLine(Schema):
    """A confirmed allocation as a checklist item cites it (D142): no counterparty, no purpose
    text (D5); the frontend labels it by date and amount and opens its bank line."""

    allocation_id: UUID
    transaction_id: UUID | None = None
    booking_date: date
    amount: Money


class ChecklistDepositCandidate(Schema):
    """A tenancy an unlinked deposit line could be split to (D142): it has a deposit (cash),
    its proof is missing, it did not end before the line's booking date and no deposit line is
    linked to it yet. unit_id: the allocation row's unit (it must be the tenancy's)."""

    tenancy_id: UUID
    unit_id: UUID
    deposit_amount: Money | None = None  # None: not recorded, so no split can be proposed


class ChecklistDepositOffer(ChecklistBankLine):
    """An unlinked `deposit_in` line of the property, offered on a missing deposit_record item
    whose tenancy is among `candidates` (D142). candidates: every such tenancy of the year's
    items, by tenancy start then id; the frontend proposes a split only from these."""

    candidates: list[ChecklistDepositCandidate] = Field(default_factory=list)


class _ChecklistItemBase(Schema):
    code: RequirementCode
    status: ChecklistStatus
    scope: ChecklistScope
    due_on: date | None = None  # null = due as soon as the item exists ("always")
    # D142, deposit_record only: the confirmed deposit lines linked to the tenancy (the proof
    # when no document is linked), and, while the item is missing, the unlinked deposit lines
    # of the property that could prove it. Both oldest first; empty for every other code.
    bank_lines: list[ChecklistBankLine] = Field(default_factory=list)
    deposit_offers: list[ChecklistDepositOffer] = Field(default_factory=list)


class ChecklistItem(_ChecklistItemBase):
    """build_checklist's output. documents: the satisfying documents (present) or the ones
    lacking a tax year (incomplete), oldest first; empty otherwise (also for a deposit proven
    by its bank lines only, D142, which lists them in bank_lines)."""

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
