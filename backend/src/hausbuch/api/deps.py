"""FastAPI dependencies of the domain routers, and the small read helpers they share.

Every domain router is built with `domain_router()`, which requires a session on every
route it holds (current_auth), so a route cannot be added without authentication.
"""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection

from hausbuch.schemas.enums import EntityType
from hausbuch.services.errors import NotFoundError
from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

from .auth_context import AuthContext, current_auth
from .commands import Commands


def domain_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(current_auth)])


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_blob_store(request: Request) -> BlobStore:
    return request.app.state.blob_store


def get_commands(request: Request, auth: Annotated[AuthContext, Depends(current_auth)]) -> Commands:
    """The request's Commands: its session's workspace and user, its request id (set by
    RequestMiddleware) as the correlation id of every event it appends."""
    request_id = getattr(request.state, "request_id", None) or uuid4()
    return Commands(request.app.state.engine, auth, request_id)


Cmd = Annotated[Commands, Depends(get_commands)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[BlobStore, Depends(get_blob_store)]


def found[T](value: T | None, entity_type: EntityType | str, entity_id: UUID) -> T:
    """`value`, or NotFoundError (404) when a get_* query returned None."""
    if value is None:
        raise NotFoundError(entity_type, entity_id)
    return value


def require(
    conn: Connection,
    getter: Callable[[Connection, UUID], Any],
    entity_type: EntityType,
    entity_id: UUID,
) -> None:
    """Inside a read: the parent of a sub-collection must exist in this workspace, so another
    workspace's id answers 404 rather than an empty list."""
    found(getter(conn, entity_id), entity_type, entity_id)
