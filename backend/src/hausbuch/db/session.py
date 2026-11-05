"""Engine and workspace-scoped transactions.

Every service call runs inside workspace_transaction(): the RLS key app.workspace_id is set
with set_config(..., is_local => true), so it lasts exactly one transaction and a pooled
connection never carries a workspace into the next one.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

_SET_WORKSPACE = text("SELECT set_config('app.workspace_id', :ws, true)")


def create_app_engine(url: str, **engine_kwargs: Any) -> Engine:
    """Engine for the app role. Extra keyword arguments go to sqlalchemy.create_engine."""
    return create_engine(url, pool_pre_ping=True, **engine_kwargs)


@contextmanager
def workspace_transaction(engine: Engine, workspace_id: UUID) -> Iterator[Connection]:
    """One transaction scoped to a workspace: commits on success, rolls back on any exception."""
    if not isinstance(workspace_id, UUID):
        raise TypeError(f"workspace_id must be a UUID, got {type(workspace_id).__name__}")
    with engine.begin() as conn:
        conn.execute(_SET_WORKSPACE, {"ws": str(workspace_id)})
        yield conn
