"""One workspace transaction per service command or query (§3.2).

Explicit `with`, not a `yield` dependency: rollback on any exception is guaranteed by
workspace_transaction itself, whatever order FastAPI tears dependencies down in. A route
calls run() at most once (one command per mutating request), so a failed request has
written nothing.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Concatenate
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from hausbuch.db.session import workspace_transaction
from hausbuch.services.context import Actor, CommandContext

from .auth_context import AuthContext

_READ_ONLY = text("SET TRANSACTION READ ONLY")


class Commands:
    """Runs services for one request, as its user, in its workspace. Every event a command
    appends carries correlation_id = request_id, so the audit trail and the log line share it.
    """

    def __init__(self, engine: Engine, auth: AuthContext, request_id: UUID) -> None:
        self.engine = engine
        self.auth = auth
        self.request_id = request_id

    @property
    def actor(self) -> Actor:
        return self.auth.actor

    def run[**P, R](
        self,
        command: Callable[Concatenate[CommandContext, P], R],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """A service command (first argument a CommandContext), committed on return and rolled
        back on any exception."""
        with workspace_transaction(self.engine, self.auth.workspace_id) as conn:
            ctx = CommandContext(
                conn=conn,
                workspace_id=self.auth.workspace_id,
                actor=self.actor,
                correlation_id=self.request_id,
                now=datetime.now(UTC),
            )
            return command(ctx, *args, **kwargs)

    def read[**P, R](
        self, query: Callable[Concatenate[Connection, P], R], /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        """A service query (first argument a Connection) in a READ ONLY transaction: a read
        path that tried to write would fail instead of writing."""
        with workspace_transaction(self.engine, self.auth.workspace_id) as conn:
            conn.execute(_READ_ONLY)
            return query(conn, *args, **kwargs)
