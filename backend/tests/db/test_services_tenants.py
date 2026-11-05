"""Tenant commands end to end, as hausbuch_app under RLS. Personal data stays out of events."""

import uuid
from datetime import UTC, datetime

import pytest
from conftest import TEST_IBAN_KEY
from sqlalchemy import text

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import BAD_CHECKSUM, iban_hmac
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.events import EventType
from hausbuch.schemas.tenancy import TenantCreate
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import ConflictError, NotFoundError
from hausbuch.services.events import list_events
from hausbuch.services.tenants import create_tenant, erase_tenant, get_tenant, update_tenant

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


def test_create_stores_mask_and_hmac_never_the_plaintext(app_engine, workspaces, read):
    a, _ = workspaces
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


def test_a_tenant_without_an_iban(run, read, workspaces):
    a, _ = workspaces
    bert = _create(run, a, TenantCreate(full_name="Bert Beispiel"))
    assert (bert.iban_masked, bert.email) == (None, None)


def test_a_bad_iban_raises_its_rule_code_and_writes_nothing(run, read, app_engine, workspaces):
    a, _ = workspaces
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


def test_update_lists_changed_fields_never_values(run, read, app_engine, workspaces):
    a, _ = workspaces
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


def test_invalid_update_hides_the_value_and_writes_nothing(run, read, workspaces):
    a, _ = workspaces
    anna = _create(run, a)
    with pytest.raises(ValueError, match="full_name") as raised:
        run(a, update_tenant, anna.id, full_name="")
    assert raised.value.__cause__ is None
    with pytest.raises(ValueError, match="iban_key"):
        run(a, update_tenant, anna.id, iban=IBAN)
    assert read(a, get_tenant, anna.id) == anna
    assert len(_events(read, a, anna.id)) == 1


def test_erase_nulls_personal_data_and_keeps_the_events(run, read, app_engine, workspaces):
    a, _ = workspaces
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


def test_tenant_is_invisible_from_another_workspace(run, read, workspaces):
    a, b = workspaces
    anna = _create(run, a)
    assert read(b, get_tenant, anna.id) is None
    with pytest.raises(NotFoundError, match="tenant"):
        run(b, erase_tenant, anna.id)
    with pytest.raises(NotFoundError, match="tenant"):
        run(b, update_tenant, anna.id, email="mallory@example.org")
    with pytest.raises(NotFoundError):
        run(a, erase_tenant, uuid.uuid4())
    assert read(a, get_tenant, anna.id) == anna
