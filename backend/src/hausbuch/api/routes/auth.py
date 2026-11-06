"""Passkey sign-in, sessions, invites (docs/phases/phase-1-step-3.md §6.3).

Public: enroll/options, enroll/verify, login/options, login/verify. Everything else needs a
session (current_auth). Cookies: the session cookie and the ceremony cookie are both
HttpOnly, SameSite=Lax, Path=/, no Domain, Secure with the __Host- prefix unless
HAUSBUCH_COOKIE_SECURE=false (http://localhost only). Every unsafe method here also checks
that Origin equals HAUSBUCH_ORIGIN (§6.6), the WebAuthn verify endpoints included.

Errors are HTTPExceptions whose detail is the ErrorKind string, like NotAuthenticatedError.
A failed ceremony is a 401 with no detail and one warning log line naming only the step and
a fixed reason code: never a token, credential id, email or client data.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import JsonValue
from sqlalchemy import Engine, text

from hausbuch.auth import ceremony as ceremonies
from hausbuch.auth import invites, passkeys, sessions
from hausbuch.auth import webauthn as ceremony_flows
from hausbuch.auth.errors import (
    AuthenticationFailed,
    DuplicateCredentialError,
    DuplicateUserError,
    InviteNotFoundError,
    LastPasskeyError,
    PasskeyNotFoundError,
)
from hausbuch.auth.events import append_user_event
from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.api import ErrorKind
from hausbuch.schemas.auth import (
    EnrollmentOptionsRequest,
    EnrollmentVerifyRequest,
    Invite,
    InviteCreate,
    LoginVerifyRequest,
    Me,
    Member,
    Passkey,
    PasskeyLabel,
    PasskeyVerifyRequest,
)
from hausbuch.schemas.events import (
    Empty,
    EventType,
    UserInvited,
    UserPasskeyRef,
    UserSessionsRevoked,
)
from hausbuch.settings import Settings

from ..auth_context import AuthContext, current_auth, session_cookie_name

log = logging.getLogger(__name__)

# The signed-in caller (401 without a valid session).
Auth = Annotated[AuthContext, Depends(current_auth)]
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_ONLY = text("SET TRANSACTION READ ONLY")


class AuthRouteError(HTTPException):
    """A non-2xx answer of an auth route; detail is the ErrorKind string (§3.5)."""

    def __init__(self, status_code: int, kind: ErrorKind) -> None:
        super().__init__(status_code=status_code, detail=kind.value)


def require_same_origin(request: Request) -> None:
    """§6.6: an unsafe request must carry Origin == HAUSBUCH_ORIGIN, else 403."""
    if request.method in UNSAFE_METHODS:
        settings: Settings = request.app.state.settings
        if request.headers.get("origin") != settings.origin:
            raise AuthRouteError(403, ErrorKind.FORBIDDEN_ORIGIN)


router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_same_origin)])


def _now() -> datetime:
    """The clock of these routes (tests replace it)."""
    return datetime.now(UTC)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _engine(request: Request) -> Engine:
    return request.app.state.engine


# --- Cookies --------------------------------------------------------------------------


def _ceremony_cookie_name(settings: Settings) -> str:
    secure = settings.cookie_secure
    return ceremonies.CEREMONY_COOKIE if secure else ceremonies.INSECURE_CEREMONY_COOKIE


def _set_cookie(response: Response, settings: Settings, name: str, value: str, max_age: int):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_cookie(response: Response, settings: Settings, name: str) -> None:
    response.delete_cookie(
        name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )


def _start_ceremony(response: Response, settings: Settings, ceremony) -> None:
    value = ceremonies.seal(settings.session_key, ceremony)
    name = _ceremony_cookie_name(settings)
    _set_cookie(response, settings, name, value, ceremonies.CEREMONY_MAX_AGE_S)


def _take_ceremony(request: Request, response: Response, kind, now: datetime):
    """The ceremony from the cookie (verified), and the cookie cleared whatever happens."""
    settings = _settings(request)
    name = _ceremony_cookie_name(settings)
    _clear_cookie(response, settings, name)
    return ceremonies.open_sealed(
        settings.session_key, request.cookies.get(name), kind=kind, now=now
    )


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    name = session_cookie_name(settings)
    _set_cookie(response, settings, name, token, settings.session_max_age_s)


@contextmanager
def _refusing(step: str, response: Response | None = None) -> Iterator[None]:
    """AuthenticationFailed -> 401 with no detail, logged as step + reason code only. The
    response's cookie changes (cleared ceremony cookie) are carried onto the 401."""
    try:
        yield
    except AuthenticationFailed as exc:
        log.warning("auth %s refused: %s", step, exc.reason)
        headers = None
        if response is not None and "set-cookie" in response.headers:
            headers = {"set-cookie": response.headers["set-cookie"]}
        error = AuthRouteError(401, ErrorKind.UNAUTHENTICATED)
        error.headers = headers
        raise error from None


# --- Enrollment (public) -------------------------------------------------------------


@router.post("/enroll/options")
def enroll_options(
    body: EnrollmentOptionsRequest, request: Request, response: Response
) -> dict[str, JsonValue]:
    settings = _settings(request)
    with _refusing("enroll.options"):
        options, ceremony = ceremony_flows.enrollment_options(
            _engine(request), settings, body.token, now=_now()
        )
    _start_ceremony(response, settings, ceremony)
    return options


@router.post("/enroll/verify", status_code=201)
def enroll_verify(body: EnrollmentVerifyRequest, request: Request, response: Response) -> Me:
    settings, now = _settings(request), _now()
    with _refusing("enroll.verify", response):
        ceremony = _take_ceremony(request, response, "registration", now)
        try:
            signed_in = ceremony_flows.enroll(
                _engine(request),
                settings,
                token=body.token,
                credential=body.credential,
                label=body.label,
                ceremony=ceremony,
                now=now,
            )
        except DuplicateCredentialError:
            raise AuthRouteError(409, ErrorKind.CONFLICT) from None
    _set_session_cookie(response, settings, signed_in.token)
    return signed_in.me


# --- Sign-in (public) -------------------------------------------------------------------


@router.post("/login/options")
def login_options(request: Request, response: Response) -> dict[str, JsonValue]:
    settings = _settings(request)
    options, ceremony = ceremony_flows.login_options(settings, now=_now())
    _start_ceremony(response, settings, ceremony)
    return options


@router.post("/login/verify")
def login_verify(body: LoginVerifyRequest, request: Request, response: Response) -> Me:
    settings, now = _settings(request), _now()
    with _refusing("login.verify", response):
        ceremony = _take_ceremony(request, response, "authentication", now)
        signed_in = ceremony_flows.sign_in(
            _engine(request), settings, credential=body.credential, ceremony=ceremony, now=now
        )
    _set_session_cookie(response, settings, signed_in.token)
    return signed_in.me


# --- Session ----------------------------------------------------------------------------


@router.post("/logout", status_code=204)
def logout(request: Request, auth: Auth) -> Response:
    now = _now()
    with workspace_transaction(_engine(request), auth.workspace_id) as conn:
        sessions.revoke_session(conn, auth.session_id, now=now)
        _event(conn, request, auth, EventType.USER_SIGNED_OUT, Empty(), now)
    return _signed_out(request)


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, auth: Auth) -> Response:
    now = _now()
    with workspace_transaction(_engine(request), auth.workspace_id) as conn:
        count = sessions.revoke_user_sessions(conn, auth.user_id, now=now)
        revoked = UserSessionsRevoked(count=count)
        _event(conn, request, auth, EventType.USER_SESSIONS_REVOKED, revoked, now)
    return _signed_out(request)


@router.get("/me")
def me(request: Request, auth: Auth) -> Me:
    with workspace_transaction(_engine(request), auth.workspace_id) as conn:
        conn.execute(_READ_ONLY)
        return sessions.load_me(
            conn, workspace_id=auth.workspace_id, user_id=auth.user_id, session_id=auth.session_id
        )


def _event(conn, request: Request, auth: AuthContext, type: EventType, payload, now) -> None:
    """A user.* event by the signed-in caller about themself (§10), correlated with the
    request like every domain command's events."""
    append_user_event(
        conn,
        type,
        payload,
        workspace_id=auth.workspace_id,
        user_id=auth.user_id,
        now=now,
        correlation_id=getattr(request.state, "request_id", None),
    )


def _signed_out(request: Request) -> Response:
    response = Response(status_code=204)
    settings = _settings(request)
    _clear_cookie(response, settings, session_cookie_name(settings))
    return response


# --- Passkeys ---------------------------------------------------------------------------


@router.get("/passkeys")
def list_passkeys(request: Request, auth: Auth) -> list[Passkey]:
    with workspace_transaction(_engine(request), auth.workspace_id) as conn:
        conn.execute(_READ_ONLY)
        return passkeys.list_passkeys(conn, auth.user_id)


@router.post("/passkeys/options")
def passkey_options(request: Request, response: Response, auth: Auth) -> dict[str, JsonValue]:
    settings = _settings(request)
    options, ceremony = ceremony_flows.passkey_options(_engine(request), settings, auth, now=_now())
    _start_ceremony(response, settings, ceremony)
    return options


@router.post("/passkeys/verify", status_code=201)
def passkey_verify(
    body: PasskeyVerifyRequest,
    request: Request,
    response: Response,
    auth: Auth,
) -> Passkey:
    settings, now = _settings(request), _now()
    with _refusing("passkeys.verify", response):
        ceremony = _take_ceremony(request, response, "registration", now)
        try:
            return ceremony_flows.register_passkey(
                _engine(request),
                settings,
                auth,
                credential=body.credential,
                label=body.label,
                ceremony=ceremony,
                now=now,
            )
        except DuplicateCredentialError:
            raise AuthRouteError(409, ErrorKind.CONFLICT) from None


@router.patch("/passkeys/{passkey_id}")
def rename_passkey(
    passkey_id: UUID,
    body: PasskeyLabel,
    request: Request,
    auth: Auth,
) -> Passkey:
    try:
        with workspace_transaction(_engine(request), auth.workspace_id) as conn:
            renamed = passkeys.rename_passkey(conn, auth.user_id, passkey_id, body.label)
            ref = UserPasskeyRef(passkey_id=passkey_id)
            _event(conn, request, auth, EventType.USER_PASSKEY_RENAMED, ref, _now())
            return renamed
    except PasskeyNotFoundError:
        raise AuthRouteError(404, ErrorKind.NOT_FOUND) from None


@router.delete("/passkeys/{passkey_id}", status_code=204)
def delete_passkey(passkey_id: UUID, request: Request, auth: Auth) -> Response:
    """Ends the sessions this passkey opened (§10.5), the caller's own included if it signed
    in with it; 409 for the user's last passkey (§6.7)."""
    try:
        now = _now()
        with workspace_transaction(_engine(request), auth.workspace_id) as conn:
            passkeys.delete_passkey(conn, auth.user_id, passkey_id, now=now)
            ref = UserPasskeyRef(passkey_id=passkey_id)
            _event(conn, request, auth, EventType.USER_PASSKEY_REMOVED, ref, now)
    except PasskeyNotFoundError:
        raise AuthRouteError(404, ErrorKind.NOT_FOUND) from None
    except LastPasskeyError:
        raise AuthRouteError(409, ErrorKind.CONFLICT) from None
    if auth.passkey_id == passkey_id:
        return _signed_out(request)
    return Response(status_code=204)


# --- Invites ----------------------------------------------------------------------------


@router.post("/invites", status_code=201)
def create_invite(body: InviteCreate, request: Request, auth: Auth) -> Invite:
    """The enrollment URL is in this response only (the one time the token exists)."""
    settings = _settings(request)
    try:
        now = _now()
        with workspace_transaction(_engine(request), auth.workspace_id) as conn:
            user, issued = invites.invite_member(
                conn, auth.workspace_id, body, created_by=auth.user_id, now=now
            )
            invited = UserInvited(invite_id=issued.id, invited_user_id=user.id)
            _event(conn, request, auth, EventType.USER_INVITED, invited, now)
    except DuplicateUserError:
        raise AuthRouteError(409, ErrorKind.CONFLICT) from None
    return Invite(
        id=issued.id,
        user=user,
        enrollment_url=invites.enrollment_url(settings.origin, issued.token),
        expires_at=issued.expires_at,
    )


@router.get("/invites")
def list_invites(request: Request, auth: Auth) -> list[Invite]:
    with workspace_transaction(_engine(request), auth.workspace_id) as conn:
        conn.execute(_READ_ONLY)
        return invites.list_pending_invites(conn, now=_now())


@router.get("/members")
def list_members(request: Request, auth: Auth) -> list[Member]:
    """The household's members, enrolled or invited, oldest first (§6.7, for /settings)."""
    with workspace_transaction(_engine(request), auth.workspace_id) as conn:
        conn.execute(_READ_ONLY)
        return invites.list_members(conn)


@router.delete("/invites/{invite_id}", status_code=204)
def delete_invite(invite_id: UUID, request: Request, auth: Auth) -> Response:
    try:
        with workspace_transaction(_engine(request), auth.workspace_id) as conn:
            invites.delete_invite(conn, invite_id)
    except InviteNotFoundError:
        raise AuthRouteError(404, ErrorKind.NOT_FOUND) from None
    return Response(status_code=204)
