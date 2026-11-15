"""A bank login's PIN typed into the app and the login name, both stored encrypted
(docs/phases/phase-6.md D196).

The rule: a bank PIN is never stored in readable form. It is a container secret (D119, D163)
or a `bank_credential` row; the login name is `bank_connection.login_ciphertext`. Neither is
ever shown, logged, put into an event, a job payload or a read model, nor returned.

Keys (no new secret): HKDF-SHA256 over the session key (Settings.session_key), no salt,
info `hausbuch/fints-pin/v1` for PINs and `hausbuch/fints-login/v1` for logins. The client
state's MAC key (services.bank_sync.state_key, D119) is derived from the same session key by
one labelled HMAC; it is left as it is, so the stored state of a live connection keeps
verifying. Each row stores its key version (a login 1, a PIN 2 since the server binding
below); a row of a version this build lacks, or one the key no longer
opens (the session key was rotated), is unreadable: the sync fails with bank.pin_unreadable or
bank.login_unreadable and the card asks to type it again. Never a crash, never a log line with
the ciphertext.

Cipher: AES-256-GCM with a random 96-bit nonce per write. The AAD binds a PIN to its workspace
and slot, a login to its workspace and connection id, rebuilt from the row's own columns: a
ciphertext copied to another row or workspace does not decrypt.

A PIN is sent only to the server it was typed for (D196 amended, 16 Nov 2026): whoever can
edit a bank connection could otherwise change its FinTS address and make the next sync send
the stored PIN to another host. The PUT that carries the PIN names the FinTS address it is
for; the row (key version 2, info `hausbuch/fints-pin/v2`) seals the SHA-256 of that address,
normalised (server_address: a plain https address by its host, port and path, any other
shape verbatim), before the PIN, so the binding is authenticated by the same tag and needs
no column. A sync compares it with the address it is about to dial and refuses with
bank.pin_server_changed before any request when they differ: the PIN must be typed again
for the new address. A row of key version 1 (typed before the binding; none on the live
data, checked 15 Nov 2026) names no address and is refused the same way, never bound at its
first use. Only the PIN typed into the app is bound; a container secret is the operator's
and follows the connection.

A PIN is a secret, not history: setting it replaces the row, deleting it removes it. Each act
is an event (workspace.bank_pin_set, workspace.bank_pin_deleted) naming the slot and its actor
only. A readable login (a connection made before migration 0039) is encrypted at the API's
start-up (encrypt_all_logins) and at the first sync or change of its connection
(encrypt_login_if_readable), the readable column nulled in the same UPDATE.
"""

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
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

PIN_INFO = b"hausbuch/fints-pin/v1"  # key version 1: the PIN alone (D196 as first built)
PIN_INFO_V2 = b"hausbuch/fints-pin/v2"  # key version 2: the server's hash, then the PIN
LOGIN_INFO = b"hausbuch/fints-login/v1"
KEY_VERSION = 1  # the login's, and a PIN row typed before the server binding
PIN_KEY_VERSION = 2  # every PIN written since D196 amended (16 Nov 2026)
NONCE_BYTES = 12  # 96 bits, AES-GCM's standard nonce
SERVER_HASH_BYTES = 32  # SHA-256 of server_address

# The login columns a read model never carries (with login_name, services.bank_sync).
LOGIN_COLUMNS = frozenset({"login_name", "login_nonce", "login_ciphertext", "login_key_version"})


# --- Keys and the cipher ---------------------------------------------------------------------


def derive_key(session_key: bytes, info: bytes) -> bytes:
    """A 256-bit key for one use, derived from the session key (HKDF-SHA256, no salt)."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(session_key)


def pin_key(settings: Settings, version: int = PIN_KEY_VERSION) -> bytes:
    info = PIN_INFO_V2 if version == PIN_KEY_VERSION else PIN_INFO
    return derive_key(settings.session_key, info)


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


# --- The server a PIN is for (D196 amended, 16 Nov 2026) --------------------------------------


# A plain https address: a host of letters, digits, dots and hyphens (or a bracketed IPv6
# literal), an optional numeric port, and a path of ordinary characters; no user info, no
# backslash, no query, no fragment. Only this shape is normalised: on it every URL parser
# (urlsplit here, urllib3 under python-fints' requests session) reads the same host.
_PLAIN_ADDRESS = re.compile(
    r"(?i:https)://(?P<host>[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::(?P<port>[0-9]{1,5}))?"
    r"(?P<path>/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*)?"
)


def server_address(url: str) -> str:
    """The FinTS address a PIN is bound to. A plain https address (_PLAIN_ADDRESS) becomes
    `https://<host lower-cased>:<port, 443 when none>` plus its path ("/" when empty), so
    the same server written another way stays the same server. Anything else (user info, a
    backslash, a query, a fragment, a port above 65535) is bound verbatim and can only
    equal itself: parsers disagree on such URLs (urlsplit reads
    `https://a.example\\@b.example/` as host b.example, urllib3 dials a.example), and a
    normalised form must never let a changed address hash like the bound one."""
    raw = url.strip()
    plain = _PLAIN_ADDRESS.fullmatch(raw)
    if plain is not None:
        port = int(plain["port"] or 443)
        if port <= 65535:
            # urlsplit agrees with the regex on this shape; it lower-cases the host
            host = (urlsplit(raw).hostname or "").lower()
            if ":" in host:
                host = f"[{host}]"  # an IPv6 literal keeps its brackets
            return f"https://{host}:{port}{plain['path'] or '/'}"
    return f"verbatim {raw}"  # never starts with https://, so never equals a plain one


def server_hash(url: str) -> bytes:
    return hashlib.sha256(server_address(url).encode()).digest()


# --- The PIN typed into the app ---------------------------------------------------------------


def _credential_row(conn: Connection, slot: str) -> Row | None:
    return conn.execute(
        select(credential_table).where(credential_table.c.credential_slot == slot)
    ).one_or_none()


@dataclass(frozen=True)
class _OpenedPin:
    """A stored PIN, decrypted, and the hash of the server it was typed for (None: a row of
    key version 1, typed before the binding); repr shows neither."""

    pin: str
    server: bytes | None

    def __repr__(self) -> str:
        return "_OpenedPin(…)"


def _open_pin(row: Row, settings: Settings) -> _OpenedPin | None:
    """The row's PIN and server, or None when it does not decrypt (a key version this build
    does not know, a rotated session key, a row moved to another slot or workspace)."""
    aad = pin_aad(row.workspace_id, row.credential_slot)
    if row.key_version == KEY_VERSION:
        pin = unseal(pin_key(settings, KEY_VERSION), row.nonce, row.ciphertext, aad)
        return None if pin is None else _OpenedPin(pin, None)
    if row.key_version != PIN_KEY_VERSION:
        return None
    try:
        raw = AESGCM(pin_key(settings)).decrypt(bytes(row.nonce), bytes(row.ciphertext), aad)
        if len(raw) <= SERVER_HASH_BYTES:
            return None
        return _OpenedPin(raw[SERVER_HASH_BYTES:].decode(), raw[:SERVER_HASH_BYTES])
    except (InvalidTag, ValueError):  # ValueError: a malformed nonce, or not UTF-8
        return None


def set_pin(
    ctx: CommandContext, slot: str, pin: SecretStr, *, fints_url: str, settings: Settings
) -> AppCredential:
    """Store (or replace) the slot's PIN, encrypted and bound to the FinTS address it was
    typed for (`fints_url`, from the same request, never read from a connection, so a later
    edit of a connection cannot re-bind it); one event naming the slot only. The caller
    validated the slot (the path pattern), the PIN's length and the address (BankPinSet)."""
    if not rules.valid_slot(slot):
        raise RuleError(E.INVALID, "a credential slot is lower-case letters, digits and _")
    nonce = os.urandom(NONCE_BYTES)
    plaintext = server_hash(fints_url) + pin.get_secret_value().encode()
    ciphertext = AESGCM(pin_key(settings)).encrypt(
        nonce, plaintext, pin_aad(ctx.workspace_id, slot)
    )
    values = {
        "nonce": nonce,
        "ciphertext": ciphertext,
        "key_version": PIN_KEY_VERSION,
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
    """Where a slot's PIN comes from (never the PIN). server_matches is False when the PIN
    typed into the app is readable but was typed for another FinTS address than the one
    asked about, or before the binding, so a sync refuses it (D196 amended)."""

    source: CredentialSource
    set_at: datetime | None = None
    readable: bool = True
    server_matches: bool = True


def pin_state(
    conn: Connection, slot: str, settings: Settings, *, fints_url: str | None = None
) -> PinState:
    """D196's precedence: the PIN typed into the app, then the slot's container secret. With
    `fints_url` (a connection's address), whether the app's PIN is bound to that address."""
    row = _credential_row(conn, slot)
    if row is not None:
        opened = _open_pin(row, settings)
        matches = fints_url is None or opened is None or opened.server == server_hash(fints_url)
        return PinState(CredentialSource.APP, row.updated_at, opened is not None, matches)
    if slot in settings.fints_pins:
        return PinState(CredentialSource.SERVER)
    return PinState(CredentialSource.NONE)


def available_slots(conn: Connection, settings: Settings) -> set[str]:
    """The slots with a PIN from either source (names only), for rules.check_slot."""
    stored = conn.execute(select(credential_table.c.credential_slot)).scalars()
    return set(stored) | set(settings.fints_pins)


def resolve_pin(conn: Connection, slot: str, settings: Settings, *, fints_url: str) -> str:
    """The slot's PIN for a sync to `fints_url` (the address the dialog is about to dial), in
    process only: the one typed into the app, else the container secret.
    bank.pin_unreadable when a stored PIN no longer decrypts (the stored one still wins:
    falling back silently would use a PIN the person replaced); bank.pin_server_changed when
    the stored PIN was typed for another address or before the binding (D196 amended: never
    sent elsewhere, and never silently replaced by the container secret either);
    bank.pin_missing when neither exists. The PIN never leaves the caller's frame into a
    log, an event, a payload or a row, and neither address is named in the error."""
    row = _credential_row(conn, slot)
    if row is not None:
        opened = _open_pin(row, settings)
        if opened is None:
            raise RuleError(E.BANK_PIN_UNREADABLE, "the stored PIN no longer decrypts")
        if opened.server is None or opened.server != server_hash(fints_url):
            raise RuleError(
                E.BANK_PIN_SERVER_CHANGED, "the stored PIN was typed for another FinTS address"
            )
        return opened.pin
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
