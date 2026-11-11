"""The document checklist of one property (spec amendment 3, 6 Nov 2026).

Loads the facts rules.document_checklist reads, inside the caller's workspace transaction,
and calls the rule. Read only: no events, no writes. Tenancies are loaded without their
parties, so no tenant personal data reaches the facts or the response (D5).

Phase 3 (docs/phases/phase-3.md §8) adds the property's confirmed allocations of the
categories that need a Beleg, each with the tax year it counts in, and every `market_rent`
tax input row (history included: the rule decides which row is in force). Phase 3 D142 adds
the property's confirmed `deposit_in` allocations (with their tenancy and bank line) and each
tenancy's deposit amount, so a bank line can prove a deposit and a missing proof can offer one.
The Anlage V draft reuses these facts (services/tax.py), so Dokumente and Steuern agree.
"""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import property_price_split as split_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import document_checklist as rule
from hausbuch.schemas.allocation import AllocationCategory, AllocationStatus
from hausbuch.schemas.checklist import (
    ChecklistAllocation,
    ChecklistCost,
    ChecklistDocument,
    ChecklistDocumentRef,
    ChecklistFacts,
    ChecklistLink,
    ChecklistLoan,
    ChecklistMarketRent,
    ChecklistPriceSplit,
    ChecklistTenancy,
    ChecklistUnit,
    DocumentChecklist,
    DocumentChecklistItem,
)
from hausbuch.schemas.document import Document
from hausbuch.schemas.enums import EntityType, LinkTarget
from hausbuch.schemas.tax import MarketRent, TaxInputKind

from .documents import get_document, list_related_documents
from .errors import NotFoundError
from .loans import list_loans
from .properties import get_property, list_acquisition_costs
from .tax_inputs import list_tax_inputs
from .units import list_units

# The tax year a confirmed allocation counts in (§5.1), from its row joined with its
# transaction's booking_date. The receipt categories are not recurring payments, for which
# §5.1 gives the booking year; a caller holding the full §5.1 rule passes it instead.
AllocationYear = Callable[[dict], int]


def booking_year(row: dict) -> int:
    return row["booking_date"].year


def document_checklist(
    conn: Connection, property_id: UUID, *, tax_year: int | None, today: date
) -> DocumentChecklist:
    """The property's checklist for `tax_year` (default: today's year, clamped into the
    available years). NotFoundError for an unknown property; ValueError (422) for a year
    outside available_years."""
    facts, documents = checklist_facts(conn, property_id)
    years = rule.available_years(facts, today)
    if tax_year is None:
        tax_year = min(max(today.year, years[0]), years[-1])
    elif tax_year not in years:
        raise ValueError(
            f"tax_year {tax_year} is outside this property's years {years[0]}-{years[-1]}"
        )
    items = rule.build_checklist(facts, tax_year, today)
    return DocumentChecklist(
        property_id=property_id,
        tax_year=tax_year,
        available_years=years,
        items=[
            DocumentChecklistItem(
                **item.model_dump(exclude={"documents"}),
                documents=[_ref(documents[d]) for d in item.documents],
            )
            for item in items
        ],
    )


def checklist_facts(
    conn: Connection, property_id: UUID, *, allocation_year: AllocationYear = booking_year
) -> tuple[ChecklistFacts, dict[UUID, Document]]:
    """The facts of one property, and every document they mention by id. allocation_year
    resolves each allocation's §5.1 tax year (default: the booking year)."""
    prop = get_property(conn, property_id)
    if prop is None:
        raise NotFoundError(EntityType.PROPERTY, property_id)

    split = None
    if prop.selected_price_split_id is not None:
        row = conn.execute(
            select(
                split_table.c.id, split_table.c.source, split_table.c.evidence_document_id
            ).where(split_table.c.id == prop.selected_price_split_id)
        ).one()
        split = ChecklistPriceSplit.model_validate(dict(row._mapping))

    costs = list_acquisition_costs(conn, property_id)
    units = list_units(conn, property_id)
    tenancies = conn.execute(
        select(
            tenancy_table.c.id,
            tenancy_table.c.unit_id,
            tenancy_table.c.start_date,
            tenancy_table.c.end_date,
            tenancy_table.c.deposit_form,
            tenancy_table.c.deposit_amount,
        ).where(tenancy_table.c.unit_id.in_([u.id for u in units]))
    )

    # Documents linked to P, its units, their tenancies or its loans, with those links; then
    # the ones a cost row or the split references, which need not be linked at all.
    documents: dict[UUID, Document] = {}
    links: dict[UUID, list[ChecklistLink]] = {}
    for related in list_related_documents(
        conn, target_type=LinkTarget.PROPERTY, target_id=property_id
    ):
        documents[related.document.id] = related.document
        links[related.document.id] = [
            ChecklistLink(target_type=v.target_type, target_id=v.target_id) for v in related.via
        ]
    allocations = [
        ChecklistAllocation(
            id=row["id"],
            category=row["category"],
            amount=row["amount"],
            booking_date=row["booking_date"],
            tax_year=allocation_year(row),
            document_id=row["document_id"],
            tenancy_id=row["tenancy_id"],
            transaction_id=row["transaction_id"],
        )
        for row in _checklist_allocations(conn, property_id)
    ]
    referenced = [c.document_id for c in costs] + [a.document_id for a in allocations]
    if split is not None:
        referenced.append(split.evidence_document_id)
    for document_id in referenced:
        if document_id is not None and document_id not in documents:
            document = get_document(conn, document_id)
            if document is None:  # the composite FK makes this impossible
                raise NotFoundError(EntityType.DOCUMENT, document_id)
            documents[document_id] = document
            links[document_id] = []

    facts = ChecklistFacts(
        property_id=prop.id,
        kind=prop.kind,
        purchase_date=prop.purchase_date,
        ownership_ended_on=prop.ownership_ended_on,
        selected_price_split=split,
        acquisition_costs=[
            ChecklistCost(id=c.id, kind=c.kind, amount=c.amount, document_id=c.document_id)
            for c in costs
        ],
        loans=[
            ChecklistLoan(id=lo.id, lender=lo.lender, disbursed_on=lo.terms.disbursed_on)
            for lo in list_loans(conn, property_id)
        ],
        units=[ChecklistUnit(id=u.id, label=u.label) for u in units],
        tenancies=[ChecklistTenancy.model_validate(dict(r._mapping)) for r in tenancies],
        documents=[_checklist_document(d, links[d.id]) for d in documents.values()],
        allocations=allocations,
        market_rents=[
            ChecklistMarketRent(
                id=row.id,
                unit_id=row.unit_id,
                valid_from=row.value.valid_from,
                supersedes_id=row.supersedes_id,
                withdrawn=row.withdrawn,
            )
            for row in list_tax_inputs(
                conn, property_id, kind=TaxInputKind.MARKET_RENT, include_history=True
            )
            if isinstance(row.value, MarketRent)
        ],
    )
    return facts, documents


# What the checklist reads of the ledger: the Beleg categories (§8) and the deposits (D142).
_CHECKLIST_CATEGORIES = rule.RECEIPT_CATEGORIES | {AllocationCategory.DEPOSIT_IN}


def _checklist_allocations(conn: Connection, property_id: UUID) -> list[dict]:
    """The property's confirmed allocations of the categories that need a Beleg, and its
    confirmed deposit_in allocations (D142), with their transaction's booking date. 0.00 rows
    are left to the rule, which skips them (D70); so is telling the two kinds apart."""
    rows = conn.execute(
        select(
            allocation_table.c.id,
            allocation_table.c.transaction_id,
            allocation_table.c.tenancy_id,
            allocation_table.c.category,
            allocation_table.c.amount,
            allocation_table.c.recurring,
            allocation_table.c.service_period_start,
            allocation_table.c.service_period_end,
            allocation_table.c.document_id,
            transaction_table.c.booking_date,
            transaction_table.c.value_date,
        )
        .join(transaction_table, transaction_table.c.id == allocation_table.c.transaction_id)
        .where(
            allocation_table.c.property_id == property_id,
            allocation_table.c.status == AllocationStatus.CONFIRMED.value,
            allocation_table.c.category.in_(sorted(c.value for c in _CHECKLIST_CATEGORIES)),
        )
        .order_by(transaction_table.c.booking_date, allocation_table.c.id)
    )
    return [dict(r._mapping) for r in rows]


def _checklist_document(document: Document, links: list[ChecklistLink]) -> ChecklistDocument:
    typing = document.typing
    return ChecklistDocument(
        id=document.id,
        type=None if typing is None else typing.type,
        subtype=None if typing is None else typing.subtype,
        tax_year=None if typing is None else typing.tax_year,
        received_at=document.received_at,
        links=links,
    )


def _ref(document: Document) -> ChecklistDocumentRef:
    typing = document.typing
    return ChecklistDocumentRef(
        id=document.id,
        type=None if typing is None else typing.type,
        subtype=None if typing is None else typing.subtype,
        tax_year=None if typing is None else typing.tax_year,
        document_date=None if typing is None else typing.document_date,
        received_at=document.received_at,
    )
