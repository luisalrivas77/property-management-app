"""Provider keys, URLs and service settings typed into the app, stored encrypted
(docs/phases/phase-6.md D260).

The rule (D196's, for a closed allowlist, SPECS below): a value is kept either as a container
variable or encrypted in `app_secret` under a key that exists only in the API container. A
secret is never shown, logged, put into an event, a job payload or a read model, nor returned
by any endpoint; a reader gets where it comes from, never what it is. The few names that are
not secret (the TEM project and sender, the inbox's user, address and label) are encrypted
the same way and returned on the card only.

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
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Connection, Row, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch import i18n
from hausbuch.db.tables import app_secret as secret_table
from hausbuch.rules import errors as E
from hausbuch.rules.bank_sync import product_id_usable
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.app_secrets import (
    AppSecretGroup,
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
MAX_BYTES = 512  # UTF-8: the ciphertext CHECK's 528 bytes with the 16-byte tag

# A URL's public default (settings.py): the container's value when no variable is set, so
# it reads as „nicht gesetzt“, the public endpoint in use.
_URL_DEFAULTS = {N.SOLANA_RPC_URL: SOLANA_PUBLIC_RPC_URL, N.BSC_RPC_URL: DEFAULT_BSC_RPC_URL}

# The Settings fields typed SecretStr; every other allowlisted field is a plain str.
SECRET_STR_FIELDS = frozenset(
    {N.ETHERSCAN_KEY, N.SOLANA_RPC_URL, N.BSC_RPC_URL, N.HELIUS_KEY, N.COINGECKO_KEY}
)


def _token(value: str) -> bool:
    """A key, an id or a mail address: printable ASCII without a blank."""
    return all("!" <= c <= "~" for c in value)


def _text(value: str) -> bool:
    """A label or a password: no control character (a blank and an umlaut are fine)."""
    return all(c.isprintable() for c in value)


def _mail(value: str) -> bool:
    local, at, domain = value.rpartition("@")
    return _token(value) and bool(at and local and "." in domain.strip("."))


_HERSTELLER_ID = re.compile(r"[0-9]{1,10}")


def _hersteller_id(value: str) -> bool:
    """An ELSTER Hersteller-ID: a short number, ASCII digits only (ERiC's sample is 74931)."""
    return _HERSTELLER_ID.fullmatch(value) is not None


def _uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Spec:
    group: AppSecretGroup
    label: str
    purpose: str  # what Hausbuch uses it for, one plain German line
    check: Callable[[str], bool] = _token


G = AppSecretGroup
# The allowlist as one table, in the card's order (the enum's).
SPECS: dict[AppSecretName, Spec] = {
    N.ANTHROPIC_API_KEY: Spec(
        G.AI,
        "Anthropic-API-Schlüssel (Claude)",
        "Liest hochgeladene Dokumente und schlägt Kategorien für Umsätze vor.",
    ),
    N.ETHERSCAN_KEY: Spec(G.CHAINS, "Etherscan", "Liest Ethereum- und Arbitrum-Adressen."),
    N.SOLANA_RPC_URL: Spec(
        G.CHAINS,
        "Solana-RPC-Adresse",
        "Liest Solana-Adressen. Ohne Eintrag nutzt Hausbuch den öffentlichen Zugang.",
        _is_http_url,
    ),
    N.BSC_RPC_URL: Spec(
        G.CHAINS,
        "BNB-Smart-Chain-RPC-Adresse",
        "Liest Kontostände auf BNB Smart Chain. Ohne Eintrag nutzt Hausbuch den öffentlichen "
        "Zugang.",
        _is_http_url,
    ),
    N.HELIUS_KEY: Spec(
        G.CHAINS,
        "Helius",
        "Kurze Beschreibungen von Solana-Transaktionen; wird zurzeit noch nicht genutzt.",
    ),
    N.COINGECKO_KEY: Spec(
        G.CHAINS,
        "CoinGecko",
        "Tägliche Kurse der Coins. Ohne Schlüssel kommen sie nur von Kraken.",
    ),
    N.FINTS_PRODUCT_ID: Spec(
        G.BANK,
        "FinTS-Produkt-ID",
        "Die Registrierungsnummer der Deutschen Kreditwirtschaft; ohne sie ruft Hausbuch "
        "keine Kontoumsätze bei der Bank ab.",
        product_id_usable,
    ),
    N.TEM_SECRET_KEY: Spec(
        G.MAIL,
        "Scaleway-TEM-Schlüssel",
        "Versendet die tägliche Erinnerungs-E-Mail (mit Projekt-ID und Absenderadresse).",
    ),
    N.TEM_PROJECT_ID: Spec(
        G.MAIL,
        "Scaleway-Projekt-ID",
        "Das Scaleway-Projekt, über das die Erinnerungs-E-Mail versendet wird.",
        _uuid,
    ),
    N.MAIL_FROM: Spec(
        G.MAIL,
        "Absenderadresse",
        "Die Adresse der bestätigten Domain, von der die Erinnerungs-E-Mail kommt.",
        _mail,
    ),
    N.INBOX_IMAP_USER: Spec(
        G.MAIL,
        "Gmail-Konto für den E-Mail-Eingang",
        "Das Gmail-Konto, dessen Label Hausbuch liest (mit App-Passwort, Adresse und Label).",
        _mail,
    ),
    N.INBOX_IMAP_PASSWORD: Spec(
        G.MAIL,
        "Gmail-App-Passwort",
        "Das App-Passwort dieses Gmail-Kontos; Hausbuch liest damit nur das eine Label.",
        _text,
    ),
    N.INBOX_ADDRESS: Spec(
        G.MAIL,
        "Weiterleitungsadresse",
        "Die Adresse mit Token (name+token@gmail.com), an die Belege weitergeleitet werden.",
        _mail,
    ),
    N.INBOX_LABEL: Spec(
        G.MAIL,
        "Gmail-Label",
        "Das Label, in das Gmail die weitergeleiteten Belege legt.",
        _text,
    ),
    N.ELSTER_HERSTELLER_ID: Spec(
        G.ELSTER,
        "ELSTER-Hersteller-ID",
        "Die Nummer, die ELSTER Hausbuch als Software-Hersteller zugeteilt hat; nötig, sobald "
        "Hausbuch Steuererklärungen über ELSTER prüft oder sendet (noch nicht gebaut).",
        _hersteller_id,
    ),
}
assert list(SPECS) == list(N)


# English beside the German of SPECS, GROUP_LABELS and STOPPED (phase 10 D284), chosen when
# Einstellungen or Zu prüfen is read: (label, purpose) per name.
SPECS_EN: dict[AppSecretName, tuple[str, str]] = {
    N.ANTHROPIC_API_KEY: (
        "Anthropic API key (Claude)",
        "Reads uploaded documents and suggests categories for transactions.",
    ),
    N.ETHERSCAN_KEY: ("Etherscan", "Reads Ethereum and Arbitrum addresses."),
    N.SOLANA_RPC_URL: (
        "Solana RPC address",
        "Reads Solana addresses. Without an entry Hausbuch uses the public access.",
    ),
    N.BSC_RPC_URL: (
        "BNB Smart Chain RPC address",
        "Reads balances on BNB Smart Chain. Without an entry Hausbuch uses the public access.",
    ),
    N.HELIUS_KEY: (
        "Helius",
        "Short descriptions of Solana transactions; not used yet.",
    ),
    N.COINGECKO_KEY: (
        "CoinGecko",
        "Daily coin prices. Without a key they come from Kraken only.",
    ),
    N.FINTS_PRODUCT_ID: (
        "FinTS product ID",
        "The registration number of the Deutsche Kreditwirtschaft (the German banking"
        " association); without it Hausbuch fetches no transactions from the bank.",
    ),
    N.TEM_SECRET_KEY: (
        "Scaleway TEM key",
        "Sends the daily reminder email (with project ID and sender address).",
    ),
    N.TEM_PROJECT_ID: (
        "Scaleway project ID",
        "The Scaleway project through which the reminder email is sent.",
    ),
    N.MAIL_FROM: (
        "Sender address",
        "The address of the verified domain the reminder email comes from.",
    ),
    N.INBOX_IMAP_USER: (
        "Gmail account for email-in",
        "The Gmail account whose label Hausbuch reads (with app password, address and label).",
    ),
    N.INBOX_IMAP_PASSWORD: (
        "Gmail app password",
        "The app password of this Gmail account; Hausbuch reads only the one label with it.",
    ),
    N.INBOX_ADDRESS: (
        "Forwarding address",
        "The address with a token (name+token@gmail.com) receipts are forwarded to.",
    ),
    N.INBOX_LABEL: (
        "Gmail label",
        "The label Gmail puts the forwarded receipts in.",
    ),
    N.ELSTER_HERSTELLER_ID: (
        "ELSTER manufacturer ID",
        "The number ELSTER assigned to Hausbuch as a software maker (Hersteller-ID); needed"
        " once Hausbuch checks or sends tax returns through ELSTER (not built yet).",
    ),
}
assert list(SPECS_EN) == list(N)


def _label(name: AppSecretName) -> str:
    return SPECS_EN[name][0] if i18n.english() else SPECS[name].label


def _purpose(name: AppSecretName) -> str:
    return SPECS_EN[name][1] if i18n.english() else SPECS[name].purpose


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
    conn: Connection,
    settings: Settings,
    names: Iterable[AppSecretName],
    *,
    unreadable: Literal["raise", "unset"] = "raise",
) -> Settings:
    """`settings` with each of `names` typed in the app put in place of the container's, for
    a consumer that reads Settings (the chain sources, the bank sync, the mailer, the inbox
    poll). A typed value that no longer decrypts raises SecretUnreadable, or with
    unreadable="unset" leaves its field None (a plain field only), so the consumer sees it
    as not configured; never the container's value. The copy lives in the caller's frame
    only."""
    changes: dict[str, object] = {}
    rows = _rows(conn)
    for name in names:
        found = _resolved(rows.get(name.value), settings, name)
        if found.source is AppSecretSource.UNREADABLE:
            if unreadable == "raise" or name in SECRET_STR_FIELDS:
                raise SecretUnreadable(name)
            changes[name.value] = None
        elif found.source is AppSecretSource.APP:
            assert found.value is not None
            # each Settings field's own type, declared, never read off its current value
            # (an unset field is None either way)
            secret_str = name in SECRET_STR_FIELDS
            changes[name.value] = found.value if secret_str else found.value.get_secret_value()
    return dataclasses.replace(settings, **changes) if changes else settings


def ai_available(conn: Connection, settings: Settings) -> bool:
    """The AI layer is on and the workspace has an Anthropic key that reads (typed, else the
    container's), so a queued job can call the model."""
    if not settings.ai_enabled:
        return False
    names = [N.ANTHROPIC_API_KEY]
    return bool(workspace_settings(conn, settings, names, unreadable="unset").anthropic_api_key)


# --- The card --------------------------------------------------------------------------------


def secret_states(conn: Connection, settings: Settings) -> list[AppSecretState]:
    """Every allowlisted name with where its value comes from; a value only for a name that is
    not secret."""
    rows = _rows(conn)
    out = []
    for name, spec in SPECS.items():
        found = _resolved(rows.get(name.value), settings, name)
        shown = None if name.secret or found.value is None else found.value.get_secret_value()
        out.append(
            AppSecretState(
                name=name,
                group=spec.group,
                label=_label(name),
                purpose=_purpose(name),
                secret=name.secret,
                variable=name.variable,
                source=found.source,
                updated_at=found.updated_at,
                value=shown,
            )
        )
    return out


def _state(conn: Connection, settings: Settings, name: AppSecretName) -> AppSecretState:
    return next(s for s in secret_states(conn, settings) if s.name is name)


# --- An unreadable value: the Zu prüfen card and the jobs' warning (D260 amended (9)) -------

# The card's group headings (frontend AppSecrets.tsx GROUPS), in the card's order.
GROUP_LABELS: dict[AppSecretGroup, str] = {
    G.AI: "KI",
    G.CHAINS: "Blockchains & Kurse",
    G.BANK: "Bank",
    G.MAIL: "E-Mail",
    G.ELSTER: "ELSTER",
}

_DIGEST = "Die tägliche Erinnerungs-E-Mail wird nicht verschickt."
_EMAIL_IN = "Weitergeleitete Belege werden nicht aus Gmail abgerufen (E-Mail-Eingang)."
# What does not run while a name's typed value cannot be read; None: nothing uses it yet.
STOPPED: dict[AppSecretName, str | None] = {
    N.ANTHROPIC_API_KEY: (
        "Dokumente werden nicht automatisch ausgelesen, und die KI schlägt keine Zuordnung "
        "von Zahlungen vor."
    ),
    N.ETHERSCAN_KEY: "Ethereum- und Arbitrum-Adressen werden nicht gelesen.",
    N.SOLANA_RPC_URL: "Solana-Adressen werden nicht gelesen.",
    N.BSC_RPC_URL: "Kontostände auf BNB Smart Chain werden nicht gelesen.",
    N.HELIUS_KEY: None,
    N.COINGECKO_KEY: "Die täglichen Coin-Kurse kommen nur von Kraken.",
    N.FINTS_PRODUCT_ID: "Der Bankabruf läuft nicht, weder täglich noch mit „Jetzt abrufen“.",
    N.TEM_SECRET_KEY: _DIGEST,
    N.TEM_PROJECT_ID: _DIGEST,
    N.MAIL_FROM: _DIGEST,
    N.INBOX_IMAP_USER: _EMAIL_IN,
    N.INBOX_IMAP_PASSWORD: _EMAIL_IN,
    N.INBOX_ADDRESS: _EMAIL_IN,
    N.INBOX_LABEL: _EMAIL_IN,
    N.ELSTER_HERSTELLER_ID: None,
}
assert list(STOPPED) == list(N)

_DIGEST_EN = "The daily reminder email is not sent."
_EMAIL_IN_EN = "Forwarded receipts are not fetched from Gmail (email-in)."
GROUP_LABELS_EN: dict[AppSecretGroup, str] = {
    G.AI: "AI",
    G.CHAINS: "Blockchains & prices",
    G.BANK: "Bank",
    G.MAIL: "Email",
    G.ELSTER: "ELSTER",
}
STOPPED_EN: dict[AppSecretName, str | None] = {
    N.ANTHROPIC_API_KEY: (
        "Documents are not read automatically, and the AI suggests no categorisation of payments."
    ),
    N.ETHERSCAN_KEY: "Ethereum and Arbitrum addresses are not read.",
    N.SOLANA_RPC_URL: "Solana addresses are not read.",
    N.BSC_RPC_URL: "Balances on BNB Smart Chain are not read.",
    N.HELIUS_KEY: None,
    N.COINGECKO_KEY: "The daily coin prices come from Kraken only.",
    N.FINTS_PRODUCT_ID: "The bank sync does not run, neither daily nor with “Fetch now”.",
    N.TEM_SECRET_KEY: _DIGEST_EN,
    N.TEM_PROJECT_ID: _DIGEST_EN,
    N.MAIL_FROM: _DIGEST_EN,
    N.INBOX_IMAP_USER: _EMAIL_IN_EN,
    N.INBOX_IMAP_PASSWORD: _EMAIL_IN_EN,
    N.INBOX_ADDRESS: _EMAIL_IN_EN,
    N.INBOX_LABEL: _EMAIL_IN_EN,
    N.ELSTER_HERSTELLER_ID: None,
}
assert list(STOPPED_EN) == list(N)
CARD = i18n.Texts(
    "app_secrets",
    {
        "unreadable": (
            "In der App gespeicherte Zugangsschlüssel lassen sich nicht mehr lesen, meist weil"
            " der Sitzungsschlüssel des Servers gewechselt wurde.",
            "Access keys stored in the app can no longer be read, usually because the server's"
            " session key was changed.",
        ),
        "affected": ("Betroffen – {groups}.", "Affected – {groups}."),
        "no_fallback": (
            "Hausbuch nimmt dafür nicht den Wert aus der Server-Konfiguration.",
            "Hausbuch does not use the value from the server configuration instead.",
        ),
        "question": (
            "Unter Einstellungen › Zugangsschlüssel neu eintragen oder löschen.",
            "Enter again or delete under Settings › Access keys.",
        ),
    },
)

# The jobs' log code for a group that is off because its typed value cannot be read.
KEY_UNREADABLE = "key_unreadable"


def unreadable_names(
    conn: Connection, settings: Settings, names: Iterable[AppSecretName]
) -> list[AppSecretName]:
    """Those of `names` typed in the app whose value no longer decrypts, so a job can tell a
    group that is off because of them from one that was never set up (names only)."""
    rows = _rows(conn)
    return [
        name
        for name in names
        if (row := rows.get(name.value)) is not None and _open(row, settings) is None
    ]


def unreadable_inbox_items(conn: Connection, settings: Settings) -> list[InboxItem]:
    """One Zu prüfen card while any value typed in the app cannot be read (principle 7): the
    groups by their card headings with each value's label, what does not run meanwhile, and
    that the server's own value never stands in. Gone once each is typed again or deleted.
    Never a value; a name by its label only."""
    rows = _rows(conn)
    lost: list[tuple[AppSecretName, datetime]] = []
    for name in N:
        row = rows.get(name.value)
        if row is not None and _open(row, settings) is None:
            lost.append((name, row.updated_at))
    if not lost:
        return []
    by_group: dict[AppSecretGroup, list[str]] = {}
    for name, _ in lost:
        by_group.setdefault(SPECS[name].group, []).append(_label(name))
    english = i18n.english()
    group_labels = GROUP_LABELS_EN if english else GROUP_LABELS
    groups = "; ".join(
        f"{group_labels[group]}: {', '.join(by_group[group])}"
        for group in GROUP_LABELS
        if group in by_group
    )
    stopped: list[str] = []
    for name, _ in lost:
        sentence = (STOPPED_EN if english else STOPPED)[name]
        if sentence is not None and sentence not in stopped:
            stopped.append(sentence)
    reasoning = " ".join(
        [
            CARD("unreadable"),
            CARD("affected", groups=groups),
            *stopped,
            CARD("no_fallback"),
        ]
    )
    return [
        InboxItem(
            kind=InboxItemKind.APP_SECRET_UNREADABLE,
            created_at=max(updated for _, updated in lost),
            reasoning=reasoning,
            question=CARD("question"),
        )
    ]


# --- Set and delete --------------------------------------------------------------------------


def checked_value(name: AppSecretName, value: SecretStr) -> str:
    """The value to store: trimmed, 1 to 512 bytes, and of its name's form (SPECS): a key
    printable ASCII without a blank, a URL as Settings checks the container variable
    (settings._is_http_url), a product id as the sync does (product_id_usable), a mail address,
    a UUID, a label or password without a control character, an ELSTER Hersteller-ID 1 to 10
    ASCII digits. Fixed messages: never the value."""
    plain = value.get_secret_value().strip()
    if not plain or len(plain.encode()) > MAX_BYTES:
        raise RuleError(E.APP_SECRET_INVALID, f"a value is 1 to {MAX_BYTES} bytes")
    if not SPECS[name].check(plain):
        if name.is_url:
            raise RuleError(
                E.APP_SECRET_URL_INVALID, "the address must be an http(s) URL with a host"
            )
        raise RuleError(E.APP_SECRET_INVALID, f"not a valid value for {name.value}")
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
    "GROUP_LABELS",
    "INFO",
    "KEY_UNREADABLE",
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
    "unreadable_inbox_items",
    "unreadable_names",
    "workspace_settings",
]
