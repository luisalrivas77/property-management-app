"""Opaque server-side sessions in Postgres (§6.4), and the API's SessionResolver.

- The cookie carries 32 random bytes (base64url); auth_session stores only their SHA-256.
- Lifetimes come from settings: absolute session_max_age_s (12 h), idle session_idle_s (2 h).
  The idle deadline slides forward on use, at most once every SLIDE_INTERVAL, and never past
  the absolute one.
- A fresh row on every sign-in; logout revokes (the row documents who was signed in); a
  user's expired rows are deleted on that user's next sign-in. No sweeper job.

Resolution reads under the lookup flag (hausbuch.auth.lookup) and writes the slide in a
separate workspace_transaction.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, Engine, delete, func, insert, or_, select, update

from hausbuch.api.auth_context import AuthContext
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import app_user, auth_session, workspace
from hausbuch.schemas.auth import Me, Session
from hausbuch.schemas.workspace import AppUser, Workspace
from hausbuch.settings import Settings

from . import lookup, tokens

SLIDE_INTERVAL = timedelta(minutes=5)

_SESSION_COLUMNS = (
    auth_session.c.id,
    auth_session.c.passkey_id,
    auth_session.c.created_at,
    auth_session.c.last_seen_at,
    auth_session.c.expires_at,
    auth_session.c.idle_expires_at,
)


@dataclass(frozen=True)
class OpenedSession:
    token: str  # the cookie value; exists only here, never stored or logged
    session: Session


def open_session(
    conn: Connection,
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
    passkey_id: UUID | None,
    now: datetime,
) -> OpenedSession:
    """A new session for the user, inside the caller's workspace_transaction. Deletes the
    user's expired sessions first (§6.4: no sweeper job)."""
    t = auth_session
    conn.execute(
        delete(t).where(
            t.c.user_id == user_id, or_(t.c.expires_at <= now, t.c.idle_expires_at <= now)
        )
    )
    token, digest = tokens.new_token()
    expires_at = now + timedelta(seconds=settings.session_max_age_s)
    row = conn.execute(
        insert(t)
        .values(
            workspace_id=workspace_id,
            user_id=user_id,
            passkey_id=passkey_id,
            token_hash=digest,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            idle_expires_at=min(now + timedelta(seconds=settings.session_idle_s), expires_at),
        )
        .returning(*_SESSION_COLUMNS)
    ).one()
    return OpenedSession(token=token, session=Session.model_validate(row._asdict()))


def get_session(conn: Connection, session_id: UUID) -> Session:
    row = conn.execute(select(*_SESSION_COLUMNS).where(auth_session.c.id == session_id)).one()
    return Session.model_validate(row._asdict())


def load_me(conn: Connection, *, workspace_id: UUID, user_id: UUID, session_id: UUID) -> Me:
    """GET /auth/me, inside a workspace_transaction of that workspace."""
    user = conn.execute(select(app_user).where(app_user.c.id == user_id)).one()
    ws = conn.execute(select(workspace).where(workspace.c.id == workspace_id)).one()
    return Me(
        user=AppUser.model_validate(user._asdict()),
        workspace=Workspace.model_validate(ws._asdict()),
        session=get_session(conn, session_id),
    )


def revoke_session(conn: Connection, session_id: UUID, *, now: datetime) -> None:
    t = auth_session
    conn.execute(
        update(t).where(t.c.id == session_id, t.c.revoked_at.is_(None)).values(revoked_at=now)
    )


def revoke_user_sessions(conn: Connection, user_id: UUID, *, now: datetime) -> int:
    t = auth_session
    result = conn.execute(
        update(t).where(t.c.user_id == user_id, t.c.revoked_at.is_(None)).values(revoked_at=now)
    )
    return result.rowcount


def end_passkey_sessions(conn: Connection, passkey_id: UUID, *, now: datetime) -> None:
    """§10.5: before a passkey is deleted, revoke every session it opened and detach them
    (passkey_id = NULL; the FK is NO ACTION on purpose). Same transaction as the delete."""
    t = auth_session
    conn.execute(
        update(t)
        .where(t.c.passkey_id == passkey_id, t.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    conn.execute(update(t).where(t.c.passkey_id == passkey_id).values(passkey_id=None))


def is_live(record: lookup.SessionRecord, now: datetime) -> bool:
    return record.revoked_at is None and now < record.expires_at and now < record.idle_expires_at


class SessionResolverImpl:
    """The SessionResolver (hausbuch.api.auth_context) over auth_session."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    def resolve(self, token: str, *, now: datetime) -> AuthContext | None:
        if not tokens.is_well_formed(token):
            return None
        record = lookup.find_session(self.engine, tokens.token_hash(token))
        if record is None or not is_live(record, now):
            return None
        if now - record.last_seen_at >= SLIDE_INTERVAL and not self._slide(record, now):
            return None  # revoked or expired between the lookup and the slide
        return AuthContext(
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            session_id=record.id,
            passkey_id=record.passkey_id,
        )

    def _slide(self, record: lookup.SessionRecord, now: datetime) -> bool:
        t = auth_session
        idle = now + timedelta(seconds=self.settings.session_idle_s)
        with workspace_transaction(self.engine, record.workspace_id) as conn:
            result = conn.execute(
                update(t)
                .where(
                    t.c.id == record.id,
                    t.c.revoked_at.is_(None),
                    t.c.expires_at > now,
                    t.c.idle_expires_at > now,
                )
                .values(last_seen_at=now, idle_expires_at=func.least(idle, t.c.expires_at))
            )
        return result.rowcount == 1
