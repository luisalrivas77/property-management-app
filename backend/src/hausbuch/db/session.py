"""Engine and workspace-scoped transactions.

Every service call runs inside workspace_transaction(): the RLS key app.workspace_id is set
with set_config(..., is_local => true), so it lasts exactly one transaction and a pooled
connection never carries a workspace into the next one.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, text

_SET_WORKSPACE = text("SELECT set_config('app.workspace_id', :ws, true)")
_BYPASSES_RLS = "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"


class UnsafeDatabaseRoleError(RuntimeError):
    """The connected role bypasses row-level security, so workspace isolation would be off."""


def create_app_engine(url: str, **engine_kwargs: Any) -> Engine:
    """Engine for the app role. Extra keyword arguments go to sqlalchemy.create_engine.

    Every new physical connection is checked once: a role that is superuser or BYPASSRLS
    (checked as current_user, the effective role) is refused, because RLS would not apply.
    """
    engine = create_engine(url, pool_pre_ping=True, **engine_kwargs)
    event.listen(engine, "connect", _refuse_rls_bypassing_role)
    return engine


def _refuse_rls_bypassing_role(dbapi_connection, _connection_record) -> None:
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        (role,) = cursor.fetchone()
        cursor.execute(_BYPASSES_RLS)
        (bypasses,) = cursor.fetchone()
    dbapi_connection.rollback()  # leave no transaction open on the fresh connection
    if bypasses:
        raise UnsafeDatabaseRoleError(
            f"database role {role!r} is a superuser or has BYPASSRLS; row-level security "
            "would not isolate workspaces. Connect as the app role (hausbuch_app)."
        )


@contextmanager
def workspace_transaction(engine: Engine, workspace_id: UUID) -> Iterator[Connection]:
    """One transaction scoped to a workspace: commits on success, rolls back on any exception."""
    if not isinstance(workspace_id, UUID):
        raise TypeError(f"workspace_id must be a UUID, got {type(workspace_id).__name__}")
    with engine.begin() as conn:
        conn.execute(_SET_WORKSPACE, {"ws": str(workspace_id)})
        yield conn
