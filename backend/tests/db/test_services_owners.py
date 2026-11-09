"""The owner slice end to end: services under hausbuch_app, RLS and one transaction per command."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.enums import ActorType, EntityType, StateCode
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import NotFoundError
from hausbuch.services.events import list_events
from hausbuch.services.owners import create_owner, get_owner, update_owner

CREATED_AT = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
UPDATED_AT = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
ERIKA = OwnerCreate(full_name="Erika Muster", church_tax=True, tax_state=StateCode.BY)


def _create(engine, ws, data=ERIKA):
    with workspace_transaction(engine, ws) as conn:
        return create_owner(new_command(conn, ws, ACTOR, now=CREATED_AT), data)


def _update(engine, ws, owner_id, **fields):
    with workspace_transaction(engine, ws) as conn:
        return update_owner(new_command(conn, ws, ACTOR, now=UPDATED_AT), owner_id, **fields)


def _events(engine, ws, owner_id):
    with workspace_transaction(engine, ws) as conn:
        return list_events(conn, entity_type=EntityType.OWNER, entity_id=owner_id)


# --- create ------------------------------------------------------------------


def test_create_writes_the_row_and_exactly_one_event(app_engine, workspaces):
    a, _ = workspaces
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        owner = create_owner(ctx, ERIKA)

    assert owner.workspace_id == a
    assert (owner.full_name, owner.tax_id_ref) == ("Erika Muster", None)
    assert (owner.church_tax, owner.tax_state) == (True, StateCode.BY)
    assert owner.created_at == owner.updated_at == CREATED_AT

    with workspace_transaction(app_engine, a) as conn:
        assert get_owner(conn, owner.id) == owner
    [created] = _events(app_engine, a, owner.id)
    assert created.type is EventType.OWNER_CREATED
    assert created.payload == {"church_tax": True, "tax_state": "BY"}
    assert created.correlation_id == ctx.correlation_id
    assert created.recorded_at == CREATED_AT
    assert (created.actor_type, created.actor_id) == (ActorType.USER, "user:test")
    assert created.property_id is None and created.effective_date is None


def test_owner_is_invisible_from_another_workspace(app_engine, workspaces):
    a, b = workspaces
    owner = _create(app_engine, a)
    with workspace_transaction(app_engine, b) as conn:
        assert get_owner(conn, owner.id) is None
        assert list_events(conn, entity_id=owner.id) == []
    with pytest.raises(NotFoundError):
        _update(app_engine, b, owner.id, full_name="Mallory")
    with workspace_transaction(app_engine, a) as conn:
        assert get_owner(conn, owner.id).full_name == "Erika Muster"


def test_failure_after_create_leaves_neither_row_nor_event(app_engine, workspaces):
    a, _ = workspaces

    class Boom(Exception):
        pass

    with pytest.raises(Boom), workspace_transaction(app_engine, a) as conn:
        owner = create_owner(new_command(conn, a, ACTOR, now=CREATED_AT), ERIKA)
        assert get_owner(conn, owner.id) is not None
        assert len(list_events(conn, entity_id=owner.id)) == 1
        raise Boom

    with workspace_transaction(app_engine, a) as conn:
        assert get_owner(conn, owner.id) is None
        assert list_events(conn, entity_id=owner.id) == []


def test_each_command_gets_its_own_correlation_id(app_engine, workspaces):
    a, _ = workspaces
    first, second = _create(app_engine, a), _create(app_engine, a)
    [e1], [e2] = _events(app_engine, a, first.id), _events(app_engine, a, second.id)
    assert e1.correlation_id != e2.correlation_id


# --- update ------------------------------------------------------------------


def test_update_records_exactly_the_changed_fields(app_engine, workspaces):
    a, _ = workspaces
    owner = _create(app_engine, a)

    updated = _update(
        app_engine,
        a,
        owner.id,
        full_name="Erika Muster",  # passed but unchanged: not in the event
        tax_id_ref="secrets:owner/erika/steuer-id",
        tax_state=StateCode.NW,
    )

    assert updated.full_name == "Erika Muster"
    assert updated.tax_id_ref == "secrets:owner/erika/steuer-id"
    assert updated.tax_state is StateCode.NW
    assert updated.created_at == CREATED_AT and updated.updated_at == UPDATED_AT

    created, changed = _events(app_engine, a, owner.id)
    assert created.type is EventType.OWNER_CREATED
    assert changed.type is EventType.OWNER_UPDATED
    assert changed.recorded_at == UPDATED_AT
    assert changed.seq > created.seq
    assert changed.payload == {
        "changes": {
            "tax_id_ref": {"old": None, "new": "secrets:owner/erika/steuer-id"},
            "tax_state": {"old": "BY", "new": "NW"},
        },
        "reason": None,
    }
    with workspace_transaction(app_engine, a) as conn:
        assert get_owner(conn, owner.id) == updated


def test_none_clears_a_field(app_engine, workspaces):
    a, _ = workspaces
    owner = _create(app_engine, a)

    updated = _update(app_engine, a, owner.id, church_tax=False, tax_state=None)

    assert (updated.church_tax, updated.tax_state) == (False, None)
    assert _events(app_engine, a, owner.id)[-1].payload["changes"] == {
        "church_tax": {"old": True, "new": False},
        "tax_state": {"old": "BY", "new": None},
    }


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"full_name": "Erika Muster"},
        {"tax_id_ref": None},
        {"tax_state": StateCode.BY},
        {"church_tax": True, "full_name": "Erika Muster"},
    ],
    ids=["nothing-passed", "same-name", "same-none", "same-state", "same-church-and-name"],
)
def test_noop_update_writes_nothing(app_engine, workspaces, fields):
    a, _ = workspaces
    owner = _create(app_engine, a)

    result = _update(app_engine, a, owner.id, **fields)

    assert result == owner
    assert result.updated_at == CREATED_AT
    with workspace_transaction(app_engine, a) as conn:
        assert get_owner(conn, owner.id) == owner
    assert [e.type for e in _events(app_engine, a, owner.id)] == [EventType.OWNER_CREATED]


@pytest.mark.parametrize(
    "fields",
    [{"full_name": ""}, {"full_name": None}, {"tax_state": None}, {"tax_state": "XX"}],
    ids=["empty-name", "null-name", "church-without-state", "unknown-state"],
)
def test_invalid_update_is_rejected_and_writes_nothing(app_engine, workspaces, fields):
    a, _ = workspaces
    owner = _create(app_engine, a)

    with pytest.raises(ValidationError):
        _update(app_engine, a, owner.id, **fields)

    with workspace_transaction(app_engine, a) as conn:
        assert get_owner(conn, owner.id) == owner
    assert len(_events(app_engine, a, owner.id)) == 1


def test_update_of_unknown_owner_raises(app_engine, workspaces):
    a, _ = workspaces
    with pytest.raises(NotFoundError, match="owner"):
        _update(app_engine, a, uuid.uuid4(), full_name="Nobody")
