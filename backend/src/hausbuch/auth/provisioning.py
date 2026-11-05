"""Workspace provisioning and recovery as the schema owner, never a superuser (§6.5, §6.7, §7).

The app role has no INSERT on `workspace` (the Scaleway finding), so a workspace is created by
the CLI through an engine on HAUSBUCH_ADMIN_DATABASE_URL (the hausbuch_owner role). RLS is
FORCEd, so even the owner may insert the row only with app.workspace_id set to the new id in
the same transaction: workspace_transaction(owner_engine, new_id) does exactly that
(set_config('app.workspace_id', <new id>, true)) before the insert.

Both entry points refuse a role that is superuser or BYPASSRLS, whatever engine they are
handed: under such a role RLS would not hold and a misconfigured URL must fail loudly.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, insert, select, text

from hausbuch.db.session import UnsafeDatabaseRoleError, workspace_transaction
from hausbuch.db.tables import app_user, auth_passkey, workspace
from hausbuch.schemas.auth import InviteCreate
from hausbuch.schemas.workspace import Workspace

from . import lookup
from .errors import UserNotFoundError
from .invites import IssuedInvite, create_invite, create_user, delete_pending_invites
from .passkeys import delete_passkey
from .sessions import revoke_user_sessions

_ROLE = text(
    "SELECT current_user, rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
)


@dataclass(frozen=True)
class Provisioned:
    workspace_id: UUID
    user_id: UUID
    invite: IssuedInvite  # invite.token: shown once, never stored


@dataclass(frozen=True)
class Reissued:
    workspace_id: UUID
    user_id: UUID
    invite: IssuedInvite
    passkeys_deleted: int
    sessions_revoked: int


def provision_workspace(
    owner_engine: Engine, *, name: str, email: str, display_name: str, now: datetime
) -> Provisioned:
    """A workspace, its first app_user and an enrollment invite, in one transaction."""
    member = InviteCreate(email=email, display_name=display_name)  # validates, lower-cases
    Workspace(id=uuid4(), name=name, created_at=now)  # the name's bounds, before connecting
    workspace_id = uuid4()
    with workspace_transaction(owner_engine, workspace_id) as conn:
        refuse_rls_bypass(conn)
        conn.execute(insert(workspace).values(id=workspace_id, name=name, created_at=now))
        user = create_user(
            conn, workspace_id, email=member.email, display_name=member.display_name, now=now
        )
        invite = create_invite(conn, workspace_id, user.id, created_by=None, now=now)
    return Provisioned(workspace_id=workspace_id, user_id=user.id, invite=invite)


def reissue_enrollment(
    owner_engine: Engine,
    *,
    email: str,
    now: datetime,
    workspace_id: UUID | None = None,
    revoke_passkeys: bool = False,
    revoke_sessions: bool = False,
) -> Reissued:
    """`hausbuch invite` (§6.7): a fresh enrollment link for an existing user; their earlier
    unused links stop working. --revoke-passkeys deletes every passkey of the user (ending
    the sessions each opened, §10.5); --revoke-sessions revokes every session of the user.

    app_user is invisible without a workspace, so the user is searched in `workspace_id`, or
    else in every provisioned workspace (lookup.provisioned_workspace_ids); emails are unique
    across workspaces, so at most one matches."""
    email = InviteCreate(email=email, display_name="-").email
    with owner_engine.connect() as conn:
        refuse_rls_bypass(conn)
    candidates = (
        [workspace_id] if workspace_id else sorted(lookup.provisioned_workspace_ids(owner_engine))
    )
    for ws in candidates:
        with workspace_transaction(owner_engine, ws) as conn:
            user_id = conn.execute(
                select(app_user.c.id).where(app_user.c.email == email)
            ).scalar_one_or_none()
            if user_id is None:
                continue
            deleted = _delete_all_passkeys(conn, user_id, now) if revoke_passkeys else 0
            revoked = revoke_user_sessions(conn, user_id, now=now) if revoke_sessions else 0
            delete_pending_invites(conn, user_id)
            invite = create_invite(conn, ws, user_id, created_by=None, now=now)
        return Reissued(ws, user_id, invite, deleted, revoked)
    raise UserNotFoundError("no user with that email")


def refuse_rls_bypass(conn: Connection) -> None:
    role, bypasses = conn.execute(_ROLE).one()
    if bypasses:
        raise UnsafeDatabaseRoleError(
            f"database role {role!r} is a superuser or has BYPASSRLS; provisioning runs as the "
            "schema owner (hausbuch_owner), never as a superuser."
        )


def _delete_all_passkeys(conn: Connection, user_id: UUID, now: datetime) -> int:
    ids = list(
        conn.execute(select(auth_passkey.c.id).where(auth_passkey.c.user_id == user_id)).scalars()
    )
    for passkey_id in ids:
        delete_passkey(conn, user_id, passkey_id, now=now, allow_last=True)
    return len(ids)
