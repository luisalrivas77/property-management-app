"""Tenancy commands and the rent schedule end to end, as hausbuch_app under RLS."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from conftest import TEST_IBAN_KEY
from pydantic import ValidationError
from sqlalchemy import text

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import BAD_LENGTH, iban_hmac
from hausbuch.rules.validation import EXACTLY_ONE_PRIMARY, RENT_OUTSIDE_TENANCY
from hausbuch.schemas.enums import (
    ActorType,
    DepositForm,
    PropertyKind,
    RentChangeReason,
    RentModel,
    TenancyRole,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.schemas.tenancy import TenancyCreate, TenancyParty, TenantCreate
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import (
    ConflictError,
    NotFoundError,
    RuleViolationError,
    TenancyOverlapError,
)
from hausbuch.services.events import list_events
from hausbuch.services.properties import create_property
from hausbuch.services.tenancies import (
    InitialRent,
    correct_move_in,
    correct_rent_row,
    create_tenancy,
    end_tenancy,
    get_tenancy,
    give_notice,
    list_rent_schedule,
    record_deposit_received,
    rent_on,
    schedule_rent,
)
from hausbuch.services.tenants import create_tenant, erase_tenant
from hausbuch.services.units import create_unit

CREATED_AT = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
DEPOSIT_IBAN = "DE89370400440532013000"
RENT = InitialRent(cold_rent=Decimal("950.00"), operating_costs_prepayment=Decimal("220.00"))


@pytest.fixture
def flat(run, fresh_workspaces):
    """(workspace, property, unit, two tenants) for one test."""
    a, _ = fresh_workspaces
    prop = run(
        a,
        create_property,
        PropertyCreate(
            label="Wohnung",
            kind=PropertyKind.ETW,
            address=Address(
                street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"
            ),
            build_year=1994,
            notarized_on=date(2024, 3, 15),
            purchase_date=date(2024, 5, 1),
            purchase_price=Decimal("289000.00"),
        ),
        now=CREATED_AT,
    )
    unit = run(
        a,
        create_unit,
        UnitCreate(property_id=prop.id, label="WE 3", floor_area_m2=Decimal("68.40")),
        now=CREATED_AT,
    )
    tenants = [
        run(a, create_tenant, TenantCreate(full_name=name), iban_key=TEST_IBAN_KEY).id
        for name in ("Anna Beispiel", "Ben Beispiel")
    ]
    return a, prop, unit, tenants


def _lease(unit, tenants, **overrides):
    values = dict(
        unit_id=unit.id,
        parties=[TenancyParty(tenant_id=tenants[0], role=TenancyRole.PRIMARY)],
        signed_on=date(2024, 6, 10),
        start_date=date(2024, 7, 1),
        rent_model=RentModel.STAFFEL,
        deposit_amount=Decimal("2850.00"),
        deposit_form=DepositForm.CASH_ACCOUNT,
        deposit_bank="Sparkasse",
        deposit_iban=DEPOSIT_IBAN,
    )
    return TenancyCreate(**(values | overrides))


def _create(run, ws, data, initial_rent=RENT, now=CREATED_AT):
    return run(ws, create_tenancy, data, initial_rent=initial_rent, iban_key=TEST_IBAN_KEY, now=now)


def _events(read, ws, tenancy_id):
    return read(ws, list_events, entity_id=tenancy_id)


def _tenancy_count(app_engine, ws, unit_id):
    with workspace_transaction(app_engine, ws) as conn:
        return conn.execute(
            text("SELECT count(*) FROM tenancy WHERE unit_id = :u"), {"u": unit_id}
        ).scalar_one()


# --- create ------------------------------------------------------------------


def test_create_writes_tenancy_parties_rent_and_milestones(app_engine, run, read, flat):
    a, prop, unit, (anna, ben) = flat
    lease = _lease(
        unit,
        [anna],
        parties=[
            TenancyParty(tenant_id=ben, role=TenancyRole.CO_TENANT),
            TenancyParty(tenant_id=anna, role=TenancyRole.PRIMARY),
        ],
    )
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        tenancy = create_tenancy(ctx, lease, initial_rent=RENT, iban_key=TEST_IBAN_KEY)

    assert tenancy.parties == [
        TenancyParty(tenant_id=anna, role=TenancyRole.PRIMARY),
        TenancyParty(tenant_id=ben, role=TenancyRole.CO_TENANT),
    ]
    assert (tenancy.start_date, tenancy.end_date, tenancy.signed_on) == (
        date(2024, 7, 1),
        None,
        date(2024, 6, 10),
    )
    assert tenancy.deposit_iban_masked == "DE89 **** 3000"
    assert read(a, get_tenancy, tenancy.id) == tenancy
    with workspace_transaction(app_engine, a) as conn:
        digest = conn.execute(
            text("SELECT deposit_iban_hmac FROM tenancy WHERE id = :id"), {"id": tenancy.id}
        ).scalar_one()
    assert digest == iban_hmac(DEPOSIT_IBAN, TEST_IBAN_KEY)

    [row] = read(a, list_rent_schedule, tenancy.id)
    assert (row.effective_from, row.cold_rent, row.reason) == (
        date(2024, 7, 1),
        Decimal("950.00"),
        RentChangeReason.INITIAL,
    )
    assert read(a, rent_on, tenancy.id, date(2024, 6, 30)) is None
    assert read(a, rent_on, tenancy.id, date(2025, 1, 1)) == row

    created, started, scheduled = _events(read, a, tenancy.id)
    assert [e.type for e in (created, started, scheduled)] == [
        EventType.TENANCY_CREATED,
        EventType.TENANCY_STARTED,
        EventType.TENANCY_RENT_SCHEDULED,
    ]
    assert [e.effective_date for e in (created, started, scheduled)] == [
        date(2024, 6, 10),
        date(2024, 7, 1),
        date(2024, 7, 1),
    ]
    for e in (created, started, scheduled):
        assert (e.property_id, e.unit_id) == (prop.id, unit.id)
        assert e.correlation_id == ctx.correlation_id
    assert created.payload == {
        "tenant_ids": [str(ben), str(anna)],
        "rent_model": "staffel",
        "fixed_term_until": None,
        "deposit_amount": "2850.00",
    }
    assert DEPOSIT_IBAN not in str(created.payload)
    assert scheduled.payload == {
        "rent_schedule_id": str(row.id),
        "effective_from": "2024-07-01",
        "cold_rent": "950.00",
        "operating_costs_prepayment": "220.00",
        "reason": "initial",
        "supersedes_id": None,
    }


def test_a_past_tenancy_without_rent_records_its_move_out(run, read, flat):
    a, _, unit, tenants = flat
    lease = _lease(
        unit,
        tenants,
        start_date=date(2023, 1, 1),
        signed_on=None,
        end_date=date(2023, 12, 31),
        deposit_amount=Decimal("0"),
        deposit_form=DepositForm.NONE,
        deposit_iban=None,
    )
    tenancy = _create(run, a, lease, initial_rent=None)
    assert read(a, list_rent_schedule, tenancy.id) == []
    events = _events(read, a, tenancy.id)
    assert [(e.type, e.effective_date) for e in events] == [
        (EventType.TENANCY_CREATED, None),
        (EventType.TENANCY_STARTED, date(2023, 1, 1)),
        (EventType.TENANCY_ENDED, date(2023, 12, 31)),
    ]
    # Inclusive dates: a lease from the move-out day overlaps; the day after does not.
    with pytest.raises(TenancyOverlapError):
        _create(run, a, _lease(unit, tenants, start_date=date(2023, 12, 31), signed_on=None))
    _create(run, a, _lease(unit, tenants, start_date=date(2024, 1, 1), signed_on=None))


@pytest.mark.parametrize("roles", [["co_tenant", "co_tenant"], ["primary", "primary"]])
def test_parties_need_exactly_one_primary(app_engine, run, flat, roles):
    a, _, unit, tenants = flat
    parties = [TenancyParty(tenant_id=t, role=r) for t, r in zip(tenants, roles, strict=True)]
    with pytest.raises(RuleViolationError) as raised:
        _create(run, a, _lease(unit, tenants, parties=parties))
    assert raised.value.code == EXACTLY_ONE_PRIMARY
    assert _tenancy_count(app_engine, a, unit.id) == 0


def test_create_rejects_bad_input_and_writes_nothing(app_engine, run, flat, fresh_workspaces):
    a, b = fresh_workspaces
    _, _, unit, (anna, ben) = flat
    foreign = run(b, create_tenant, TenantCreate(full_name="Eve"), iban_key=TEST_IBAN_KEY).id
    run(a, erase_tenant, ben)

    with pytest.raises(RuleViolationError) as raised:
        _create(
            run,
            a,
            _lease(unit, [anna]),
            RENT.model_copy(update={"effective_from": date(2024, 6, 30)}),
        )
    assert raised.value.code == RENT_OUTSIDE_TENANCY
    with pytest.raises(RuleError) as raised:
        _create(run, a, _lease(unit, [anna], deposit_iban="DE89 3704 0044 0532 0130 0"))
    assert raised.value.code == BAD_LENGTH
    with pytest.raises(ValueError, match="only once"):
        parties = [TenancyParty(tenant_id=anna, role=r) for r in TenancyRole]
        _create(run, a, _lease(unit, [anna], parties=parties))
    with pytest.raises(NotFoundError, match="tenant"):
        _create(run, a, _lease(unit, [foreign]))
    with pytest.raises(ConflictError, match="erased"):
        _create(run, a, _lease(unit, [ben]))
    with pytest.raises(NotFoundError, match="unit"):
        _create(run, a, _lease(unit, [anna], unit_id=uuid.uuid4()))
    assert _tenancy_count(app_engine, a, unit.id) == 0


def test_an_overlapping_tenancy_is_refused_and_writes_nothing(app_engine, run, read, flat):
    a, prop, unit, tenants = flat
    _create(run, a, _lease(unit, tenants))
    before = len(read(a, list_events, property_id=prop.id))

    with pytest.raises(TenancyOverlapError, match="overlaps"):
        _create(run, a, _lease(unit, tenants, start_date=date(2030, 1, 1), signed_on=None))

    assert _tenancy_count(app_engine, a, unit.id) == 1
    assert len(read(a, list_events, property_id=prop.id)) == before


def test_failure_after_create_leaves_nothing(app_engine, read, flat):
    a, prop, unit, tenants = flat

    class Boom(Exception):
        pass

    with pytest.raises(Boom), workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        tenancy = create_tenancy(
            ctx, _lease(unit, tenants), initial_rent=RENT, iban_key=TEST_IBAN_KEY
        )
        raise Boom

    assert read(a, get_tenancy, tenancy.id) is None
    assert read(a, list_rent_schedule, tenancy.id) == []
    assert _events(read, a, tenancy.id) == []
    assert _tenancy_count(app_engine, a, unit.id) == 0


def test_tenancy_is_invisible_from_another_workspace(run, read, flat, fresh_workspaces):
    a, _, unit, tenants = flat
    _, b = fresh_workspaces
    tenancy = _create(run, a, _lease(unit, tenants))
    assert read(b, get_tenancy, tenancy.id) is None
    assert read(b, list_rent_schedule, tenancy.id) == []
    with pytest.raises(NotFoundError, match="tenancy"):
        run(
            b,
            schedule_rent,
            tenancy.id,
            effective_from=date(2025, 7, 1),
            cold_rent=Decimal("1.00"),
            reason=RentChangeReason.AGREEMENT,
        )
    with pytest.raises(NotFoundError, match="tenancy"):
        run(b, correct_move_in, tenancy.id, date(2024, 7, 2))
    with pytest.raises(NotFoundError, match="unit"):
        _create(run, b, _lease(unit, tenants, deposit_iban=None))
    assert read(a, get_tenancy, tenancy.id) == tenancy


# --- rent schedule -------------------------------------------------------------


def _step(run, ws, tenancy_id, day, cold, reason=RentChangeReason.STAFFEL_STEP):
    return run(
        ws,
        schedule_rent,
        tenancy_id,
        effective_from=day,
        cold_rent=Decimal(cold),
        operating_costs_prepayment=Decimal("220.00"),
        reason=reason,
        now=LATER,
    )


def test_schedule_rent_appends_a_dated_row(run, read, flat):
    a, prop, unit, tenants = flat
    tenancy = _create(run, a, _lease(unit, tenants))

    step = _step(run, a, tenancy.id, date(2025, 7, 1), "1000.00")

    assert read(a, rent_on, tenancy.id, date(2025, 6, 30)).cold_rent == Decimal("950.00")
    assert read(a, rent_on, tenancy.id, date(2025, 7, 1)) == step
    event = _events(read, a, tenancy.id)[-1]
    assert (event.type, event.effective_date) == (
        EventType.TENANCY_RENT_SCHEDULED,
        date(2025, 7, 1),
    )
    assert (event.property_id, event.unit_id) == (prop.id, unit.id)
    assert event.payload["reason"] == "staffel_step"

    with pytest.raises(RuleViolationError) as raised:
        _step(run, a, tenancy.id, date(2024, 6, 30), "900.00")
    assert raised.value.code == RENT_OUTSIDE_TENANCY
    with pytest.raises(ValueError, match="correct_rent_row"):
        _step(run, a, tenancy.id, date(2025, 8, 1), "1.00", reason=RentChangeReason.CORRECTION)
    with pytest.raises(ValidationError):
        _step(run, a, tenancy.id, date(2025, 8, 1), "-1.00")
    assert len(read(a, list_rent_schedule, tenancy.id)) == 2


def test_correct_rent_row_supersedes_and_corrects_its_event(run, read, flat):
    a, _, unit, tenants = flat
    tenancy = _create(run, a, _lease(unit, tenants))
    wrong = _step(run, a, tenancy.id, date(2025, 7, 1), "1100.00")
    wrong_event = _events(read, a, tenancy.id)[-1]

    fixed = run(a, correct_rent_row, wrong.id, cold_rent=Decimal("1000.00"), now=LATER)

    assert (fixed.reason, fixed.supersedes_id) == (RentChangeReason.CORRECTION, wrong.id)
    assert (fixed.effective_from, fixed.operating_costs_prepayment) == (
        date(2025, 7, 1),
        Decimal("220.00"),
    )
    assert read(a, rent_on, tenancy.id, date(2026, 1, 1)) == fixed
    assert len(read(a, list_rent_schedule, tenancy.id)) == 3  # the wrong row stays
    event = _events(read, a, tenancy.id)[-1]
    assert event.corrects_event_id == wrong_event.id
    assert event.payload["rent_schedule_id"] == str(fixed.id)
    assert event.payload["supersedes_id"] == str(wrong.id)

    # The superseded row cannot be corrected again; its correction can, by date too.
    with pytest.raises(ConflictError, match=str(fixed.id)):
        run(a, correct_rent_row, wrong.id, cold_rent=Decimal("999.00"))
    again = run(a, correct_rent_row, fixed.id, effective_from=date(2025, 8, 1), now=LATER)
    assert _events(read, a, tenancy.id)[-1].corrects_event_id == event.id
    assert read(a, rent_on, tenancy.id, date(2025, 7, 15)).cold_rent == Decimal("950.00")
    assert read(a, rent_on, tenancy.id, date(2025, 8, 1)) == again

    # A no-op writes nothing; a date outside the tenancy is refused.
    assert run(a, correct_rent_row, again.id, cold_rent=Decimal("1000")) == again
    with pytest.raises(RuleViolationError):
        run(a, correct_rent_row, again.id, effective_from=date(2024, 6, 1))
    with pytest.raises(NotFoundError, match="rent_schedule"):
        run(a, correct_rent_row, uuid.uuid4(), cold_rent=Decimal("1.00"))
    assert len(read(a, list_rent_schedule, tenancy.id)) == 4


# --- move-in, deposit, notice, move-out ----------------------------------------


def test_correct_move_in_appends_a_correcting_event(run, read, flat):
    a, prop, unit, tenants = flat
    tenancy = _create(
        run, a, _lease(unit, tenants, start_date=date(2024, 7, 15)), initial_rent=None
    )
    original = _events(read, a, tenancy.id)[1]

    fixed = run(a, correct_move_in, tenancy.id, date(2024, 7, 1), now=LATER)

    assert fixed.start_date == date(2024, 7, 1) and fixed.updated_at == LATER
    correction = _events(read, a, tenancy.id)[-1]
    assert correction.type is EventType.TENANCY_STARTED
    assert correction.corrects_event_id == original.id
    assert correction.effective_date == date(2024, 7, 1)
    assert (correction.property_id, correction.unit_id) == (prop.id, unit.id)

    assert run(a, correct_move_in, tenancy.id, date(2024, 7, 1)) == fixed  # no-op
    _step(run, a, tenancy.id, date(2024, 7, 1), "950.00", RentChangeReason.INITIAL)
    with pytest.raises(RuleViolationError) as raised:
        run(a, correct_move_in, tenancy.id, date(2024, 7, 2))
    assert raised.value.code == RENT_OUTSIDE_TENANCY
    with pytest.raises(ValidationError, match="signed_on"):
        run(a, correct_move_in, tenancy.id, date(2024, 6, 1))
    assert read(a, get_tenancy, tenancy.id).start_date == date(2024, 7, 1)
    assert len(_events(read, a, tenancy.id)) == 4


def test_correct_move_in_cannot_overlap_the_previous_tenancy(run, read, flat):
    a, _, unit, tenants = flat
    _create(
        run,
        a,
        _lease(
            unit, tenants, start_date=date(2023, 1, 1), end_date=date(2024, 6, 30), signed_on=None
        ),
        initial_rent=None,
    )
    tenancy = _create(run, a, _lease(unit, tenants, signed_on=None), initial_rent=None)
    with pytest.raises(TenancyOverlapError):
        run(a, correct_move_in, tenancy.id, date(2024, 6, 30))
    assert read(a, get_tenancy, tenancy.id).start_date == date(2024, 7, 1)
    assert len(_events(read, a, tenancy.id)) == 2


def test_correct_move_in_cannot_pass_the_notice_end(run, read, flat):
    a, _, unit, tenants = flat
    tenancy = _create(run, a, _lease(unit, tenants), initial_rent=None)
    run(
        a,
        give_notice,
        tenancy.id,
        given_by="tenant",
        given_on=date(2026, 3, 31),
        ends_on=date(2026, 6, 30),
        now=LATER,
    )
    before = _events(read, a, tenancy.id)

    with pytest.raises(ValueError, match="2026-06-30"):
        run(a, correct_move_in, tenancy.id, date(2026, 7, 15), now=LATER)

    assert read(a, get_tenancy, tenancy.id).start_date == date(2024, 7, 1)
    assert _events(read, a, tenancy.id) == before
    # A correction that stays before the notice's end is still accepted.
    assert run(a, correct_move_in, tenancy.id, date(2024, 7, 2)).start_date == date(2024, 7, 2)


def test_deposit_notice_and_move_out(run, read, flat):
    a, prop, unit, tenants = flat
    tenancy = _create(run, a, _lease(unit, tenants))
    _step(run, a, tenancy.id, date(2025, 7, 1), "1000.00")

    deposit = run(
        a,
        record_deposit_received,
        tenancy.id,
        amount=Decimal("2850.00"),
        received_on=date(2024, 7, 1),
        now=LATER,
    )
    assert (deposit.type, deposit.effective_date, deposit.payload) == (
        EventType.TENANCY_DEPOSIT_RECEIVED,
        date(2024, 7, 1),
        {"amount": "2850.00"},
    )
    assert (deposit.property_id, deposit.unit_id) == (prop.id, unit.id)
    with pytest.raises(ValidationError):
        run(
            a,
            record_deposit_received,
            tenancy.id,
            amount=Decimal("0"),
            received_on=date(2024, 7, 1),
        )

    noticed = run(
        a,
        give_notice,
        tenancy.id,
        given_by="tenant",
        given_on=date(2026, 3, 31),
        ends_on=date(2026, 6, 30),
        now=LATER,
    )
    assert noticed.notice_given_on == date(2026, 3, 31)
    notice = _events(read, a, tenancy.id)[-1]
    assert (notice.type, notice.effective_date, notice.payload) == (
        EventType.TENANCY_NOTICE_GIVEN,
        date(2026, 3, 31),
        {"given_by": "tenant", "ends_on": "2026-06-30"},
    )
    with pytest.raises(ConflictError, match="already given"):
        run(
            a,
            give_notice,
            tenancy.id,
            given_by="landlord",
            given_on=date(2026, 4, 1),
            ends_on=date(2026, 7, 31),
        )

    with pytest.raises(RuleViolationError) as raised:
        run(a, end_tenancy, tenancy.id, date(2025, 6, 30))  # the 2025-07-01 step is in force
    assert raised.value.code == RENT_OUTSIDE_TENANCY
    ended = run(a, end_tenancy, tenancy.id, date(2026, 6, 30), now=LATER)
    assert ended.end_date == date(2026, 6, 30)
    moved_out = _events(read, a, tenancy.id)[-1]
    assert (moved_out.type, moved_out.effective_date) == (
        EventType.TENANCY_ENDED,
        date(2026, 6, 30),
    )
    with pytest.raises(ConflictError, match="already ended"):
        run(a, end_tenancy, tenancy.id, date(2026, 7, 31))
    with pytest.raises(RuleViolationError):
        _step(run, a, tenancy.id, date(2026, 7, 1), "1050.00")
    assert read(a, get_tenancy, tenancy.id) == ended
