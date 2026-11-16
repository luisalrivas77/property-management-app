"""Provider keys, URLs and service settings typed in the app (docs/phases/phase-6.md D260).

The allowlist is closed: each name is a Settings field of the same name and the container
variable `HAUSBUCH_<NAME>`. Nothing that reaches the database, encrypts these rows or opens
the owner's role is on it (the database URL, the session key, the IBAN key, the owner URL;
D260 (2)). A name is `secret` (a key, a password, an RPC address that may carry a key: never
returned) or not (the TEM project, the sender address, the inbox's user, address and label:
shown back on the card, never in an event). The request's value is a SecretStr either way and
is never echoed by a 422.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, SecretStr

from .common import Schema


class AppSecretName(StrEnum):
    """app_secret.name (migration 0045's CHECK), in the card's order."""

    # KI
    ANTHROPIC_API_KEY = "anthropic_api_key"
    # Blockchains & Kurse
    ETHERSCAN_KEY = "etherscan_key"
    SOLANA_RPC_URL = "solana_rpc_url"
    BSC_RPC_URL = "bsc_rpc_url"
    HELIUS_KEY = "helius_key"
    COINGECKO_KEY = "coingecko_key"
    # Bank
    FINTS_PRODUCT_ID = "fints_product_id"
    # E-Mail: the reminder digest (Scaleway TEM) and email-in (the Gmail label)
    TEM_SECRET_KEY = "tem_secret_key"
    TEM_PROJECT_ID = "tem_project_id"
    MAIL_FROM = "mail_from"
    INBOX_IMAP_USER = "inbox_imap_user"
    INBOX_IMAP_PASSWORD = "inbox_imap_password"
    INBOX_ADDRESS = "inbox_address"
    INBOX_LABEL = "inbox_label"

    @property
    def variable(self) -> str:
        """The container variable of the same Settings field (its name, never its value)."""
        return f"HAUSBUCH_{self.value.upper()}"

    @property
    def is_url(self) -> bool:
        return self in URL_SECRETS

    @property
    def secret(self) -> bool:
        return self not in SHOWN_SETTINGS


URL_SECRETS = frozenset({AppSecretName.SOLANA_RPC_URL, AppSecretName.BSC_RPC_URL})
# Not secret: returned on the card (the inbox address is shown to members already, the
# inbound-mail page's token address).
SHOWN_SETTINGS = frozenset(
    {
        AppSecretName.TEM_PROJECT_ID,
        AppSecretName.MAIL_FROM,
        AppSecretName.INBOX_IMAP_USER,
        AppSecretName.INBOX_ADDRESS,
        AppSecretName.INBOX_LABEL,
    }
)


class AppSecretGroup(StrEnum):
    AI = "ai"  # „KI“
    CHAINS = "chains"  # „Blockchains & Kurse“
    BANK = "bank"  # „Bank“
    MAIL = "mail"  # „E-Mail“


class AppSecretSource(StrEnum):
    """Where a name's value comes from now: typed in the app (it wins), the container
    variable, nowhere (a URL then uses its public default), or typed in the app but no longer
    readable (the session key changed): the consumer then uses nothing, never the variable."""

    APP = "app"
    CONTAINER = "container"
    NONE = "none"
    UNREADABLE = "unreadable"


class AppSecretState(Schema):
    """One row of the card „Zugangsschlüssel (API keys)“. `value` only for a name that is not
    secret; a secret's value and its length are never returned."""

    name: AppSecretName
    group: AppSecretGroup
    label: str
    purpose: str  # one plain German line: what Hausbuch uses it for
    secret: bool
    variable: str  # the container variable's name, the alternative to typing it here
    source: AppSecretSource
    updated_at: datetime | None = None  # when it was typed in the app (source app/unreadable)
    value: str | None = None  # not secret only, from the app or the container


class AppSecrets(Schema):
    """GET /app-secrets: every allowlisted name, in a fixed order."""

    secrets: list[AppSecretState]


class AppSecretPut(Schema):
    """PUT /app-secrets/{name}: the value, stored encrypted and never logged or echoed (a
    SecretStr: its repr is stars; hide_input_in_errors keeps a 422 from quoting it). Checked
    by services.app_secrets.checked_value per name (app_secret.invalid, app_secret.url_invalid)."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    value: SecretStr
