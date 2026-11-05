"""auth_passkey rows: list, add, rename, delete, and the atomic sign-counter update.

Everything here runs inside a workspace_transaction (RLS scopes it to the workspace); the
functions that act for a signed-in user also filter on that user, so one household member
never renames or deletes the other's passkeys.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import auth_passkey
from hausbuch.schemas.auth import Passkey

from .errors import DuplicateCredentialError, LastPasskeyError, PasskeyNotFoundError
from .sessions import end_passkey_sessions

_PASSKEY_COLUMNS = (
    auth_passkey.c.id,
    auth_passkey.c.label,
    auth_passkey.c.created_at,
    auth_passkey.c.last_used_at,
    auth_passkey.c.backed_up,
    auth_passkey.c.aaguid,
)


@dataclass(frozen=True)
class NewCredential:
    """A verified registration (hausbuch.auth.webauthn.verify_registration)."""

    credential_id: bytes
    public_key: bytes  # COSE key bytes as py_webauthn returns them
    sign_count: int
    aaguid: UUID | None
    transports: list[str] | None
    backup_eligible: bool
    backed_up: bool


def list_passkeys(conn: Connection, user_id: UUID) -> list[Passkey]:
    rows = conn.execute(
        select(*_PASSKEY_COLUMNS)
        .where(auth_passkey.c.user_id == user_id)
        .order_by(auth_passkey.c.created_at, auth_passkey.c.id)
    )
    return [Passkey.model_validate(r._asdict()) for r in rows]


def credential_ids(conn: Connection, user_id: UUID) -> list[bytes]:
    """For exclude_credentials, so an authenticator is not registered twice."""
    query = select(auth_passkey.c.credential_id).where(auth_passkey.c.user_id == user_id)
    return list(conn.execute(query).scalars())


def add_passkey(
    conn: Connection,
    *,
    workspace_id: UUID,
    user_id: UUID,
    credential: NewCredential,
    label: str,
    now: datetime,
) -> Passkey:
    """Store a verified credential. A credential id registered already (in any workspace) is
    a DuplicateCredentialError; the savepoint keeps the caller's transaction usable."""
    try:
        with conn.begin_nested():
            row = conn.execute(
                insert(auth_passkey)
                .values(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    credential_id=credential.credential_id,
                    public_key=credential.public_key,
                    sign_count=credential.sign_count,
                    aaguid=credential.aaguid,
                    transports=credential.transports,
                    backup_eligible=credential.backup_eligible,
                    backed_up=credential.backed_up,
                    label=label,
                    created_at=now,
                )
                .returning(*_PASSKEY_COLUMNS)
            ).one()
    except IntegrityError:
        # The DBAPI message names the offending value; it is deliberately not kept (§10.5).
        raise DuplicateCredentialError("credential is registered already") from None
    return Passkey.model_validate(row._asdict())


def rename_passkey(conn: Connection, user_id: UUID, passkey_id: UUID, label: str) -> Passkey:
    t = auth_passkey
    row = conn.execute(
        update(t)
        .where(t.c.id == passkey_id, t.c.user_id == user_id)
        .values(label=label)
        .returning(*_PASSKEY_COLUMNS)
    ).one_or_none()
    if row is None:
        raise PasskeyNotFoundError("passkey not found")
    return Passkey.model_validate(row._asdict())


def delete_passkey(
    conn: Connection,
    user_id: UUID,
    passkey_id: UUID,
    *,
    now: datetime,
    allow_last: bool = False,
) -> None:
    """Delete one of the user's passkeys and end the sessions it opened (§10.5), in the
    caller's transaction. Refuses the user's last passkey unless allow_last (the CLI's
    recovery path, §6.7). The user's passkeys are locked first, so two concurrent deletes
    cannot both see "one left"."""
    t = auth_passkey
    mine = set(
        conn.execute(select(t.c.id).where(t.c.user_id == user_id).with_for_update()).scalars()
    )
    if passkey_id not in mine:
        raise PasskeyNotFoundError("passkey not found")
    if len(mine) == 1 and not allow_last:
        raise LastPasskeyError("the last passkey cannot be deleted")
    end_passkey_sessions(conn, passkey_id, now=now)
    conn.execute(delete(t).where(t.c.id == passkey_id))


def record_assertion(
    conn: Connection, passkey_id: UUID, new_sign_count: int, now: datetime
) -> bool:
    """§10.5: the atomic sign counter. Accepts the assertion only if its counter is above the
    stored one, in the same UPDATE that stores it, so of two concurrent assertions with the
    same counter exactly one wins; False means a possible cloned authenticator.

    Authenticators that do not count (synced passkeys always report 0) stay at 0 and are
    accepted with 0; py_webauthn has already refused 0 for a credential whose stored counter
    is above 0."""
    t = auth_passkey
    counts = t.c.sign_count < new_sign_count if new_sign_count > 0 else t.c.sign_count == 0
    result = conn.execute(
        update(t)
        .where(t.c.id == passkey_id, counts)
        .values(sign_count=new_sign_count, last_used_at=now)
    )
    return result.rowcount == 1
