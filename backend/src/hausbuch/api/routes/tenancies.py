"""Tenancies: rent schedule, corrections, deposit, notice, move-out.

Endpoints: docs/phases/phase-1-step-3.md §4.5. Rent rows are append-only (D12): a wrong row
is superseded by POST /rent-schedule/{row_id}/corrections, never edited. The milestones
(contract date, move-in, notice, move-out, each Kaution receipt) change only through their
corrections, which append correcting events.
"""

from datetime import date
from uuid import UUID

from fastapi import Response
from sqlalchemy import Connection

from hausbuch.schemas.api import (
    DateCorrection,
    DepositReceipt,
    DepositReceivedBody,
    EndTenancyBody,
    NoticeBody,
    RentRowCorrection,
    TenancyCreateRequest,
    TenancyPartiesBody,
    TenancyPatch,
)
from hausbuch.schemas.document import Document
from hausbuch.schemas.enums import EntityType, LinkTarget
from hausbuch.schemas.events import DepositReceived, Event, EventType
from hausbuch.schemas.tenancy import RentScheduleCreate, RentScheduleEntry, Tenancy
from hausbuch.services.events import list_events
from hausbuch.services.tenancies import (
    correct_deposit_received,
    correct_move_in,
    correct_move_out,
    correct_notice,
    correct_rent_row,
    correct_signed_on,
    create_tenancy,
    end_tenancy,
    get_tenancy,
    give_notice,
    list_rent_schedule,
    record_deposit_received,
    rent_on,
    schedule_rent,
    set_parties,
    update_tenancy,
)

from ..deps import Cmd, SettingsDep, domain_router, found, require
from .documents import linked_documents

router = domain_router()


@router.post("/tenancies", status_code=201)
def tenancy_create(body: TenancyCreateRequest, cmd: Cmd, settings: SettingsDep) -> Tenancy:
    return cmd.run(
        create_tenancy,
        body.as_create(),
        initial_rent=body.initial_rent,
        iban_key=settings.iban_hmac_key,
    )


@router.get("/tenancies/{tenancy_id}")
def tenancy_get(tenancy_id: UUID, cmd: Cmd) -> Tenancy:
    return found(cmd.read(get_tenancy, tenancy_id), EntityType.TENANCY, tenancy_id)


@router.patch("/tenancies/{tenancy_id}")
def tenancy_update(
    tenancy_id: UUID, body: TenancyPatch, cmd: Cmd, settings: SettingsDep
) -> Tenancy:
    """Befristung, Mietmodell and Kaution; the milestones change through corrections."""
    return cmd.run(update_tenancy, tenancy_id, **body.changes(), iban_key=settings.iban_hmac_key)


@router.put("/tenancies/{tenancy_id}/parties")
def tenancy_set_parties(tenancy_id: UUID, body: TenancyPartiesBody, cmd: Cmd) -> Tenancy:
    """Replace the parties (add, remove, change the primary). 422 rule_violation
    tenancy.exactly_one_primary / tenancy.no_tenant, 404 tenant for an unknown one."""
    return cmd.run(set_parties, tenancy_id, body.parties, effective_date=body.effective_date)


@router.post("/tenancies/{tenancy_id}/corrections/move-in")
def tenancy_correct_move_in(tenancy_id: UUID, body: DateCorrection, cmd: Cmd) -> Tenancy:
    return cmd.run(correct_move_in, tenancy_id, body.new_date)


@router.post("/tenancies/{tenancy_id}/corrections/signed-on")
def tenancy_correct_signed_on(tenancy_id: UUID, body: DateCorrection, cmd: Cmd) -> Tenancy:
    """The contract date (tenancy.created); also records one that was left empty."""
    return cmd.run(correct_signed_on, tenancy_id, body.new_date)


@router.post("/tenancies/{tenancy_id}/corrections/notice")
def tenancy_correct_notice(tenancy_id: UUID, body: NoticeBody, cmd: Cmd) -> Tenancy:
    """The notice already given (tenancy.notice_given); 409 if there is none."""
    return cmd.run(
        correct_notice,
        tenancy_id,
        given_by=body.given_by,
        given_on=body.given_on,
        ends_on=body.ends_on,
    )


@router.post("/tenancies/{tenancy_id}/corrections/move-out")
def tenancy_correct_move_out(tenancy_id: UUID, body: DateCorrection, cmd: Cmd) -> Tenancy:
    """The move-out date (tenancy.ended); 409 if the tenancy has not ended."""
    return cmd.run(correct_move_out, tenancy_id, body.new_date)


@router.post("/tenancies/{tenancy_id}/deposit-received", status_code=201)
def tenancy_deposit_received(tenancy_id: UUID, body: DepositReceivedBody, cmd: Cmd) -> Event:
    return cmd.run(
        record_deposit_received, tenancy_id, amount=body.amount, received_on=body.received_on
    )


def _deposits(conn: Connection, tenancy_id: UUID) -> list[DepositReceipt]:
    require(conn, get_tenancy, EntityType.TENANCY, tenancy_id)
    events = [
        e
        for e in list_events(conn, entity_type=EntityType.TENANCY, entity_id=tenancy_id)
        if e.type is EventType.TENANCY_DEPOSIT_RECEIVED
    ]
    corrected = {e.corrects_event_id for e in events if e.corrects_event_id is not None}
    return [
        DepositReceipt(
            event_id=e.id,
            amount=DepositReceived.model_validate(e.payload).amount,
            received_on=e.effective_date,
            recorded_at=e.recorded_at,
        )
        for e in events
        if e.id not in corrected
    ]


@router.get("/tenancies/{tenancy_id}/deposits")
def tenancy_deposits(tenancy_id: UUID, cmd: Cmd) -> list[DepositReceipt]:
    """The Kaution payments received (tenancy.deposit_received), in recording order."""
    return cmd.read(_deposits, tenancy_id)


@router.post("/tenancies/{tenancy_id}/deposits/{event_id}/corrections")
def tenancy_correct_deposit(
    tenancy_id: UUID, event_id: UUID, body: DepositReceivedBody, cmd: Cmd
) -> Event:
    """Correct one receipt from GET .../deposits (its event_id): the correcting event, or the
    receipt itself when nothing changes. 404 deposit_receipt for an id that is not one of this
    tenancy's receipts, 409 for one already corrected."""
    return cmd.run(
        correct_deposit_received,
        tenancy_id,
        event_id,
        amount=body.amount,
        received_on=body.received_on,
    )


@router.post("/tenancies/{tenancy_id}/notice")
def tenancy_notice(tenancy_id: UUID, body: NoticeBody, cmd: Cmd) -> Tenancy:
    return cmd.run(
        give_notice,
        tenancy_id,
        given_by=body.given_by,
        given_on=body.given_on,
        ends_on=body.ends_on,
    )


@router.post("/tenancies/{tenancy_id}/end")
def tenancy_end(tenancy_id: UUID, body: EndTenancyBody, cmd: Cmd) -> Tenancy:
    return cmd.run(end_tenancy, tenancy_id, body.end_date)


# --- Rent schedule ------------------------------------------------------------------


def _schedule(conn: Connection, tenancy_id: UUID) -> list[RentScheduleEntry]:
    require(conn, get_tenancy, EntityType.TENANCY, tenancy_id)
    return list_rent_schedule(conn, tenancy_id)


@router.get("/tenancies/{tenancy_id}/rent-schedule")
def tenancy_rent_schedule(tenancy_id: UUID, cmd: Cmd) -> list[RentScheduleEntry]:
    """Every row, superseded ones included (the UI greys them), in effective order."""
    return cmd.read(_schedule, tenancy_id)


def _rent_on(conn: Connection, tenancy_id: UUID, at: date) -> RentScheduleEntry | None:
    require(conn, get_tenancy, EntityType.TENANCY, tenancy_id)
    return rent_on(conn, tenancy_id, at)


@router.get("/tenancies/{tenancy_id}/rent")
def tenancy_rent_at(tenancy_id: UUID, at: date, cmd: Cmd) -> RentScheduleEntry | None:
    """The row in force on `at`, or null before the first row."""
    return cmd.read(_rent_on, tenancy_id, at)


@router.post("/rent-schedule", status_code=201)
def rent_schedule_add(body: RentScheduleCreate, cmd: Cmd) -> RentScheduleEntry:
    """A new dated row (Staffel step, increase, ...). reason "correction" is refused with 422:
    a correction supersedes a row through /rent-schedule/{row_id}/corrections."""
    return cmd.run(
        schedule_rent,
        body.tenancy_id,
        effective_from=body.effective_from,
        cold_rent=body.cold_rent,
        operating_costs_prepayment=body.operating_costs_prepayment,
        reason=body.reason,
        document_id=body.document_id,
    )


@router.post(
    "/rent-schedule/{row_id}/corrections",
    status_code=201,
    responses={200: {"model": RentScheduleEntry, "description": "Nothing to correct"}},
)
def rent_row_correct(
    row_id: UUID, body: RentRowCorrection, cmd: Cmd, response: Response
) -> RentScheduleEntry:
    """201 with the correction row; 200 with the row itself when the body changes nothing
    (the service writes nothing then)."""
    entry = cmd.run(correct_rent_row, row_id, **body.changes())
    if entry.id == row_id:
        response.status_code = 200
    return entry


@router.get("/tenancies/{tenancy_id}/documents")
def tenancy_documents(tenancy_id: UUID, cmd: Cmd) -> list[Document]:
    return cmd.read(linked_documents(LinkTarget.TENANCY, get_tenancy), tenancy_id)
