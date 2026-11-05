"""hausbuch.api.commands.Commands (phase 1 step 3, §3.2): one workspace transaction per
command or query, as the session's user, with the request id as correlation id."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hausbuch.api.auth_context import AuthContext
from hausbuch.api.commands import Commands
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services.events import list_events
from hausbuch.services.owners import create_owner, get_owner, list_owners


def _commands(engine, ws, request_id=None) -> Commands:
    auth = AuthContext(workspace_id=ws, user_id=uuid4(), session_id=uuid4(), passkey_id=None)
    return Commands(engine, auth, request_id or uuid4())


def test_run_commits_as_the_user_with_the_request_id(app_engine, fresh_workspaces):
    a, b = fresh_workspaces
    request_id = uuid4()
    cmd = _commands(app_engine, a, request_id)
    before = datetime.now(UTC)
    owner = cmd.run(create_owner, OwnerCreate(full_name="Luis Rivas"))

    assert owner.workspace_id == a
    assert before <= owner.created_at <= datetime.now(UTC)
    [event] = cmd.read(list_events, entity_id=owner.id)
    assert event.correlation_id == request_id
    assert (event.actor_type, event.actor_id) == (ActorType.USER, f"user:{cmd.auth.user_id}")
    assert _commands(app_engine, b).read(list_owners) == []  # RLS: another workspace


def test_run_rolls_back_on_any_exception(app_engine, fresh_workspaces):
    a, _ = fresh_workspaces
    cmd = _commands(app_engine, a)

    class Boom(Exception):
        pass

    def create_then_fail(ctx):
        owner = create_owner(ctx, OwnerCreate(full_name="Never Stored"))
        raise Boom(owner.id)

    with pytest.raises(Boom) as excinfo:
        cmd.run(create_then_fail)
    assert cmd.read(get_owner, excinfo.value.args[0]) is None
    assert cmd.read(list_owners) == []
    assert cmd.read(list_events) == []


def test_read_is_a_read_only_transaction(app_engine, fresh_workspaces):
    a, _ = fresh_workspaces
    cmd = _commands(app_engine, a)

    def sneaky_write(conn):
        conn.execute(
            text("INSERT INTO owner (workspace_id, full_name) VALUES (:ws, 'x')"), {"ws": a}
        )

    with pytest.raises(DBAPIError, match="read-only transaction"):
        cmd.read(sneaky_write)
    assert cmd.read(list_owners) == []


def test_each_call_is_its_own_transaction(app_engine, fresh_workspaces):
    a, _ = fresh_workspaces
    cmd = _commands(app_engine, a)
    first = cmd.read(lambda conn: conn.execute(text("SELECT txid_current()")).scalar_one())
    second = cmd.read(lambda conn: conn.execute(text("SELECT txid_current()")).scalar_one())
    assert first != second
    assert (
        cmd.read(lambda conn: conn.execute(text("SELECT current_workspace_id()")).scalar_one()) == a
    )
