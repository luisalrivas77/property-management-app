"""Tenant commands end to end, as hausbuch_app under RLS. Personal data stays out of events."""

import threading
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import text

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import BAD_CHECKSUM, iban_hmac
from hausbuch.schemas.document import RetentionPolicy
from hausbuch.schemas.enums import (
    ActorType,
    PropertyKind,
    RentModel,
    RetentionHold,
    TenancyRole,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.schemas.tenancy import TenancyCreate, TenancyParty, TenantCreate
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import ConflictError, NotFoundError, RetentionHoldError
from hausbuch.services.events import list_events
from hausbuch.services.parties import tenancies_ever_of
from hausbuch.services.properties import create_property
from hausbuch.services.tenancies import create_tenancy, end_tenancy, set_parties
from hausbuch.services.tenants import create_tenant, erase_tenant, get_tenant, update_tenant
from hausbuch.services.units import create_unit

CREATED_AT = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
IBAN = "DE89370400440532013000"
OTHER_IBAN = "AT611904300234573201"
ANNA = TenantCreate(
    full_name="Anna Beispiel",
    email="anna@example.org",
    phone="+49 30 1234567",
    iban="de89 3704 0044 0532 0130 00",
)


def _create(run, ws, data=ANNA):
    return run(ws, create_tenant, data, iban_key=TEST_IBAN_KEY, now=CREATED_AT)


def _events(read, ws, tenant_id):
    return read(ws, list_events, entity_id=tenant_id)


def _stored(app_engine, ws, tenant_id):
    """The raw row and every event about it, as text, to search for leaked data."""
    with workspace_transaction(app_engine, ws) as conn:
        row = conn.execute(
            text("SELECT t::text, t.iban_hmac FROM tenant t WHERE id = :id"), {"id": tenant_id}
        ).one()
        events = conn.execute(
            text("SELECT e::text FROM event e WHERE entity_id = :id"), {"id": tenant_id}
        ).scalars()
        return row[0], row[1], " ".join(events)


def test_create_stores_mask_and_hmac_never_the_plaintext(app_engine, fresh_workspaces, read):
    a, _ = fresh_workspaces
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        anna = create_tenant(ctx, ANNA, iban_key=TEST_IBAN_KEY)

    assert (anna.full_name, anna.email, anna.phone) == (
        "Anna Beispiel",
        "anna@example.org",
        "+49 30 1234567",
    )
    assert anna.iban_masked == "DE89 **** 3000" and anna.erased_at is None
    assert read(a, get_tenant, anna.id) == anna
    assert "iban" not in anna.model_dump() and "iban_hmac" not in anna.model_dump()

    row, digest, events = _stored(app_engine, a, anna.id)
    assert digest == iban_hmac(IBAN, TEST_IBAN_KEY)
    for leak in (IBAN, "0532013000", "3704 0044"):
        assert leak not in row and leak not in events
    for personal in ("Anna", "anna@example.org", "1234567"):
        assert personal not in events

    [created] = _events(read, a, anna.id)
    assert created.type is EventType.TENANT_CREATED
    assert created.payload == {} and created.correlation_id == ctx.correlation_id
    assert (created.property_id, created.unit_id, created.effective_date) == (None, None, None)


def test_a_tenant_without_an_iban(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    bert = _create(run, a, TenantCreate(full_name="Bert Beispiel"))
    assert (bert.iban_masked, bert.email) == (None, None)


def test_a_bad_iban_raises_its_rule_code_and_writes_nothing(
    run, read, app_engine, fresh_workspaces
):
    a, _ = fresh_workspaces
    bad = "DE89370400440532013001"
    with workspace_transaction(app_engine, a) as conn:
        before = conn.execute(text("SELECT count(*) FROM tenant")).scalar_one()

    with pytest.raises(RuleError) as raised:
        _create(run, a, ANNA.model_copy(update={"iban": bad}))
    assert raised.value.code == BAD_CHECKSUM
    assert bad not in str(raised.value)

    anna = _create(run, a)
    with pytest.raises(RuleError) as raised:
        run(a, update_tenant, anna.id, iban=bad, iban_key=TEST_IBAN_KEY)
    assert raised.value.code == BAD_CHECKSUM
    with workspace_transaction(app_engine, a) as conn:
        assert conn.execute(text("SELECT count(*) FROM tenant")).scalar_one() == before + 1
    assert read(a, get_tenant, anna.id) == anna


def test_update_lists_changed_fields_never_values(run, read, app_engine, fresh_workspaces):
    a, _ = fresh_workspaces
    anna = _create(run, a)

    updated = run(
        a,
        update_tenant,
        anna.id,
        full_name="Anna Beispiel",  # unchanged
        email="anna.b@example.org",
        iban=OTHER_IBAN,
        iban_key=TEST_IBAN_KEY,
        now=LATER,
    )

    assert updated.email == "anna.b@example.org"
    assert updated.iban_masked == "AT61 **** 3201" and updated.updated_at == LATER
    changed = _events(read, a, anna.id)[-1]
    assert changed.type is EventType.TENANT_UPDATED
    assert changed.payload == {"changed_fields": ["email", "iban"]}
    _, digest, events = _stored(app_engine, a, anna.id)
    assert digest == iban_hmac(OTHER_IBAN, TEST_IBAN_KEY)
    assert "anna.b@example.org" not in events and OTHER_IBAN not in events

    # The same IBAN in another spelling is no change; None clears it.
    spelled = "at61 1904 3002 3457 3201"
    assert run(a, update_tenant, anna.id, iban=spelled, iban_key=TEST_IBAN_KEY) == updated
    cleared = run(a, update_tenant, anna.id, iban=None, phone=None, now=LATER)
    assert (cleared.iban_masked, cleared.phone) == (None, None)
    assert _events(read, a, anna.id)[-1].payload == {"changed_fields": ["phone", "iban"]}
    assert len(_events(read, a, anna.id)) == 3


def test_invalid_update_hides_the_value_and_writes_nothing(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    anna = _create(run, a)
    with pytest.raises(ValueError, match="full_name") as raised:
        run(a, update_tenant, anna.id, full_name="")
    assert raised.value.__cause__ is None
    with pytest.raises(ValueError, match="iban_key"):
        run(a, update_tenant, anna.id, iban=IBAN)
    assert read(a, get_tenant, anna.id) == anna
    assert len(_events(read, a, anna.id)) == 1


def test_erase_nulls_personal_data_and_keeps_the_events(run, read, app_engine, fresh_workspaces):
    a, _ = fresh_workspaces
    anna = _create(run, a)
    run(a, update_tenant, anna.id, email="new@example.org", now=LATER)

    erased = run(a, erase_tenant, anna.id, now=LATER)

    assert erased.erased_at == LATER
    assert (erased.full_name, erased.email, erased.phone, erased.iban_masked) == (None,) * 4
    _, digest, _ = _stored(app_engine, a, anna.id)
    assert digest is None
    assert [e.type for e in _events(read, a, anna.id)] == [
        EventType.TENANT_CREATED,
        EventType.TENANT_UPDATED,
        EventType.TENANT_ERASED,
    ]
    assert run(a, erase_tenant, anna.id) == erased  # idempotent
    with pytest.raises(ConflictError, match="erased"):
        run(a, update_tenant, anna.id, full_name="Anna")
    assert len(_events(read, a, anna.id)) == 3


def test_tenant_is_invisible_from_another_workspace(run, read, fresh_workspaces):
    a, b = fresh_workspaces
    anna = _create(run, a)
    assert read(b, get_tenant, anna.id) is None
    with pytest.raises(NotFoundError, match="tenant"):
        run(b, erase_tenant, anna.id)
    with pytest.raises(NotFoundError, match="tenant"):
        run(b, update_tenant, anna.id, email="mallory@example.org")
    with pytest.raises(NotFoundError):
        run(a, erase_tenant, uuid.uuid4())
    assert read(a, get_tenant, anna.id) == anna


# --- erasure respects retention holds ------------------------------------------


def _unit(run, ws):
    prop = run(
        ws,
        create_property,
        PropertyCreate(
            label="Wohnung",
            kind=PropertyKind.ETW,
            address=Address(
                street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"
            ),
            build_year=1994,
            notarized_on=date(2015, 3, 15),
            purchase_date=date(2015, 5, 1),
            purchase_price=Decimal("289000.00"),
        ),
        now=CREATED_AT,
    )
    unit = UnitCreate(property_id=prop.id, label="WE 3", floor_area_m2=Decimal("68.40"))
    return run(ws, create_unit, unit, now=CREATED_AT).id


def _lease(unit_id, tenant_id, start, end=None):
    return TenancyCreate(
        unit_id=unit_id,
        parties=[TenancyParty(tenant_id=tenant_id, role="primary")],
        start_date=start,
        end_date=end,
        rent_model=RentModel.FIXED,
    )


def _let(run, ws, unit_id, tenant_id, start, end=None):
    lease = _lease(unit_id, tenant_id, start, end)
    return run(ws, create_tenancy, lease, initial_rent=None, iban_key=TEST_IBAN_KEY, now=CREATED_AT)


def _assert_untouched(read, ws, tenant):
    assert read(ws, get_tenant, tenant.id) == tenant
    assert [e.type for e in _events(read, ws, tenant.id)] == [EventType.TENANT_CREATED]


def test_a_tenant_with_a_running_tenancy_cannot_be_erased(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    anna = _create(run, a)
    unit_id = _unit(run, a)
    _let(run, a, unit_id, anna.id, date(2016, 1, 1), date(2019, 12, 31))  # an earlier lease
    _let(run, a, unit_id, anna.id, date(2024, 7, 1))  # still running

    with pytest.raises(RetentionHoldError, match="running tenancy") as raised:
        run(a, erase_tenant, anna.id, now=LATER)

    assert raised.value.result.hold is RetentionHold.TENANCY_ACTIVE
    assert isinstance(raised.value, ConflictError)
    _assert_untouched(read, a, anna)


def test_erasure_waits_for_the_retention_period_after_move_out(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    unit_id = _unit(run, a)
    recent, old = _create(run, a), _create(run, a)
    _let(run, a, unit_id, recent.id, date(2023, 1, 1), date(2024, 12, 31))
    _let(run, a, unit_id, old.id, date(2016, 1, 1), date(2020, 6, 30))

    # Moved out 2024: retained until 31 Dec 2028 (4 years, D11).
    with pytest.raises(RetentionHoldError, match="2028-12-31") as raised:
        run(a, erase_tenant, recent.id, now=LATER)
    assert raised.value.result.retain_until == date(2028, 12, 31)
    _assert_untouched(read, a, recent)

    # Moved out 2020: retained until 31 Dec 2024, inclusive; a longer policy holds it still.
    on_last_day = datetime(2024, 12, 31, 12, 0, tzinfo=UTC)
    with pytest.raises(RetentionHoldError, match="2024-12-31"):
        run(a, erase_tenant, old.id, now=on_last_day)
    with pytest.raises(RetentionHoldError, match="2030-12-31"):
        run(a, erase_tenant, old.id, policy=RetentionPolicy(tenant_contact_years=10), now=LATER)
    _assert_untouched(read, a, old)

    erased = run(a, erase_tenant, old.id, now=LATER)
    assert erased.erased_at == LATER and erased.full_name is None
    assert _events(read, a, old.id)[-1].type is EventType.TENANT_ERASED


def test_on_request_erasure_inside_the_claims_period_records_its_basis(run, read, fresh_workspaces):
    """Luis, 5 Nov 2026: after move-out a tenant's request may be honoured inside the 4-year
    default (keeping data for claims is allowed, not required); the event records why."""
    a, _ = fresh_workspaces
    unit_id = _unit(run, a)
    moved_out = _create(run, a)
    _let(run, a, unit_id, moved_out.id, date(2023, 1, 1), date(2024, 12, 31))

    with pytest.raises(RetentionHoldError):  # without the request, the default still holds
        run(a, erase_tenant, moved_out.id, now=LATER)
    erased = run(a, erase_tenant, moved_out.id, on_request=True, now=LATER)

    assert erased.erased_at == LATER and erased.full_name is None
    event = _events(read, a, moved_out.id)[-1]
    assert event.type is EventType.TENANT_ERASED
    assert event.payload == {"basis": "data_subject_request"}


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2024, 7, 1), None),  # open-ended
        (date(2024, 7, 1), date(2027, 6, 30)),  # end already recorded, still in the future
        (date(2027, 1, 1), date(2027, 12, 31)),  # signed, not yet started
        (date(2024, 7, 1), LATER.date()),  # moving out today: the end date still runs
    ],
    ids=["open-ended", "future-end", "not-yet-started", "ends-today"],
)
def test_a_request_cannot_erase_a_tenant_whose_tenancy_runs(
    run, read, fresh_workspaces, start, end
):
    """While a lease runs or is still to come, the data performs the contract: no right to
    erasure yet. A recorded future end date does not make the tenancy over (review of the
    first version: it let a request erase a tenant who still lived in the flat)."""
    a, _ = fresh_workspaces
    anna = _create(run, a)
    _let(run, a, _unit(run, a), anna.id, start, end)

    with pytest.raises(RetentionHoldError, match="running tenancy") as raised:
        run(a, erase_tenant, anna.id, on_request=True, now=LATER)
    assert raised.value.result.hold is RetentionHold.TENANCY_ACTIVE
    _assert_untouched(read, a, anna)


def test_a_request_erases_from_the_day_after_move_out(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    anna = _create(run, a)
    _let(run, a, _unit(run, a), anna.id, date(2024, 7, 1), date(2026, 11, 5))  # moved out

    erased = run(a, erase_tenant, anna.id, on_request=True, now=LATER)  # 6 Nov
    assert erased.full_name is None
    assert _events(read, a, anna.id)[-1].payload == {"basis": "data_subject_request"}


def test_the_recorded_basis_is_what_permitted_the_erasure(run, read, fresh_workspaces):
    """A request only becomes the basis when it was needed, i.e. inside the claims period."""
    a, _ = fresh_workspaces
    applicant, old = _create(run, a), _create(run, a)
    _let(run, a, _unit(run, a), old.id, date(2016, 1, 1), date(2020, 6, 30))  # expired 2024

    run(a, erase_tenant, applicant.id, on_request=True, now=LATER)
    run(a, erase_tenant, old.id, on_request=True, now=LATER)

    assert _events(read, a, applicant.id)[-1].payload == {"basis": "no_tenancy"}
    assert _events(read, a, old.id)[-1].payload == {"basis": "retention_expired"}


def test_erasure_basis_without_a_request(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    applicant, old = _create(run, a), _create(run, a)
    _let(run, a, _unit(run, a), old.id, date(2016, 1, 1), date(2020, 6, 30))

    run(a, erase_tenant, applicant.id, now=LATER)
    run(a, erase_tenant, old.id, now=LATER)

    assert _events(read, a, applicant.id)[-1].payload == {"basis": "no_tenancy"}
    assert _events(read, a, old.id)[-1].payload == {"basis": "retention_expired"}


def test_a_tenant_without_any_tenancy_can_be_erased(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    applicant = _create(run, a)
    assert run(a, erase_tenant, applicant.id, now=LATER).erased_at == LATER


def test_erasure_waits_for_a_tenancy_being_created(app_engine, run, read, fresh_workspaces):
    """create_tenancy holds the tenant FOR KEY SHARE, so an erasure started meanwhile waits
    for it, then sees the new tenancy and refuses."""
    a, _ = fresh_workspaces
    anna = _create(run, a)
    unit_id = _unit(run, a)
    errors: list[Exception] = []

    def erase():
        try:
            run(a, erase_tenant, anna.id, now=LATER)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        lease = _lease(unit_id, anna.id, date(2024, 7, 1))
        create_tenancy(ctx, lease, initial_rent=None, iban_key=TEST_IBAN_KEY)
        other = threading.Thread(target=erase)
        other.start()
        other.join(timeout=1)
        assert other.is_alive()  # blocked on the tenant row
    other.join(timeout=10)

    assert not other.is_alive()
    [error] = errors
    assert isinstance(error, RetentionHoldError)
    _assert_untouched(read, a, anna)


def test_a_tenancy_waits_for_an_erasure_in_flight(app_engine, run, read, fresh_workspaces):
    """The other order: an erasure locks the tenant first. create_tenancy's FOR KEY SHARE read
    waits for it, then sees erased_at, so no tenancy is left pointing at an erased tenant."""
    a, _ = fresh_workspaces
    applicant = _create(run, a)
    unit_id = _unit(run, a)
    errors: list[Exception] = []

    def let():
        try:
            _let(run, a, unit_id, applicant.id, date(2024, 7, 1))
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    with workspace_transaction(app_engine, a) as conn:
        erase_tenant(new_command(conn, a, ACTOR, now=LATER), applicant.id)
        other = threading.Thread(target=let)
        other.start()
        other.join(timeout=1)
        assert other.is_alive()  # blocked on the tenant row
    other.join(timeout=10)

    assert not other.is_alive()
    [error] = errors
    assert isinstance(error, ConflictError) and "erased" in str(error)
    with workspace_transaction(app_engine, a) as conn:
        count = conn.execute(
            text("SELECT count(*) FROM tenancy_tenant WHERE tenant_id = :t"), {"t": applicant.id}
        ).scalar_one()
    assert count == 0


PRIMARY, CO_TENANT = TenancyRole.PRIMARY, TenancyRole.CO_TENANT


def _shared(run, ws, unit_id, anna_id, ben_id, start, end=None):
    """A tenancy with Anna as the primary and Ben as a co-tenant."""
    lease = _lease(unit_id, anna_id, start, end).model_copy(
        update={
            "parties": [
                TenancyParty(tenant_id=anna_id, role=PRIMARY),
                TenancyParty(tenant_id=ben_id, role=CO_TENANT),
            ]
        }
    )
    return run(ws, create_tenancy, lease, initial_rent=None, iban_key=TEST_IBAN_KEY, now=CREATED_AT)


def _only(run, ws, tenancy_id, tenant_id):
    """Set the parties to `tenant_id` alone, removing everyone else."""
    parties = [TenancyParty(tenant_id=tenant_id, role=PRIMARY)]
    return run(ws, set_parties, tenancy_id, parties, now=CREATED_AT)


def test_a_removed_party_is_held_by_the_tenancy_they_left(run, read, fresh_workspaces):
    """F1 (decided 6 Nov 2026): removing a party is bookkeeping, not the end of the claims
    period. Retention follows every tenancy the tenant was ever a party of, so a co-tenant
    removed from a running lease is still held by it, and after move-out the usual period
    runs from its end. The basis is never no_tenancy for someone who was a party."""
    a, _ = fresh_workspaces
    anna, ben = _create(run, a), _create(run, a)
    tenancy = _shared(run, a, _unit(run, a), anna.id, ben.id, date(2024, 7, 1))
    assert [p.tenant_id for p in _only(run, a, tenancy.id, anna.id).parties] == [anna.id]

    for on_request in (False, True):
        with pytest.raises(RetentionHoldError, match="running tenancy") as raised:
            run(a, erase_tenant, ben.id, on_request=on_request, now=LATER)
        assert raised.value.result.hold is RetentionHold.TENANCY_ACTIVE
    _assert_untouched(read, a, ben)

    run(a, end_tenancy, tenancy.id, date(2026, 11, 5), now=CREATED_AT)
    with pytest.raises(RetentionHoldError, match="2030-12-31") as raised:
        run(a, erase_tenant, ben.id, now=LATER)
    assert raised.value.result.retain_until == date(2030, 12, 31)
    _assert_untouched(read, a, ben)

    erased = run(a, erase_tenant, ben.id, on_request=True, now=LATER)
    assert erased.full_name is None
    assert _events(read, a, ben.id)[-1].payload == {"basis": "data_subject_request"}


def test_a_party_who_joined_and_left_is_still_held(run, read, fresh_workspaces):
    """Ben was never in tenancy.created: he joined by set_parties and was removed again. The
    parties_set event that added him still names the tenancy."""
    a, _ = fresh_workspaces
    anna, ben = _create(run, a), _create(run, a)
    tenancy = _let(run, a, _unit(run, a), anna.id, date(2024, 7, 1))
    joined = [
        TenancyParty(tenant_id=anna.id, role=PRIMARY),
        TenancyParty(tenant_id=ben.id, role=CO_TENANT),
    ]
    run(a, set_parties, tenancy.id, joined, now=CREATED_AT)
    _only(run, a, tenancy.id, anna.id)

    with pytest.raises(RetentionHoldError, match="running tenancy") as raised:
        run(a, erase_tenant, ben.id, on_request=True, now=LATER)
    assert raised.value.result.hold is RetentionHold.TENANCY_ACTIVE
    _assert_untouched(read, a, ben)


def test_a_party_removed_long_ago_is_erased_as_expired_not_as_unlinked(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    anna, ben = _create(run, a), _create(run, a)
    tenancy = _shared(run, a, _unit(run, a), anna.id, ben.id, date(2016, 1, 1), date(2020, 6, 30))
    _only(run, a, tenancy.id, anna.id)

    run(a, erase_tenant, ben.id, on_request=True, now=LATER)
    assert _events(read, a, ben.id)[-1].payload == {"basis": "retention_expired"}


def test_the_event_log_lookup_stays_in_its_workspace(app_engine, run, fresh_workspaces):
    """tenancies_ever_of reads the event table under RLS: workspace a cannot see that b's
    events name a tenant, so b's tenancies never hold anything in a."""
    a, b = fresh_workspaces
    anna, ben = _create(run, b), _create(run, b)
    tenancy = _shared(run, b, _unit(run, b), anna.id, ben.id, date(2024, 7, 1))
    _only(run, b, tenancy.id, anna.id)

    with workspace_transaction(app_engine, b) as conn:
        assert tenancies_ever_of(conn, [ben.id]) == {tenancy.id}
    with workspace_transaction(app_engine, a) as conn:
        assert tenancies_ever_of(conn, [ben.id]) == set()
