"""Sign-in and passkey events (phase 2 spec §10, D28).

Entity type `user`, actor the user themself, no effective date. Payloads carry passkey,
invite and user ids, a passkey's label and a count: never an IP, a user agent, a token or
credential material. Failed sign-ins have no workspace and stay in the server log only.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection

from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.events import EventType
from hausbuch.services.context import Actor, CommandContext
from hausbuch.services.events import append_event


def append_user_event(
    conn: Connection,
    type: EventType,
    payload: Schema,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    correlation_id: UUID | None = None,
) -> None:
    """Append one user.* event about `user_id`, by them, in the caller's transaction."""
    ctx = CommandContext(
        conn=conn,
        workspace_id=workspace_id,
        actor=Actor(ActorType.USER, f"user:{user_id}"),
        correlation_id=correlation_id or uuid4(),
        now=now,
    )
    append_event(ctx, type, user_id, payload)
