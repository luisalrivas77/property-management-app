"""Who is calling: the session's workspace and user, never anything the client names (§3.1).

current_auth reads the session cookie and asks the app's SessionResolver (app.state) who it
belongs to. Chunk 2 implements the resolver over auth_session; API tests pass a fake one.
No request parameter, header or body field ever names a workspace.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from fastapi import HTTPException, Request

from hausbuch.schemas.api import ErrorKind
from hausbuch.schemas.enums import ActorType
from hausbuch.services.context import Actor
from hausbuch.settings import Settings

# §6.4: __Host- requires Secure, so a dev setup without Secure cookies drops the prefix.
SESSION_COOKIE = "__Host-hb_session"
INSECURE_SESSION_COOKIE = "hb_session"
# A real token is 43 characters (32 bytes, base64url); anything far longer is not one, and is
# refused without being hashed or looked up.
MAX_TOKEN_LENGTH = 128


@dataclass(frozen=True)
class AuthContext:
    """The resolved session. Every command of the request runs in workspace_id as user_id."""

    workspace_id: UUID
    user_id: UUID
    session_id: UUID
    passkey_id: UUID | None

    @property
    def actor(self) -> Actor:
        """The event log's actor: identifies the person (Luis vs his spouse) without naming
        them."""
        return Actor(ActorType.USER, f"user:{self.user_id}")


@runtime_checkable
class SessionResolver(Protocol):
    def resolve(self, token: str, *, now: datetime) -> AuthContext | None:
        """The session the cookie token belongs to, or None when there is no valid one
        (unknown, revoked, or past its absolute or idle expiry at `now`)."""
        ...


class NotAuthenticatedError(HTTPException):
    """401: no valid session. The API's error handler renders it as
    ErrorResponse(error="unauthenticated")."""

    def __init__(self) -> None:
        super().__init__(status_code=401, detail=ErrorKind.UNAUTHENTICATED.value)


def session_cookie_name(settings: Settings) -> str:
    """The session cookie's name; the code that sets the cookie must use the same one."""
    return SESSION_COOKIE if settings.cookie_secure else INSECURE_SESSION_COOKIE


def current_auth(request: Request) -> AuthContext:
    """FastAPI dependency: the caller's AuthContext, or 401. Also kept on request.state.auth
    for the request log line (user_id, workspace_id)."""
    state = request.app.state
    token = request.cookies.get(session_cookie_name(state.settings))
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise NotAuthenticatedError()
    resolver: SessionResolver = state.session_resolver
    auth = resolver.resolve(token, now=datetime.now(UTC))
    if auth is None:
        raise NotAuthenticatedError()
    request.state.auth = auth
    return auth
