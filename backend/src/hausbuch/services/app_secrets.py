"""Provider keys and URLs typed into the app, stored encrypted (docs/phases/phase-6.md D260).

The rule (D196's, for a closed allowlist of third-party keys, schemas.app_secrets): a value is
kept either as a container variable or encrypted in `app_secret` under a key that exists only
in the API container. It is never shown, logged, put into an event, a job payload or a read
model, nor returned by any endpoint; a reader gets where it comes from, never what it is.

Keys: HKDF-SHA256 over the session key (Settings.session_key), no salt, info
`hausbuch/app-secret/v1` (bank_credentials.derive_key, unchanged). Cipher: AES-256-GCM with a
random 96-bit nonce per write; the AAD is the workspace id and the name, rebuilt from the
row's own columns, so a ciphertext copied to another row or workspace does not decrypt.

Precedence, resolved when a job or request runs and never cached into a payload: the value
typed in the app, else the Settings field of the same name (the container variable). A row
that no longer decrypts (the session key was rotated) or of a key version this build lacks is
`unreadable`: the consumer gets nothing, never the container variable, because a person typed
a value on purpose and replacing it silently would use one they replaced; the card says
„Schlüssel kann nicht gelesen werden, bitte neu eintragen“. Never a crash, never a log line
with the ciphertext.

A secret, not history: setting it replaces the row, deleting it removes it; each act is an
event (workspace.app_secret_set, workspace.app_secret_deleted) naming the name and its actor.
"""

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Connection, Row, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch.db.tables import app_secret as secret_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.app_secrets import (
    AppSecretKind,
    AppSecretName,
    AppSecretSource,
    AppSecretState,
)
from hausbuch.schemas.events import AppSecretChanged, EventType
from hausbuch.settings import (
    DEFAULT_BSC_RPC_URL,
    SOLANA_PUBLIC_RPC_URL,
    Settings,
    _is_http_url,
)

from .allocations import user_id
from .bank_credentials import NONCE_BYTES, derive_key, seal, unseal
from .context import CommandContext
from .events import append_event

N = AppSecretName

INFO = b"hausbuch/app-secret/v1"
KEY_VERSION = 1
MAX_LENGTH = 512  # characters, printable ASCII: the ciphertext CHECK's 528 bytes with the tag

# A URL's public default (settings.py): the container's value when no variable is set, so
# it reads as „nicht gesetzt“, the public endpoint in use.
_URL_DEFAULTS = {N.SOLANA_RPC_URL: SOLANA_PUBLIC_RPC_URL, N.BSC_RPC_URL: DEFAULT_BSC_RPC_URL}

# The Settings fields typed SecretStr; every other allowlisted field is a plain str.
SECRET_STR_FIELDS = frozenset(
    {N.ETHERSCAN_KEY, N.SOLANA_RPC_URL, N.BSC_RPC_URL, N.HELIUS_KEY, N.COINGECKO_KEY}
)

# (label, what Hausbuch uses it for), in the card's order.
TEXTS: dict[AppSecretName, tuple[str, str]] = {
    N.ANTHROPIC_API_KEY: (
        "Anthropic (Claude)",
        "Liest hochgeladene Dokumente und schlägt Kategorien für Umsätze vor (KI).",
    ),
    N.ETHERSCAN_KEY: ("Etherscan", "Liest Ethereum- und Arbitrum-Adressen."),
    N.SOLANA_RPC_URL: (
        "Solana-RPC-Adresse",
        "Liest Solana-Adressen. Ohne Eintrag nutzt Hausbuch den öffentlichen Zugang.",
    ),
    N.BSC_RPC_URL: (
        "BNB-Smart-Chain-RPC-Adresse",
        "Liest Kontostände auf BNB Smart Chain. Ohne Eintrag nutzt Hausbuch den öffentlichen "
        "Zugang.",
    ),
    N.HELIUS_KEY: (
        "Helius",
        "Kurze Beschreibungen von Solana-Transaktionen; wird zurzeit noch nicht genutzt.",
    ),
    N.COINGECKO_KEY: (
        "CoinGecko",
        "Tägliche Kurse der Coins. Ohne Schlüssel kommen sie nur von Kraken.",
    ),
}


# --- Keys and the cipher ---------------------------------------------------------------------


def secret_key(settings: Settings) -> bytes:
    return derive_key(settings.session_key, INFO)


def secret_aad(workspace_id: UUID, name: str) -> bytes:
    """The parts, each length-prefixed (bank_credentials' scheme, its own tag)."""
    out = bytearray()
    for part in ("app-secret", str(workspace_id), name):
        raw = part.encode()
        out += len(raw).to_bytes(4, "big") + raw
    return bytes(out)


def _open(row: Row, settings: Settings) -> str | None:
    """The row's value, or None when it does not decrypt (a key version this build lacks, a
    rotated session key, a row moved to another name or workspace)."""
    if row.key_version != KEY_VERSION:
        return None
    aad = secret_aad(row.workspace_id, row.name)
    return unseal(secret_key(settings), row.nonce, row.ciphertext, aad)


# --- Resolution --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """Where a name's value comes from and the value to use (None: none, or unreadable);
    repr shows no value."""

    source: AppSecretSource
    value: SecretStr | None = dataclasses.field(default=None, repr=False)
    updated_at: datetime | None = None


def container_value(settings: Settings, name: AppSecretName) -> SecretStr | None:
    """The Settings field of the same name, None when unset or a URL's public default."""
    value = getattr(settings, name.value)
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if not value or _URL_DEFAULTS.get(name) == value:
        return None
    return SecretStr(value)


def _rows(conn: Connection) -> dict[str, Row]:
    return {r.name: r for r in conn.execute(select(secret_table))}


def _resolved(row: Row | None, settings: Settings, name: AppSecretName) -> Resolved:
    if row is not None:
        value = _open(row, settings)
        if value is None:
            return Resolved(AppSecretSource.UNREADABLE, None, row.updated_at)
        return Resolved(AppSecretSource.APP, SecretStr(value), row.updated_at)
    container = container_value(settings, name)
    if container is not None:
        return Resolved(AppSecretSource.CONTAINER, container)
    return Resolved(AppSecretSource.NONE)


def lookup(conn: Connection, settings: Settings, name: AppSecretName) -> Resolved:
    """The workspace's (RLS) value of `name`: typed in the app, else the container's."""
    row = conn.execute(select(secret_table).where(secret_table.c.name == name.value)).one_or_none()
    return _resolved(row, settings, name)


def resolve(conn: Connection, settings: Settings, name: AppSecretName) -> SecretStr | None:
    """The value to use, in process only; None when neither exists or the typed one is
    unreadable (never the container's then: lookup() tells the two apart)."""
    return lookup(conn, settings, name).value


class SecretUnreadable(Exception):
    """A value typed in the app no longer decrypts; names the secret only."""

    def __init__(self, name: AppSecretName) -> None:
        super().__init__(f"{name.value} typed in the app no longer decrypts")
        self.name = name


def workspace_settings(
    conn: Connection, settings: Settings, names: Iterable[AppSecretName]
) -> Settings:
    """`settings` with each of `names` typed in the app put in place of the container's, for
    a factory that reads Settings (the chain sources). Raises SecretUnreadable for a typed
    value that no longer decrypts. The copy lives in the caller's frame only."""
    changes: dict[str, object] = {}
    rows = _rows(conn)
    for name in names:
        found = _resolved(rows.get(name.value), settings, name)
        if found.source is AppSecretSource.UNREADABLE:
            raise SecretUnreadable(name)
        if found.source is AppSecretSource.APP:
            assert found.value is not None
            # each Settings field's own type, declared, never read off its current value
            # (an unset field is None either way)
            secret_str = name in SECRET_STR_FIELDS
            changes[name.value] = found.value if secret_str else found.value.get_secret_value()
    return dataclasses.replace(settings, **changes) if changes else settings


# --- The card --------------------------------------------------------------------------------


def secret_states(conn: Connection, settings: Settings) -> list[AppSecretState]:
    """Every allowlisted name with where its value comes from; never the value."""
    rows = _rows(conn)
    out = []
    for name in N:
        found = _resolved(rows.get(name.value), settings, name)
        label, purpose = TEXTS[name]
        out.append(
            AppSecretState(
                name=name,
                label=label,
                purpose=purpose,
                kind=AppSecretKind.URL if name.is_url else AppSecretKind.KEY,
                variable=name.variable,
                source=found.source,
                updated_at=found.updated_at,
            )
        )
    return out


def _state(conn: Connection, settings: Settings, name: AppSecretName) -> AppSecretState:
    return next(s for s in secret_states(conn, settings) if s.name is name)


# --- Set and delete --------------------------------------------------------------------------


def checked_value(name: AppSecretName, value: SecretStr) -> str:
    """The value to store: trimmed, 1 to 512 printable ASCII characters (no blank or control
    character inside), and a URL an http(s) URL with a host, exactly as Settings checks the
    container variable (settings._is_http_url). Fixed messages: never the value."""
    plain = value.get_secret_value().strip()
    if not plain or len(plain) > MAX_LENGTH or not all("!" <= c <= "~" for c in plain):
        raise RuleError(
            E.APP_SECRET_INVALID,
            f"a key is 1 to {MAX_LENGTH} printable characters without blanks",
        )
    if name.is_url and not _is_http_url(plain):
        raise RuleError(E.APP_SECRET_URL_INVALID, "the address must be an http(s) URL with a host")
    return plain


def set_secret(
    ctx: CommandContext, name: AppSecretName, value: SecretStr, *, settings: Settings
) -> AppSecretState:
    """Store (or replace) `name`, encrypted; one event naming the name only."""
    plain = checked_value(name, value)
    sealed = seal(secret_key(settings), plain, secret_aad(ctx.workspace_id, name.value))
    assert len(sealed.nonce) == NONCE_BYTES
    values = {
        "nonce": sealed.nonce,
        "ciphertext": sealed.ciphertext,
        "key_version": KEY_VERSION,
        "set_by": user_id(ctx),
        "updated_at": ctx.now,
    }
    ctx.conn.execute(
        pg_insert(secret_table)
        .values(workspace_id=ctx.workspace_id, name=name.value, created_at=ctx.now, **values)
        .on_conflict_do_update(index_elements=["workspace_id", "name"], set_=values)
    )
    append_event(
        ctx,
        EventType.WORKSPACE_APP_SECRET_SET,
        ctx.workspace_id,
        AppSecretChanged(name=name),
    )
    return _state(ctx.conn, settings, name)


def delete_secret(ctx: CommandContext, name: AppSecretName) -> bool:
    """Delete `name` typed in the app; the container variable, if any, applies again. False
    (and no event) when none was stored."""
    deleted = ctx.conn.execute(
        delete(secret_table).where(secret_table.c.name == name.value).returning(secret_table.c.id)
    ).one_or_none()
    if deleted is None:
        return False
    append_event(
        ctx,
        EventType.WORKSPACE_APP_SECRET_DELETED,
        ctx.workspace_id,
        AppSecretChanged(name=name),
    )
    return True


__all__ = [
    "INFO",
    "KEY_VERSION",
    "Resolved",
    "SecretUnreadable",
    "checked_value",
    "container_value",
    "delete_secret",
    "lookup",
    "resolve",
    "secret_states",
    "set_secret",
    "workspace_settings",
]
