"""workspace_transaction: transaction-local workspace, commit and rollback."""

import uuid
from contextlib import nullcontext

import pytest
from conftest import make_engine
from sqlalchemy import text

from hausbuch.db.session import UnsafeDatabaseRoleError, workspace_transaction

CURRENT = text("SELECT current_workspace_id(), pg_backend_pid()")


@pytest.fixture
def single_connection_engine(dbname):
    """A pool of exactly one connection, so every transaction reuses the same backend."""
    engine = make_engine(dbname, pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()


def test_sets_the_workspace_for_the_transaction(app_engine, workspaces):
    a, _ = workspaces
    with workspace_transaction(app_engine, a) as conn:
        assert conn.execute(CURRENT).one()[0] == a


@pytest.mark.parametrize("fail", [False, True], ids=["after-commit", "after-rollback"])
def test_workspace_does_not_leak_to_the_next_transaction(
    single_connection_engine, workspaces, fail
):
    a, _ = workspaces
    engine = single_connection_engine

    class Boom(Exception):
        pass

    with (
        pytest.raises(Boom) if fail else nullcontext(),
        workspace_transaction(engine, a) as conn,
    ):
        ws, pid = conn.execute(CURRENT).one()
        assert ws == a
        if fail:
            raise Boom

    with engine.begin() as conn:
        ws_after, pid_after = conn.execute(CURRENT).one()
        assert pid_after == pid, "the pool should have reused the same connection"
        assert ws_after is None
        assert conn.execute(text("SELECT count(*) FROM workspace")).scalar_one() == 0


def test_engine_refuses_a_role_that_bypasses_rls(dbname, workspaces):
    a, _ = workspaces
    engine = make_engine(dbname, role="postgres")
    try:
        with (
            pytest.raises(UnsafeDatabaseRoleError, match="'postgres'"),
            workspace_transaction(engine, a),
        ):
            pytest.fail("a superuser connection must never be handed out")
    finally:
        engine.dispose()


def test_rejects_a_non_uuid_workspace(app_engine):
    with pytest.raises(TypeError), workspace_transaction(app_engine, str(uuid.uuid4())):
        pass
