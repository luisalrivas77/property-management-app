"""Tenancy commands: tenancy, parties and the append-only rent schedule, with tenancy.* events
in the same transaction (D4).

- Move-in (tenancy.started) and move-out (tenancy.ended) are milestones; move-in changes only
  through correct_move_in, which appends a correcting event and moves the rent row that
  started on the old move-in date with it (§10.8 item 6).
- The contract date (tenancy.created), the notice, the move-out and each Kaution receipt are
  corrected the same way (correct_signed_on, correct_notice, correct_move_out,
  correct_deposit_received): the row follows and a correcting event repeats the milestone.
  A notice or move-out cannot be withdrawn: a correction keeps the event's type and needs a
  date, so there is nothing to say "it never happened" with.
- The parties change through set_parties (the whole list, exactly one primary), which
  appends tenancy.parties_set with tenant ids and roles only (D5).
- The other fields (Befristung, Mietmodell, Kaution) change through update_tenancy, which
  appends tenancy.updated with old and new values (the deposit account is the landlord's, D5,
  and appears masked only).
- Rent rows are never edited: a wrong row is superseded by a correction row (D12), and its
  tenancy.rent_scheduled event is corrected by the new row's event.
- No two tenancies of a unit overlap: the database's exclusion constraint decides, and its
  error becomes TenancyOverlapError.
- Tenancy events carry the unit's property_id and unit_id (History scope).
"""

from collections.abc import Set
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, Row, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import rent_schedule as rent_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import tenancy_tenant as party_table
from hausbuch.db.tables import tenant as tenant_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.rules.rent import rent_at
from hausbuch.rules.validation import RENT_OUTSIDE_TENANCY, check_rent_row, check_tenancy_parties
from hausbuch.schemas.enums import DepositForm, EntityType, RentChangeReason, RentModel, TenancyRole
from hausbuch.schemas.events import (
    DepositReceived,
    Empty,
    Event,
    EventType,
    FieldChange,
    NoticeGiven,
    RentScheduled,
    TenancyCreated,
    TenancyPartiesSet,
    Updated,
)
from hausbuch.schemas.tenancy import (  # InitialRent is re-exported: it used to live here
    InitialRent,
    RentScheduleCreate,
    RentScheduleEntry,
    Tenancy,
    TenancyCreate,
    TenancyParty,
)

from .context import CommandContext
from .errors import (
    ConflictError,
    NotFoundError,
    RuleViolationError,
    TenancyOverlapError,
    require_valid,
    violated_constraint,
)
from .events import (
    append_event,
    diff_changes,
    find_event,
    get_event,
    latest_in_chain,
    milestone_root,
)
from .iban import protect_iban
from .lookup import require_document, require_row
from .owners import UNSET, Unset

_OVERLAP = "tenancy_no_overlap"
_SUPERSEDED_ONCE = "rent_schedule_supersedes_id_key"
_DATE_FIELDS = set(TenancyCreate.model_fields) - {"deposit_iban"}


def create_tenancy(
    ctx: CommandContext,
    data: TenancyCreate,
    *,
    initial_rent: InitialRent | None,
    iban_key: bytes,
) -> Tenancy:
    """The tenancy and its parties, plus tenancy.created (signed_on), tenancy.started
    (start_date), the first rent row when given, and tenancy.ended if end_date is known.

    initial_rent=None records no rent row (add one later with schedule_rent)."""
    require_valid(check_tenancy_parties([p.role for p in data.parties]), "tenancy parties")
    tenant_ids = [p.tenant_id for p in data.parties]
    if len(set(tenant_ids)) != len(tenant_ids):
        raise ValueError("each tenant may be a party of a tenancy only once")
    rent = None
    if initial_rent is not None:
        rent_from = initial_rent.effective_from or data.start_date
        require_valid(check_rent_row(data.start_date, data.end_date, rent_from), "initial rent")
        rent = initial_rent.model_copy(update={"effective_from": rent_from})
    deposit_iban = None if data.deposit_iban is None else protect_iban(data.deposit_iban, iban_key)

    unit = require_row(ctx.conn, unit_table, EntityType.UNIT, data.unit_id)
    _require_tenants(ctx.conn, tenant_ids)
    if rent is not None:
        require_document(ctx.conn, rent.document_id)

    row = _write_tenancy(
        ctx,
        insert(tenancy_table).values(
            workspace_id=ctx.workspace_id,
            **data.model_dump(exclude={"parties", "deposit_iban"}),
            deposit_iban_masked=None if deposit_iban is None else deposit_iban.masked,
            deposit_iban_hmac=None if deposit_iban is None else deposit_iban.hmac,
            created_at=ctx.now,
            updated_at=ctx.now,
        ),
    )
    ctx.conn.execute(
        insert(party_table),
        [
            {
                "workspace_id": ctx.workspace_id,
                "tenancy_id": row.id,
                "tenant_id": p.tenant_id,
                "role": p.role.value,
            }
            for p in data.parties
        ],
    )
    scope = {"property_id": unit.property_id, "unit_id": unit.id}
    append_event(
        ctx,
        EventType.TENANCY_CREATED,
        row.id,
        TenancyCreated(
            tenant_ids=tenant_ids,
            rent_model=row.rent_model,
            fixed_term_until=row.fixed_term_until,
            deposit_amount=row.deposit_amount,
        ),
        effective_date=row.signed_on,
        **scope,
    )
    append_event(
        ctx, EventType.TENANCY_STARTED, row.id, Empty(), effective_date=row.start_date, **scope
    )
    if rent is not None:
        _insert_rent_row(
            ctx,
            RentScheduleCreate(
                tenancy_id=row.id,
                effective_from=rent.effective_from,
                cold_rent=rent.cold_rent,
                operating_costs_prepayment=rent.operating_costs_prepayment,
                reason=RentChangeReason.INITIAL,
                document_id=rent.document_id,
            ),
            scope,
        )
    if row.end_date is not None:
        append_event(
            ctx, EventType.TENANCY_ENDED, row.id, Empty(), effective_date=row.end_date, **scope
        )
    return _to_tenancy(ctx.conn, row)


def correct_move_in(ctx: CommandContext, tenancy_id: UUID, new_date: date) -> Tenancy:
    """Correct the move-in date: updates start_date and appends a correcting tenancy.started
    against the latest event in its chain. The move-in cannot be after the end date of a
    notice already given.

    The rent row in force that started on the old move-in date moves with it, in the same
    transaction, through correct_rent_row (a correction row plus a correcting
    tenancy.rent_scheduled event, so History shows it corrected). Every other rent row in
    force keeps its date and must stay inside the tenancy."""
    row, scope = _lock(ctx.conn, tenancy_id)
    current = _to_tenancy(ctx.conn, row)
    _validate_dates(current, start_date=new_date)
    if new_date == current.start_date:
        return current
    if current.notice_given_on is not None:
        notice_root = milestone_root(ctx.conn, EventType.TENANCY_NOTICE_GIVEN, tenancy_id)
        notice = NoticeGiven.model_validate(latest_in_chain(ctx.conn, notice_root.id).payload)
        if new_date > notice.ends_on:
            raise ValueError(
                f"a notice ends the tenancy on {notice.ends_on}; it cannot start after that"
            )
    in_force = _rows_in_force(ctx.conn, tenancy_id)
    first = [r for r in in_force if r.effective_from == row.start_date]
    if len(first) > 1:
        # Two rows on one date tie on effective_from. Moving both in one command would give
        # them the same recorded_at, so which one applies would hang on their random ids.
        raise ValueError(
            f"{len(first)} rent rows start on {row.start_date}; correct all but one of them "
            "before correcting the move-in"
        )
    if first:
        # The moved row would tie with a later row on the new date and, being recorded last,
        # silently replace it. (A row before the new date is refused as outside, below.)
        reached = [r for r in in_force if r not in first and r.effective_from == new_date]
        if reached:
            raise ValueError(
                f"the rent row from {row.start_date} would move onto the row from "
                f"{reached[0].effective_from}; correct one of them first"
            )
    _require_rows_inside(
        ctx.conn, tenancy_id, new_date, current.end_date, moving={r.id for r in first}
    )

    root = milestone_root(ctx.conn, EventType.TENANCY_STARTED, tenancy_id)
    latest = latest_in_chain(ctx.conn, root.id)
    updated = _update_tenancy(ctx, tenancy_id, start_date=new_date)
    append_event(
        ctx,
        EventType.TENANCY_STARTED,
        tenancy_id,
        Empty(),
        effective_date=new_date,
        corrects_event_id=latest.id,
        **scope,
    )
    for rent_row in first:  # after the update, so correct_rent_row checks the new start date
        correct_rent_row(ctx, rent_row.id, effective_from=new_date)
    return _to_tenancy(ctx.conn, updated)


def update_tenancy(
    ctx: CommandContext,
    tenancy_id: UUID,
    *,
    fixed_term_until: date | None | Unset = UNSET,
    rent_model: RentModel | Unset = UNSET,
    deposit_amount: Decimal | Unset = UNSET,
    deposit_form: DepositForm | Unset = UNSET,
    deposit_bank: str | None | Unset = UNSET,
    deposit_iban: str | None | Unset = UNSET,
    iban_key: bytes | None = None,
) -> Tenancy:
    """Change the non-milestone fields. The tenancy as a whole must still pass create's rules
    (Befristung not before move-in; deposit form 'none' exactly when the amount is 0).
    deposit_iban is write-only: None removes it; tenancy.updated shows only its mask.
    No-op (no write, no event) if nothing changes."""
    requested = {
        name: value
        for name, value in (
            ("fixed_term_until", fixed_term_until),
            ("rent_model", rent_model),
            ("deposit_amount", deposit_amount),
            ("deposit_form", deposit_form),
            ("deposit_bank", deposit_bank),
        )
        if value is not UNSET
    }
    account = None
    if deposit_iban is not UNSET and deposit_iban is not None:
        if iban_key is None:
            raise ValueError("iban_key is required to change a tenancy's deposit IBAN")
        account = protect_iban(deposit_iban, iban_key)

    row, scope = _lock(ctx.conn, tenancy_id)
    current = _to_tenancy(ctx.conn, row)
    new = TenancyCreate.model_validate(current.model_dump(include=_DATE_FIELDS) | requested)
    old = current.model_dump(include=set(requested))
    values = new.model_dump(include=set(requested))
    changes = diff_changes(old, values)
    values = {name: values[name] for name in changes}
    if deposit_iban is not UNSET:
        new_hmac = None if account is None else account.hmac
        if new_hmac != row.deposit_iban_hmac:
            new_mask = None if account is None else account.masked
            values |= {"deposit_iban_masked": new_mask, "deposit_iban_hmac": new_hmac}
            # Always recorded, even when two accounts happen to share a mask.
            changes["deposit_iban_masked"] = FieldChange(
                old=current.deposit_iban_masked, new=new_mask
            )
    if not values:
        return current

    updated = _update_tenancy(ctx, tenancy_id, **values)
    append_event(ctx, EventType.TENANCY_UPDATED, tenancy_id, Updated(changes=changes), **scope)
    return _to_tenancy(ctx.conn, updated)


def set_parties(
    ctx: CommandContext,
    tenancy_id: UUID,
    parties: list[TenancyParty],
    *,
    effective_date: date | None = None,
) -> Tenancy:
    """Replace the tenancy's parties with `parties`: add a tenant, remove one, or make
    another the primary. Exactly one primary, each tenant once, at least one party. A tenant
    who joins must exist and not be erased; one who stays may have been erased after move-out.
    effective_date (optional) is when the change took effect: not before the contract (or
    the move-in) and not after the move-out. Appends tenancy.parties_set with ids and roles
    only. No-op if the parties are the same."""
    require_valid(check_tenancy_parties([p.role for p in parties]), "tenancy parties")
    tenant_ids = [p.tenant_id for p in parties]
    if len(set(tenant_ids)) != len(tenant_ids):
        raise ValueError("each tenant may be a party of a tenancy only once")
    row, scope = _lock(ctx.conn, tenancy_id)
    current = _to_tenancy(ctx.conn, row)
    if effective_date is not None:
        earliest = current.signed_on or current.start_date
        if effective_date < earliest or (
            current.end_date is not None and effective_date > current.end_date
        ):
            raise ValueError("a change of parties must fall within the tenancy")
    before = {(p.tenant_id, p.role) for p in current.parties}
    if {(p.tenant_id, p.role) for p in parties} == before:
        return current
    kept = {p.tenant_id for p in current.parties}
    _require_tenants(ctx.conn, [t for t in tenant_ids if t not in kept])

    # The primary index is not deferrable, so the rows are replaced rather than updated.
    ctx.conn.execute(delete(party_table).where(party_table.c.tenancy_id == tenancy_id))
    ctx.conn.execute(
        insert(party_table),
        [
            {
                "workspace_id": ctx.workspace_id,
                "tenancy_id": tenancy_id,
                "tenant_id": p.tenant_id,
                "role": p.role.value,
            }
            for p in parties
        ],
    )
    updated = _update_tenancy(ctx, tenancy_id)
    result = _to_tenancy(ctx.conn, updated)
    append_event(
        ctx,
        EventType.TENANCY_PARTIES_SET,
        tenancy_id,
        TenancyPartiesSet(parties=result.parties),
        effective_date=effective_date,
        **scope,
    )
    return result


def correct_signed_on(ctx: CommandContext, tenancy_id: UUID, new_date: date) -> Tenancy:
    """Correct (or, if it was unknown, record) the contract date: updates signed_on and
    appends a correcting tenancy.created with the corrected event's payload unchanged. It
    cannot be after the move-in. No-op if it is already the stored date."""
    row, scope = _lock(ctx.conn, tenancy_id)
    current = _to_tenancy(ctx.conn, row)
    _validate_dates(current, signed_on=new_date)
    if new_date == current.signed_on:
        return current
    latest = latest_in_chain(
        ctx.conn, milestone_root(ctx.conn, EventType.TENANCY_CREATED, tenancy_id).id
    )
    updated = _update_tenancy(ctx, tenancy_id, signed_on=new_date)
    append_event(
        ctx,
        EventType.TENANCY_CREATED,
        tenancy_id,
        TenancyCreated.model_validate(latest.payload),
        effective_date=new_date,
        corrects_event_id=latest.id,
        **scope,
    )
    return _to_tenancy(ctx.conn, updated)


def correct_notice(
    ctx: CommandContext,
    tenancy_id: UUID,
    *,
    given_by: Literal["tenant", "landlord"],
    given_on: date,
    ends_on: date,
) -> Tenancy:
    """Correct the notice already given (who, when, to which date): updates notice_given_on
    and appends a correcting tenancy.notice_given. The same rules as give_notice, except that
    the tenancy may already have ended; then the notice cannot be dated after the move-out.
    No-op if nothing changes."""
    payload = NoticeGiven(given_by=given_by, ends_on=ends_on)
    if ends_on < given_on:
        raise ValueError("a notice cannot end the tenancy before it is given")
    row, scope = _lock(ctx.conn, tenancy_id)
    current = _to_tenancy(ctx.conn, row)
    if current.notice_given_on is None:
        raise ConflictError("no notice was given, so there is none to correct")
    if ends_on < current.start_date:
        raise ValueError("a notice cannot end the tenancy before it starts")
    if current.end_date is not None and given_on > current.end_date:
        raise ValueError("a notice cannot be given after the move-out")
    latest = latest_in_chain(
        ctx.conn, milestone_root(ctx.conn, EventType.TENANCY_NOTICE_GIVEN, tenancy_id).id
    )
    if latest.effective_date == given_on and NoticeGiven.model_validate(latest.payload) == payload:
        return current
    updated = _update_tenancy(ctx, tenancy_id, notice_given_on=given_on)
    append_event(
        ctx,
        EventType.TENANCY_NOTICE_GIVEN,
        tenancy_id,
        payload,
        effective_date=given_on,
        corrects_event_id=latest.id,
        **scope,
    )
    return _to_tenancy(ctx.conn, updated)


def correct_move_out(ctx: CommandContext, tenancy_id: UUID, new_date: date) -> Tenancy:
    """Correct the move-out date: updates end_date and appends a correcting tenancy.ended.
    As on end_tenancy, the rent rows in force must stay inside the tenancy, and the unit's
    other tenancies must not overlap. When a notice was given, the move-out cannot be before
    it. No-op if it is already the stored date."""
    row, scope = _lock(ctx.conn, tenancy_id)
    current = _to_tenancy(ctx.conn, row)
    if current.end_date is None:
        raise ConflictError("the tenancy has not ended, so there is no move-out to correct")
    _validate_dates(current, end_date=new_date)
    if current.notice_given_on is not None and new_date < current.notice_given_on:
        raise ValueError("the move-out cannot be before the notice was given")
    if new_date == current.end_date:
        return current
    _require_rows_inside(ctx.conn, tenancy_id, current.start_date, new_date)
    latest = latest_in_chain(
        ctx.conn, milestone_root(ctx.conn, EventType.TENANCY_ENDED, tenancy_id).id
    )
    updated = _update_tenancy(ctx, tenancy_id, end_date=new_date)
    append_event(
        ctx,
        EventType.TENANCY_ENDED,
        tenancy_id,
        Empty(),
        effective_date=new_date,
        corrects_event_id=latest.id,
        **scope,
    )
    return _to_tenancy(ctx.conn, updated)


def correct_deposit_received(
    ctx: CommandContext,
    tenancy_id: UUID,
    event_id: UUID,
    *,
    amount: Decimal,
    received_on: date,
) -> Event:
    """Correct one Kaution receipt (amount and date): appends a tenancy.deposit_received that
    corrects it. event_id must be a receipt of this tenancy that is not yet corrected (the
    ids GET .../deposits lists). No-op (returns the receipt) if nothing changes."""
    payload = DepositReceived(amount=amount)
    _, scope = _lock(ctx.conn, tenancy_id)
    receipt = get_event(ctx.conn, event_id)
    if (
        receipt is None
        or receipt.type is not EventType.TENANCY_DEPOSIT_RECEIVED
        or receipt.entity_id != tenancy_id
    ):
        raise NotFoundError("deposit_receipt", event_id)
    latest = latest_in_chain(ctx.conn, event_id)
    if latest.id != event_id:
        raise ConflictError(
            f"deposit receipt {event_id} is already corrected by {latest.id}; correct that one"
        )
    recorded = DepositReceived.model_validate(receipt.payload)
    if receipt.effective_date == received_on and recorded.amount == payload.amount:
        return receipt
    return append_event(
        ctx,
        EventType.TENANCY_DEPOSIT_RECEIVED,
        tenancy_id,
        payload,
        effective_date=received_on,
        corrects_event_id=event_id,
        **scope,
    )


def record_deposit_received(
    ctx: CommandContext, tenancy_id: UUID, *, amount: Decimal, received_on: date
) -> Event:
    """Kaution (or an instalment of it) received. A milestone only; no state changes."""
    payload = DepositReceived(amount=amount)
    _, scope = _lock(ctx.conn, tenancy_id)
    return append_event(
        ctx,
        EventType.TENANCY_DEPOSIT_RECEIVED,
        tenancy_id,
        payload,
        effective_date=received_on,
        **scope,
    )


def give_notice(
    ctx: CommandContext,
    tenancy_id: UUID,
    *,
    given_by: Literal["tenant", "landlord"],
    given_on: date,
    ends_on: date,
) -> Tenancy:
    """Record a notice (Kündigung). Sets notice_given_on; the move-out itself is end_tenancy."""
    payload = NoticeGiven(given_by=given_by, ends_on=ends_on)
    if ends_on < given_on:
        raise ValueError("a notice cannot end the tenancy before it is given")
    row, scope = _lock(ctx.conn, tenancy_id)
    if row.notice_given_on is not None:
        raise ConflictError(f"notice was already given on {row.notice_given_on}")
    if row.end_date is not None:
        raise ConflictError(f"the tenancy already ended on {row.end_date}")
    if ends_on < row.start_date:
        raise ValueError("a notice cannot end the tenancy before it starts")
    updated = _update_tenancy(ctx, tenancy_id, notice_given_on=given_on)
    append_event(
        ctx, EventType.TENANCY_NOTICE_GIVEN, tenancy_id, payload, effective_date=given_on, **scope
    )
    return _to_tenancy(ctx.conn, updated)


def end_tenancy(ctx: CommandContext, tenancy_id: UUID, end_date: date) -> Tenancy:
    """Move-out: sets end_date and appends tenancy.ended. Rent rows in force must not start
    after it. Once set, the end date is a milestone, changed only by correct_move_out."""
    row, scope = _lock(ctx.conn, tenancy_id)
    current = _to_tenancy(ctx.conn, row)
    if current.end_date is not None:
        raise ConflictError(f"the tenancy already ended on {current.end_date}")
    _validate_dates(current, end_date=end_date)
    _require_rows_inside(ctx.conn, tenancy_id, current.start_date, end_date)
    updated = _update_tenancy(ctx, tenancy_id, end_date=end_date)
    append_event(
        ctx, EventType.TENANCY_ENDED, tenancy_id, Empty(), effective_date=end_date, **scope
    )
    return _to_tenancy(ctx.conn, updated)


def get_tenancy(conn: Connection, tenancy_id: UUID) -> Tenancy | None:
    row = conn.execute(select(tenancy_table).where(tenancy_table.c.id == tenancy_id)).one_or_none()
    return None if row is None else _to_tenancy(conn, row)


def list_tenancies(conn: Connection, unit_id: UUID) -> list[Tenancy]:
    """Every tenancy of the unit, ended ones included, by move-in date."""
    rows = conn.execute(
        select(tenancy_table)
        .where(tenancy_table.c.unit_id == unit_id)
        .order_by(tenancy_table.c.start_date, tenancy_table.c.id)
    ).all()
    return [_to_tenancy(conn, r) for r in rows]


def list_tenancies_of_tenant(conn: Connection, tenant_id: UUID) -> list[Tenancy]:
    """Every tenancy the tenant is a party of now, ended ones included, by move-in date.

    A tenant removed from a tenancy (set_parties) is no longer a party, so it is not listed
    here; retention still follows it (parties.tenancies_ever_of)."""
    rows = conn.execute(
        select(tenancy_table)
        .join(party_table, party_table.c.tenancy_id == tenancy_table.c.id)
        .where(party_table.c.tenant_id == tenant_id)
        .order_by(tenancy_table.c.start_date, tenancy_table.c.id)
    ).all()
    return [_to_tenancy(conn, r) for r in rows]


# --- Rent schedule -----------------------------------------------------------------


def schedule_rent(
    ctx: CommandContext,
    tenancy_id: UUID,
    *,
    effective_from: date,
    cold_rent: Decimal,
    operating_costs_prepayment: Decimal = Decimal("0"),
    reason: RentChangeReason,
    document_id: UUID | None = None,
) -> RentScheduleEntry:
    """Append a dated rent row (Staffel step, increase, ...). Use correct_rent_row to fix one."""
    if reason is RentChangeReason.CORRECTION:
        raise ValueError("a correction supersedes a row: use correct_rent_row")
    data = RentScheduleCreate(
        tenancy_id=tenancy_id,
        effective_from=effective_from,
        cold_rent=cold_rent,
        operating_costs_prepayment=operating_costs_prepayment,
        reason=reason,
        document_id=document_id,
    )
    row, scope = _lock(ctx.conn, tenancy_id)
    require_valid(check_rent_row(row.start_date, row.end_date, effective_from), "rent row")
    require_document(ctx.conn, document_id)
    return _insert_rent_row(ctx, data, scope)


def correct_rent_row(
    ctx: CommandContext,
    row_id: UUID,
    *,
    effective_from: date | Unset = UNSET,
    cold_rent: Decimal | Unset = UNSET,
    operating_costs_prepayment: Decimal | Unset = UNSET,
    document_id: UUID | None | Unset = UNSET,
) -> RentScheduleEntry:
    """Supersede a wrong rent row with a correction row (D12). Fields not passed keep the
    wrong row's values. Appends a tenancy.rent_scheduled event that corrects the wrong row's
    event, so History shows one corrected entry. No-op if nothing changes."""
    wrong = RentScheduleEntry.model_validate(
        dict(require_row(ctx.conn, rent_table, "rent_schedule", row_id)._mapping)
    )
    tenancy, scope = _lock(ctx.conn, wrong.tenancy_id)
    newer = ctx.conn.execute(
        select(rent_table.c.id).where(rent_table.c.supersedes_id == row_id)
    ).scalar_one_or_none()
    if newer is not None:
        raise ConflictError(
            f"rent row {row_id} is already superseded by {newer}; correct that row instead"
        )

    requested = {
        name: value
        for name, value in (
            ("effective_from", effective_from),
            ("cold_rent", cold_rent),
            ("operating_costs_prepayment", operating_costs_prepayment),
            ("document_id", document_id),
        )
        if value is not UNSET
    }
    kept = wrong.model_dump(
        include={"tenancy_id", "effective_from", "cold_rent", "operating_costs_prepayment"}
    ) | {"document_id": wrong.document_id}
    correction = RentScheduleCreate.model_validate(
        kept | requested | {"reason": RentChangeReason.CORRECTION, "supersedes_id": row_id}
    )
    if all(getattr(correction, name) == getattr(wrong, name) for name in kept):
        return wrong
    require_valid(
        check_rent_row(tenancy.start_date, tenancy.end_date, correction.effective_from),
        "rent row",
    )
    require_document(ctx.conn, correction.document_id)

    wrong_event = find_event(
        ctx.conn,
        EventType.TENANCY_RENT_SCHEDULED,
        wrong.tenancy_id,
        payload_contains={"rent_schedule_id": row_id},
    )
    if wrong_event is None:
        raise NotFoundError("rent_schedule event", row_id)
    latest = latest_in_chain(ctx.conn, wrong_event.id)
    return _insert_rent_row(ctx, correction, scope, corrects_event_id=latest.id)


def list_rent_schedule(conn: Connection, tenancy_id: UUID) -> list[RentScheduleEntry]:
    """Every rent row of the tenancy, superseded ones included, in effective order."""
    rows = conn.execute(
        select(rent_table)
        .where(rent_table.c.tenancy_id == tenancy_id)
        .order_by(rent_table.c.effective_from, rent_table.c.recorded_at, rent_table.c.id)
    )
    return [RentScheduleEntry.model_validate(dict(r._mapping)) for r in rows]


def rent_on(conn: Connection, tenancy_id: UUID, at: date) -> RentScheduleEntry | None:
    """The rent row in force on `at` (rules.rent.rent_at), or None before the first row."""
    return rent_at(list_rent_schedule(conn, tenancy_id), at)


def _insert_rent_row(
    ctx: CommandContext,
    data: RentScheduleCreate,
    scope: dict[str, UUID],
    *,
    corrects_event_id: UUID | None = None,
) -> RentScheduleEntry:
    try:
        row = ctx.conn.execute(
            insert(rent_table)
            .values(workspace_id=ctx.workspace_id, **data.model_dump(), recorded_at=ctx.now)
            .returning(*rent_table.c)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _SUPERSEDED_ONCE:
            raise ConflictError(
                f"rent row {data.supersedes_id} was superseded concurrently",
                constraint=_SUPERSEDED_ONCE,
            ) from exc
        raise
    entry = RentScheduleEntry.model_validate(dict(row._mapping))
    append_event(
        ctx,
        EventType.TENANCY_RENT_SCHEDULED,
        entry.tenancy_id,
        RentScheduled(
            rent_schedule_id=entry.id,
            effective_from=entry.effective_from,
            cold_rent=entry.cold_rent,
            operating_costs_prepayment=entry.operating_costs_prepayment,
            reason=entry.reason,
            supersedes_id=entry.supersedes_id,
        ),
        effective_date=entry.effective_from,
        corrects_event_id=corrects_event_id,
        **scope,
    )
    return entry


def _rows_in_force(conn: Connection, tenancy_id: UUID) -> list[RentScheduleEntry]:
    """The tenancy's rent rows that no correction supersedes, in effective order."""
    rows = list_rent_schedule(conn, tenancy_id)
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [r for r in rows if r.id not in superseded]


def _require_rows_inside(
    conn: Connection,
    tenancy_id: UUID,
    start_date: date,
    end_date: date | None,
    *,
    moving: Set[UUID] = frozenset(),
) -> None:
    """Every rent row still in force (not superseded) must fall inside the new dates, except
    the rows in `moving`, which the caller corrects to a date inside them."""
    outside = [
        r
        for r in _rows_in_force(conn, tenancy_id)
        if r.id not in moving and check_rent_row(start_date, end_date, r.effective_from)
    ]
    if outside:
        raise RuleViolationError(
            [RENT_OUTSIDE_TENANCY],
            f"rent rows from {', '.join(str(r.effective_from) for r in outside)} would fall "
            "outside the tenancy; correct them first",
        )


# --- Rows ------------------------------------------------------------------------


def _require_tenants(conn: Connection, tenant_ids: list[UUID]) -> None:
    """The parties exist and are not erased. FOR KEY SHARE holds them until commit, so a
    concurrent erase_tenant (FOR UPDATE) waits and then sees this tenancy's retention hold;
    if the erasure locked first, this read waits and then sees erased_at."""
    found = {
        r.id: r
        for r in conn.execute(
            select(tenant_table.c.id, tenant_table.c.erased_at)
            .where(tenant_table.c.id.in_(tenant_ids))
            .with_for_update(read=True, key_share=True)
        )
    }
    for tenant_id in tenant_ids:
        if tenant_id not in found:
            raise NotFoundError(EntityType.TENANT, tenant_id)
        if found[tenant_id].erased_at is not None:
            raise ConflictError(f"tenant {tenant_id} was erased")


def _lock(conn: Connection, tenancy_id: UUID) -> tuple[Row, dict[str, UUID]]:
    """The tenancy row FOR UPDATE, and the History scope of its events."""
    row = require_row(conn, tenancy_table, EntityType.TENANCY, tenancy_id, for_update=True)
    property_id = conn.execute(
        select(unit_table.c.property_id).where(unit_table.c.id == row.unit_id)
    ).scalar_one()
    return row, {"property_id": property_id, "unit_id": row.unit_id}


def _validate_dates(current: Tenancy, **changes: date) -> None:
    """The changed dates must pass TenancyCreate's checks (end after start, signed before)."""
    TenancyCreate.model_validate(current.model_dump(include=_DATE_FIELDS) | changes)


def _update_tenancy(ctx: CommandContext, tenancy_id: UUID, **values: Any) -> Row:
    return _write_tenancy(
        ctx,
        update(tenancy_table)
        .where(tenancy_table.c.id == tenancy_id)
        .values(**values, updated_at=ctx.now),
    )


def _write_tenancy(ctx: CommandContext, statement: Any) -> Row:
    try:
        return ctx.conn.execute(statement.returning(*tenancy_table.c)).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _OVERLAP:
            raise TenancyOverlapError(
                "another tenancy of this unit overlaps these dates", constraint=_OVERLAP
            ) from exc
        raise


def _to_tenancy(conn: Connection, row: Row) -> Tenancy:
    parties = [
        TenancyParty(tenant_id=p.tenant_id, role=p.role)
        for p in conn.execute(
            select(party_table.c.tenant_id, party_table.c.role)
            .where(party_table.c.tenancy_id == row.id)
            .order_by(party_table.c.role != TenancyRole.PRIMARY.value, party_table.c.tenant_id)
        )
    ]
    values = {k: v for k, v in row._mapping.items() if k != "deposit_iban_hmac"}
    return Tenancy.model_validate(values | {"parties": parties})
