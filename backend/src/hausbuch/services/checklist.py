"""The document checklist of one property (spec amendment 3, 6 Nov 2026).

Loads the facts rules.document_checklist reads, inside the caller's workspace transaction,
and calls the rule. Read only: no events, no writes. Tenancies are loaded without their
parties, so no tenant personal data reaches the facts or the response (D5).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import property_price_split as split_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.rules import document_checklist as rule
from hausbuch.schemas.checklist import (
    ChecklistCost,
    ChecklistDocument,
    ChecklistDocumentRef,
    ChecklistFacts,
    ChecklistLink,
    ChecklistLoan,
    ChecklistPriceSplit,
    ChecklistTenancy,
    ChecklistUnit,
    DocumentChecklist,
    DocumentChecklistItem,
)
from hausbuch.schemas.document import Document
from hausbuch.schemas.enums import EntityType, LinkTarget

from .documents import get_document, list_related_documents
from .errors import NotFoundError
from .loans import list_loans
from .properties import get_property, list_acquisition_costs
from .units import list_units


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
    conn: Connection, property_id: UUID
) -> tuple[ChecklistFacts, dict[UUID, Document]]:
    """The facts of one property, and every document they mention by id."""
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
    referenced = [c.document_id for c in costs]
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
    )
    return facts, documents


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
