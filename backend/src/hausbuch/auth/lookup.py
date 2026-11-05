"""Pre-auth reads: the one place `app.auth_lookup` is switched on (§6.2).

Sign-in, session resolution and invite redemption must find the workspace before they can
open a workspace_transaction, so they read the three auth tables under the SELECT-only
`auth_lookup` policy. The flag is set only here, with set_config(..., is_local => true), in a
transaction that

- is READ ONLY (so even a bug cannot write under the flag; the policy grants no writes either),
- has no workspace set (checked, not assumed),
- ends before any write: every update that follows a lookup (sign_count, last_seen_at,
  used_at) runs in a separate, normal workspace_transaction.

Callers get plain frozen records, never a connection, so nothing can run under the flag
outside this module.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, select, text

from hausbuch.db.tables import auth_invite, auth_passkey, auth_session

LOOKUP_SETTING = "app.auth_lookup"
_READ_ONLY = text("SET TRANSACTION READ ONLY")
_LOOKUP_ON = text(
    "SELECT set_config('app.auth_lookup', 'on', true),"
    " coalesce(current_setting('app.workspace_id', true), '')"
)


class LookupScopeError(RuntimeError):
    """The lookup transaction found a workspace already set: a programming error."""


@contextmanager
def auth_lookup_transaction(engine: Engine) -> Iterator[Connection]:
    """A READ ONLY transaction with the lookup flag on and no workspace. Private to this module
    in spirit: the find_* functions below are the supported way in."""
    with engine.begin() as conn:
        conn.execute(_READ_ONLY)  # must be the transaction's first statement
        _, workspace = conn.execute(_LOOKUP_ON).one()
        if workspace:
            raise LookupScopeError("auth lookup must run with no workspace set")
        yield conn


@dataclass(frozen=True)
class PasskeyRecord:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    public_key: bytes
    sign_count: int


@dataclass(frozen=True)
class SessionRecord:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    passkey_id: UUID | None
    last_seen_at: datetime
    expires_at: datetime
    idle_expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class InviteRecord:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    expires_at: datetime
    used_at: datetime | None

    def usable(self, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at


def find_passkey(engine: Engine, credential_id: bytes) -> PasskeyRecord | None:
    """The passkey with this credential id, in any workspace (credential ids are global)."""
    t = auth_passkey
    query = select(t.c.id, t.c.workspace_id, t.c.user_id, t.c.public_key, t.c.sign_count).where(
        t.c.credential_id == credential_id
    )
    with auth_lookup_transaction(engine) as conn:
        row = conn.execute(query).one_or_none()
    return None if row is None else PasskeyRecord(*row)


def find_session(engine: Engine, token_hash: bytes) -> SessionRecord | None:
    t = auth_session
    query = select(
        t.c.id,
        t.c.workspace_id,
        t.c.user_id,
        t.c.passkey_id,
        t.c.last_seen_at,
        t.c.expires_at,
        t.c.idle_expires_at,
        t.c.revoked_at,
    ).where(t.c.token_hash == token_hash)
    with auth_lookup_transaction(engine) as conn:
        row = conn.execute(query).one_or_none()
    return None if row is None else SessionRecord(*row)


def find_invite(engine: Engine, token_hash: bytes) -> InviteRecord | None:
    t = auth_invite
    query = select(t.c.id, t.c.workspace_id, t.c.user_id, t.c.expires_at, t.c.used_at).where(
        t.c.token_hash == token_hash
    )
    with auth_lookup_transaction(engine) as conn:
        row = conn.execute(query).one_or_none()
    return None if row is None else InviteRecord(*row)


def provisioned_workspace_ids(engine: Engine) -> set[UUID]:
    """Workspaces that have an invite or a passkey. Every user is created together with an
    invite (bootstrap, member invite) and redeemed invites are kept, so this is every
    workspace that was provisioned through hausbuch.auth. The CLI uses it where the workspace
    table itself is invisible (RLS is forced even for the schema owner)."""
    query = select(auth_invite.c.workspace_id).union(select(auth_passkey.c.workspace_id))
    with auth_lookup_transaction(engine) as conn:
        return set(conn.execute(query).scalars())
