"""A bank login's PIN typed into the app and the login name, both stored encrypted
(docs/phases/phase-6.md D196).

The rule: a bank PIN is never stored in readable form. It is a container secret (D119, D163)
or a `bank_credential` row; the login name is `bank_connection.login_ciphertext`. Neither is
ever shown, logged, put into an event, a job payload or a read model, nor returned.

Keys (no new secret): HKDF-SHA256 over the session key (Settings.session_key), no salt,
info `hausbuch/fints-pin/v1` for PINs and `hausbuch/fints-login/v1` for logins. The client
state's MAC key (services.bank_sync.state_key, D119) is derived from the same session key by
one labelled HMAC; it is left as it is, so the stored state of a live connection keeps
verifying. Each row stores KEY_VERSION; a row of another version, or one the key no longer
opens (the session key was rotated), is unreadable: the sync fails with bank.pin_unreadable or
bank.login_unreadable and the card asks to type it again. Never a crash, never a log line with
the ciphertext.

Cipher: AES-256-GCM with a random 96-bit nonce per write. The AAD binds a PIN to its workspace
and slot, a login to its workspace and connection id, rebuilt from the row's own columns: a
ciphertext copied to another row or workspace does not decrypt.

A PIN is a secret, not history: setting it replaces the row, deleting it removes it. Each act
is an event (workspace.bank_pin_set, workspace.bank_pin_deleted) naming the slot and its actor
only. A readable login (a connection made before migration 0039) is encrypted at the API's
start-up (encrypt_all_logins) and at the first sync or change of its connection
(encrypt_login_if_readable), the readable column nulled in the same UPDATE.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, Row, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_credential as credential_table
from hausbuch.rules import bank_sync as rules
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.bank_sync import AppCredential, CredentialSource
from hausbuch.schemas.events import BankPinChanged, EventType
from hausbuch.settings import Settings

from .allocations import user_id
from .context import CommandContext
from .events import append_event

logger = logging.getLogger("hausbuch.services.bank_credentials")

PIN_INFO = b"hausbuch/fints-pin/v1"
LOGIN_INFO = b"hausbuch/fints-login/v1"
KEY_VERSION = 1
NONCE_BYTES = 12  # 96 bits, AES-GCM's standard nonce

# The login columns a read model never carries (with login_name, services.bank_sync).
LOGIN_COLUMNS = frozenset({"login_name", "login_nonce", "login_ciphertext", "login_key_version"})


# --- Keys and the cipher ---------------------------------------------------------------------


def derive_key(session_key: bytes, info: bytes) -> bytes:
    """A 256-bit key for one use, derived from the session key (HKDF-SHA256, no salt)."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(session_key)


def pin_key(settings: Settings) -> bytes:
    return derive_key(settings.session_key, PIN_INFO)


def login_key(settings: Settings) -> bytes:
    return derive_key(settings.session_key, LOGIN_INFO)


def _aad(*parts: str) -> bytes:
    """The parts, each length-prefixed, so no two tuples share an AAD."""
    out = bytearray()
    for part in parts:
        raw = part.encode()
        out += len(raw).to_bytes(4, "big") + raw
    return bytes(out)


def pin_aad(workspace_id: UUID, slot: str) -> bytes:
    return _aad("pin", str(workspace_id), slot)


def login_aad(workspace_id: UUID, connection_id: UUID) -> bytes:
    return _aad("login", str(workspace_id), str(connection_id))


@dataclass(frozen=True)
class Sealed:
    """A ciphertext with its nonce; repr shows neither."""

    nonce: bytes
    ciphertext: bytes

    def __repr__(self) -> str:
        return "Sealed(…)"


def seal(key: bytes, plaintext: str, aad: bytes) -> Sealed:
    nonce = os.urandom(NONCE_BYTES)
    return Sealed(nonce, AESGCM(key).encrypt(nonce, plaintext.encode(), aad))


def unseal(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> str | None:
    """The plaintext, or None when the key, the AAD or the bytes do not match."""
    try:
        return AESGCM(key).decrypt(bytes(nonce), bytes(ciphertext), aad).decode()
    except (InvalidTag, ValueError):  # ValueError: a malformed nonce, or not UTF-8
        return None


# --- The PIN typed into the app ---------------------------------------------------------------


def _credential_row(conn: Connection, slot: str) -> Row | None:
    return conn.execute(
        select(credential_table).where(credential_table.c.credential_slot == slot)
    ).one_or_none()


def _open_pin(row: Row, settings: Settings) -> str | None:
    if row.key_version != KEY_VERSION:
        return None
    aad = pin_aad(row.workspace_id, row.credential_slot)
    return unseal(pin_key(settings), row.nonce, row.ciphertext, aad)


def set_pin(ctx: CommandContext, slot: str, pin: SecretStr, *, settings: Settings) -> AppCredential:
    """Store (or replace) the slot's PIN, encrypted; one event naming the slot. The caller
    validated the slot (the path pattern) and the PIN's length (BankPinSet)."""
    if not rules.valid_slot(slot):
        raise RuleError(E.INVALID, "a credential slot is lower-case letters, digits and _")
    sealed = seal(pin_key(settings), pin.get_secret_value(), pin_aad(ctx.workspace_id, slot))
    values = {
        "nonce": sealed.nonce,
        "ciphertext": sealed.ciphertext,
        "key_version": KEY_VERSION,
        "set_by": user_id(ctx),
        "updated_at": ctx.now,
    }
    row = ctx.conn.execute(
        pg_insert(credential_table)
        .values(workspace_id=ctx.workspace_id, credential_slot=slot, created_at=ctx.now, **values)
        .on_conflict_do_update(index_elements=["workspace_id", "credential_slot"], set_=values)
        .returning(credential_table.c.credential_slot, credential_table.c.updated_at)
    ).one()
    append_event(
        ctx,
        EventType.WORKSPACE_BANK_PIN_SET,
        ctx.workspace_id,
        BankPinChanged(credential_slot=slot),
    )
    return AppCredential(
        slot=row.credential_slot, set_at=row.updated_at, used=_slot_used(ctx.conn, slot)
    )


def _slot_used(conn: Connection, slot: str) -> bool:
    """Whether a connection of the workspace names the slot (RLS)."""
    named = select(connection_table.c.id).where(connection_table.c.credential_slot == slot)
    return conn.execute(named.limit(1)).first() is not None


def delete_pin(ctx: CommandContext, slot: str) -> bool:
    """Delete the slot's PIN typed into the app; the container variable, if any, applies
    again. False (and no event) when none was stored."""
    deleted = ctx.conn.execute(
        delete(credential_table)
        .where(credential_table.c.credential_slot == slot)
        .returning(credential_table.c.id)
    ).one_or_none()
    if deleted is None:
        return False
    append_event(
        ctx,
        EventType.WORKSPACE_BANK_PIN_DELETED,
        ctx.workspace_id,
        BankPinChanged(credential_slot=slot),
    )
    return True


def app_pins(conn: Connection, settings: Settings) -> list[AppCredential]:
    """The slots whose PIN was typed into the app, with when, whether it still decrypts and
    whether a connection of the workspace uses the slot (a PIN saved under a slot nothing
    uses is shown so it can be deleted; it is never deleted by itself)."""
    used = set(conn.execute(select(connection_table.c.credential_slot)).scalars())
    rows = conn.execute(select(credential_table).order_by(credential_table.c.credential_slot))
    return [
        AppCredential(
            slot=r.credential_slot,
            set_at=r.updated_at,
            readable=_open_pin(r, settings) is not None,
            used=r.credential_slot in used,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class PinState:
    """Where a slot's PIN comes from (never the PIN)."""

    source: CredentialSource
    set_at: datetime | None = None
    readable: bool = True


def pin_state(conn: Connection, slot: str, settings: Settings) -> PinState:
    """D196's precedence: the PIN typed into the app, then the slot's container secret."""
    row = _credential_row(conn, slot)
    if row is not None:
        return PinState(CredentialSource.APP, row.updated_at, _open_pin(row, settings) is not None)
    if slot in settings.fints_pins:
        return PinState(CredentialSource.SERVER)
    return PinState(CredentialSource.NONE)


def available_slots(conn: Connection, settings: Settings) -> set[str]:
    """The slots with a PIN from either source (names only), for rules.check_slot."""
    stored = conn.execute(select(credential_table.c.credential_slot)).scalars()
    return set(stored) | set(settings.fints_pins)


def resolve_pin(conn: Connection, slot: str, settings: Settings) -> str:
    """The slot's PIN for a sync, in process only: the one typed into the app, else the
    container secret. bank.pin_unreadable when a stored PIN no longer decrypts (the stored one
    still wins: falling back silently would use a PIN the person replaced);
    bank.pin_missing when neither exists. The value never leaves the caller's frame into a
    log, an event, a payload or a row."""
    row = _credential_row(conn, slot)
    if row is not None:
        pin = _open_pin(row, settings)
        if pin is None:
            raise RuleError(E.BANK_PIN_UNREADABLE, "the stored PIN no longer decrypts")
        return pin
    rules.check_slot(set(settings.fints_pins), slot)  # bank.pin_missing, naming the variable
    return settings.fints_pins[slot]


# --- The login name -------------------------------------------------------------------------


def sealed_login(
    settings: Settings, workspace_id: UUID, connection_id: UUID, login: str
) -> dict[str, Any]:
    """The bank_connection values that store `login` encrypted (and no readable login)."""
    sealed = seal(login_key(settings), login, login_aad(workspace_id, connection_id))
    return {
        "login_name": None,
        "login_nonce": sealed.nonce,
        "login_ciphertext": sealed.ciphertext,
        "login_key_version": KEY_VERSION,
    }


def connection_login(row: Row | Any, settings: Settings) -> str | None:
    """The connection's login in process: the readable column of a row not yet encrypted,
    else the decrypted one; None when it no longer decrypts."""
    if row.login_name is not None:
        return row.login_name
    if row.login_ciphertext is None or row.login_key_version != KEY_VERSION:
        return None
    aad = login_aad(row.workspace_id, row.id)
    return unseal(login_key(settings), row.login_nonce, row.login_ciphertext, aad)


def encrypt_login_if_readable(conn: Connection, row: Row, settings: Settings) -> Row:
    """Encrypt a connection's readable login in place (the readable column nulled in the same
    UPDATE); the row as it is now. A no-op for a row already encrypted. No event: the login
    does not change, only how it is stored.

    `row` may be stale (a sync read it before a member's change of the login committed): the
    UPDATE applies only while the stored login is still readable and still this one (so
    never NULL), so it never seals an old login over a new one; otherwise the row is read
    again and returned as it now is."""
    if row.login_name is None:
        return row
    updated = conn.execute(
        update(connection_table)
        .where(connection_table.c.id == row.id, connection_table.c.login_name == row.login_name)
        .values(**sealed_login(settings, row.workspace_id, row.id, row.login_name))
        .returning(connection_table)
    ).one_or_none()
    if updated is not None:
        return updated
    return conn.execute(select(connection_table).where(connection_table.c.id == row.id)).one()


def encrypt_readable_logins(conn: Connection, settings: Settings) -> int:
    """Encrypt every readable login of the current workspace (RLS); how many."""
    rows = conn.execute(
        select(connection_table).where(connection_table.c.login_name.is_not(None)).with_for_update()
    ).all()
    for row in rows:
        encrypt_login_if_readable(conn, row, settings)
    return len(rows)


def encrypt_all_logins(engine: Engine, settings: Settings) -> int:
    """At the API's start-up (D196): every provisioned workspace's readable logins, one
    transaction per workspace. Never raises: a failure is logged by its class name only (a
    driver message can quote parameters) and the next start-up or sync tries again."""
    from hausbuch.auth.lookup import provisioned_workspace_ids
    from hausbuch.db.session import workspace_transaction

    encrypted = failed = 0
    try:
        workspace_ids = sorted(provisioned_workspace_ids(engine))
    except Exception as exc:
        _log_logins(0, 1, type(exc).__name__)
        return 0
    error: str | None = None
    for workspace_id in workspace_ids:
        try:
            with workspace_transaction(engine, workspace_id) as conn:
                encrypted += encrypt_readable_logins(conn, settings)
        except Exception as exc:
            failed += 1
            error = type(exc).__name__
    _log_logins(encrypted, failed, error)
    return encrypted


def _log_logins(encrypted: int, failed: int, error: str | None) -> None:
    fields: dict[str, Any] = {"event": "bank_logins_encrypted", "encrypted": encrypted}
    if failed:
        fields |= {"failed_workspaces": failed, "exc_type": error}
    level = logging.WARNING if failed else logging.INFO
    logger.log(level, "bank logins encrypted", extra={"fields": fields})
