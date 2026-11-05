"""Who is running a command, inside which transaction, and when."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection

from hausbuch.schemas.enums import ActorType


@dataclass(frozen=True)
class Actor:
    actor_type: ActorType
    actor_id: str  # user id, job name, or model id

    def __post_init__(self) -> None:
        if not self.actor_id:
            raise ValueError("actor_id must not be empty")


@dataclass(frozen=True)
class CommandContext:
    """One command. Every event it appends shares correlation_id and is recorded at `now`."""

    conn: Connection  # inside workspace_transaction(engine, workspace_id)
    workspace_id: UUID
    actor: Actor
    correlation_id: UUID
    now: datetime

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            raise ValueError("now must be timezone-aware")


def new_command(
    conn: Connection, workspace_id: UUID, actor: Actor, *, now: datetime | None = None
) -> CommandContext:
    """A context for one command, with a fresh correlation id. Pass `now` in tests."""
    return CommandContext(
        conn=conn,
        workspace_id=workspace_id,
        actor=actor,
        correlation_id=uuid4(),
        now=now if now is not None else datetime.now(UTC),
    )
