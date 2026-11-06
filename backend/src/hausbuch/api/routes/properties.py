"""Properties: ownership, Nebenkosten, price splits, purchase-date corrections.

Endpoints: docs/phases/phase-1-step-3.md §4.2 (History and the audit trail of a property are
in history.py, §4.8). Writes go to flat collections (POST /acquisition-costs, the Create
schema names the property); reads of a property's sub-collections come from the property
and answer 404 when it does not exist in the session's workspace.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query, Response
from sqlalchemy import Connection

from hausbuch.schemas.api import (
    AcquisitionCostPatch,
    DateCorrection,
    NotarizedOnCorrection,
    OwnershipBody,
    PropertyPatch,
    RelatedDocument,
)
from hausbuch.schemas.checklist import DocumentChecklist
from hausbuch.schemas.enums import EntityType, LinkTarget
from hausbuch.schemas.loan import Loan
from hausbuch.schemas.property import (
    AcquisitionCost,
    AcquisitionCostCreate,
    PriceSplit,
    PriceSplitCreate,
    Property,
    PropertyCreate,
    PropertyOwnership,
    Unit,
)
from hausbuch.services.checklist import document_checklist
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.loans import list_loans
from hausbuch.services.properties import (
    add_acquisition_cost,
    correct_notarized_on,
    correct_purchase_date,
    create_property,
    get_ownership,
    get_property,
    list_acquisition_costs,
    list_price_splits,
    list_properties,
    record_price_split,
    remove_acquisition_cost,
    select_price_split,
    set_ownership,
    update_acquisition_cost,
    update_property,
)
from hausbuch.services.units import list_units

from ..deps import Cmd, domain_router, found, require
from .documents import related_documents

router = domain_router()
OWNERSHIP = "property_ownership"


@router.get("/properties")
def properties_list(cmd: Cmd) -> list[Property]:
    return cmd.read(list_properties)


@router.post("/properties", status_code=201)
def property_create(body: PropertyCreate, cmd: Cmd) -> Property:
    return cmd.run(create_property, body)


@router.get("/properties/{property_id}")
def property_get(property_id: UUID, cmd: Cmd) -> Property:
    return found(cmd.read(get_property, property_id), EntityType.PROPERTY, property_id)


@router.patch("/properties/{property_id}")
def property_update(property_id: UUID, body: PropertyPatch, cmd: Cmd) -> Property:
    return cmd.run(update_property, property_id, **body.changes())


@router.post("/properties/{property_id}/corrections/notarized-on")
def property_correct_notarized_on(
    property_id: UUID, body: NotarizedOnCorrection, cmd: Cmd
) -> Property:
    return cmd.run(
        correct_notarized_on, property_id, body.new_date, purchase_price=body.purchase_price
    )


@router.post("/properties/{property_id}/corrections/purchase-date")
def property_correct_purchase_date(property_id: UUID, body: DateCorrection, cmd: Cmd) -> Property:
    return cmd.run(correct_purchase_date, property_id, body.new_date)


# --- Ownership -----------------------------------------------------------------------


@router.get("/properties/{property_id}/ownership")
def ownership_get(property_id: UUID, cmd: Cmd) -> PropertyOwnership:
    """404 when the property does not exist or no shares are set yet."""
    return found(cmd.read(get_ownership, property_id), OWNERSHIP, property_id)


@router.put("/properties/{property_id}/ownership")
def ownership_set(property_id: UUID, body: OwnershipBody, cmd: Cmd) -> PropertyOwnership:
    return cmd.run(set_ownership, property_id, body.shares, effective_date=body.effective_date)


# --- Acquisition costs and price splits (the AfA basis) -------------------------------


def _costs(conn: Connection, property_id: UUID) -> list[AcquisitionCost]:
    require(conn, get_property, EntityType.PROPERTY, property_id)
    return list_acquisition_costs(conn, property_id)


@router.get("/properties/{property_id}/acquisition-costs")
def acquisition_costs_list(property_id: UUID, cmd: Cmd) -> list[AcquisitionCost]:
    return cmd.read(_costs, property_id)


@router.post("/acquisition-costs", status_code=201)
def acquisition_cost_add(body: AcquisitionCostCreate, cmd: Cmd) -> AcquisitionCost:
    return cmd.run(add_acquisition_cost, body)


@router.patch("/acquisition-costs/{cost_id}")
def acquisition_cost_update(cost_id: UUID, body: AcquisitionCostPatch, cmd: Cmd) -> AcquisitionCost:
    """Change a Nebenkosten row; document_id sets, swaps or (null) clears its Beleg."""
    return cmd.run(update_acquisition_cost, cost_id, **body.changes())


@router.delete("/acquisition-costs/{cost_id}", status_code=204)
def acquisition_cost_remove(cost_id: UUID, cmd: Cmd) -> Response:
    """Delete a row entered by mistake; property.acquisition_cost_removed keeps what it said."""
    cmd.run(remove_acquisition_cost, cost_id)
    return Response(status_code=204)


def _splits(conn: Connection, property_id: UUID) -> list[PriceSplit]:
    require(conn, get_property, EntityType.PROPERTY, property_id)
    return list_price_splits(conn, property_id)


@router.get("/properties/{property_id}/price-splits")
def price_splits_list(property_id: UUID, cmd: Cmd) -> list[PriceSplit]:
    return cmd.read(_splits, property_id)


@router.post("/price-splits", status_code=201)
def price_split_record(body: PriceSplitCreate, cmd: Cmd) -> PriceSplit:
    return cmd.run(record_price_split, body)


@router.post("/properties/{property_id}/price-splits/{split_id}/select")
def price_split_select(property_id: UUID, split_id: UUID, cmd: Cmd) -> Property:
    return cmd.run(select_price_split, property_id, split_id)


# --- Units and loans of the property ---------------------------------------------------


def _units(conn: Connection, property_id: UUID) -> list[Unit]:
    require(conn, get_property, EntityType.PROPERTY, property_id)
    return list_units(conn, property_id)


@router.get("/properties/{property_id}/units")
def property_units(property_id: UUID, cmd: Cmd) -> list[Unit]:
    return cmd.read(_units, property_id)


def _loans(conn: Connection, property_id: UUID) -> list[Loan]:
    require(conn, get_property, EntityType.PROPERTY, property_id)
    return list_loans(conn, property_id)


@router.get("/properties/{property_id}/loans")
def property_loans(property_id: UUID, cmd: Cmd) -> list[Loan]:
    return cmd.read(_loans, property_id)


# --- Documents ---------------------------------------------------------------------------


@router.get("/properties/{property_id}/documents")
def property_documents(property_id: UUID, cmd: Cmd) -> list[RelatedDocument]:
    """The documents linked to the property or to one of its units, those units' tenancies
    or its loans, each once with every matching link in `via`. Unlike the tenancy, loan and
    tenant `/documents` reads, this rolls up children; owner and tenant links are not."""
    return cmd.read(related_documents(LinkTarget.PROPERTY, get_property), property_id)


# --- Document checklist (spec amendment 3) ------------------------------------------------


@router.get("/properties/{property_id}/checklist")
def property_checklist(
    property_id: UUID,
    cmd: Cmd,
    tax_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> DocumentChecklist:
    """The documents the property's tax year needs, with their status. tax_year defaults to
    the current (Berlin) year clamped into available_years; a year outside them is 422."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(document_checklist, property_id, tax_year=tax_year, today=today)
