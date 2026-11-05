"""Enrollment invites: one-time links for the first user, a household member, or recovery.

The link is `<origin>/enroll#token=<token>`: the token travels in the fragment, so it never
reaches a server log or the Next.js server. Only its SHA-256 is stored. An invite is valid for
INVITE_TTL and is redeemed at most once (an atomic UPDATE ... WHERE used_at IS NULL).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import app_user, auth_invite
from hausbuch.schemas.auth import Invite, InviteCreate
from hausbuch.schemas.workspace import AppUser

from .errors import DuplicateUserError, InviteNotFoundError
from .tokens import new_token

INVITE_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class IssuedInvite:
    id: UUID
    user_id: UUID
    token: str  # shown once (CLI output, POST /auth/invites response); never stored
    expires_at: datetime


def enrollment_url(origin: str, token: str) -> str:
    return f"{origin}/enroll#token={token}"


def create_invite(
    conn: Connection,
    workspace_id: UUID,
    user_id: UUID,
    *,
    created_by: UUID | None,
    now: datetime,
) -> IssuedInvite:
    """A new invite for an existing user, in the caller's workspace_transaction. The same
    code serves the CLI (created_by None) and the API."""
    token, digest = new_token()
    expires_at = now + INVITE_TTL
    invite_id = conn.execute(
        insert(auth_invite)
        .values(
            workspace_id=workspace_id,
            user_id=user_id,
            token_hash=digest,
            created_by=created_by,
            created_at=now,
            expires_at=expires_at,
        )
        .returning(auth_invite.c.id)
    ).scalar_one()
    return IssuedInvite(id=invite_id, user_id=user_id, token=token, expires_at=expires_at)


def create_user(
    conn: Connection, workspace_id: UUID, *, email: str, display_name: str, now: datetime
) -> AppUser:
    """A new app_user (lower-cased email). Emails are unique across workspaces: a taken one is
    a DuplicateUserError whose message does not name it."""
    try:
        with conn.begin_nested():
            row = conn.execute(
                insert(app_user)
                .values(
                    workspace_id=workspace_id,
                    email=email.lower(),
                    display_name=display_name,
                    created_at=now,
                )
                .returning(app_user)
            ).one()
    except IntegrityError:
        raise DuplicateUserError("a user with that email exists already") from None
    return AppUser.model_validate(row._asdict())


def invite_member(
    conn: Connection,
    workspace_id: UUID,
    body: InviteCreate,
    *,
    created_by: UUID,
    now: datetime,
) -> tuple[AppUser, IssuedInvite]:
    """POST /auth/invites: the member's user row (it exists from the invite on, harmless
    while un-enrolled) and their invite."""
    user = create_user(
        conn, workspace_id, email=body.email, display_name=body.display_name, now=now
    )
    return user, create_invite(conn, workspace_id, user.id, created_by=created_by, now=now)


def list_pending_invites(conn: Connection, *, now: datetime) -> list[Invite]:
    """The workspace's unused, unexpired invites, oldest first. Never the token."""
    t = auth_invite
    rows = conn.execute(
        select(t.c.id.label("invite_id"), t.c.expires_at.label("invite_expires_at"), app_user)
        .join(
            app_user,
            (app_user.c.workspace_id == t.c.workspace_id) & (app_user.c.id == t.c.user_id),
        )
        .where(t.c.used_at.is_(None), t.c.expires_at > now)
        .order_by(t.c.created_at, t.c.id)
    )
    invites = []
    for row in rows:
        values = row._asdict()
        user = AppUser.model_validate({c.name: values[c.name] for c in app_user.columns})
        invites.append(
            Invite(id=values["invite_id"], user=user, expires_at=values["invite_expires_at"])
        )
    return invites


def delete_invite(conn: Connection, invite_id: UUID) -> None:
    """DELETE /auth/invites/{id}: only a pending invite; a used one documents an enrollment."""
    result = conn.execute(
        delete(auth_invite).where(auth_invite.c.id == invite_id, auth_invite.c.used_at.is_(None))
    )
    if result.rowcount == 0:
        raise InviteNotFoundError("invite not found")


def delete_pending_invites(conn: Connection, user_id: UUID) -> int:
    """Recovery (`hausbuch invite`): earlier unused links of the user stop working."""
    result = conn.execute(
        delete(auth_invite).where(auth_invite.c.user_id == user_id, auth_invite.c.used_at.is_(None))
    )
    return result.rowcount


def redeem_invite(conn: Connection, invite_id: UUID, *, now: datetime) -> bool:
    """Mark the invite used, atomically: False if it was used (or expired) meanwhile."""
    t = auth_invite
    result = conn.execute(
        update(t)
        .where(t.c.id == invite_id, t.c.used_at.is_(None), t.c.expires_at > now)
        .values(used_at=now)
    )
    return result.rowcount == 1
