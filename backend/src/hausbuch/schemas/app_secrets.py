"""Provider keys and URLs typed in the app (docs/phases/phase-6.md D260).

The allowlist is closed: each name is a Settings field of the same name and the container
variable `HAUSBUCH_<NAME>`. Nothing that reaches the database, encrypts these rows or opens
the owner's role is on it (D260 (2)). No model here carries a value, its length or its
ciphertext; the request's value is a SecretStr and is never echoed by a 422.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, SecretStr

from .common import Schema


class AppSecretName(StrEnum):
    """app_secret.name (migration 0045's CHECK)."""

    ANTHROPIC_API_KEY = "anthropic_api_key"
    ETHERSCAN_KEY = "etherscan_key"
    SOLANA_RPC_URL = "solana_rpc_url"
    BSC_RPC_URL = "bsc_rpc_url"
    HELIUS_KEY = "helius_key"
    COINGECKO_KEY = "coingecko_key"

    @property
    def variable(self) -> str:
        """The container variable of the same Settings field (its name, never its value)."""
        return f"HAUSBUCH_{self.value.upper()}"

    @property
    def is_url(self) -> bool:
        return self in URL_SECRETS


URL_SECRETS = frozenset({AppSecretName.SOLANA_RPC_URL, AppSecretName.BSC_RPC_URL})


class AppSecretSource(StrEnum):
    """Where a name's value comes from now: typed in the app (it wins), the container
    variable, nowhere (a URL then uses its public default), or typed in the app but no longer
    readable (the session key changed): the consumer then uses nothing, never the variable."""

    APP = "app"
    CONTAINER = "container"
    NONE = "none"
    UNREADABLE = "unreadable"


class AppSecretKind(StrEnum):
    KEY = "key"
    URL = "url"


class AppSecretState(Schema):
    """One row of the card „Zugangsschlüssel (API keys)“: never a value or its length."""

    name: AppSecretName
    label: str
    purpose: str  # one plain German line: what Hausbuch uses it for
    kind: AppSecretKind
    variable: str  # the container variable's name, the alternative to typing it here
    source: AppSecretSource
    updated_at: datetime | None = None  # when it was typed in the app (source app/unreadable)


class AppSecrets(Schema):
    """GET /app-secrets: every allowlisted name, in a fixed order."""

    secrets: list[AppSecretState]


class AppSecretPut(Schema):
    """PUT /app-secrets/{name}: the value, stored encrypted and never returned, logged or
    echoed (a SecretStr: its repr is stars; hide_input_in_errors keeps a 422 from quoting
    it). Checked by services.app_secrets.set_secret: surrounding blanks trimmed, then 1 to
    512 printable ASCII characters (app_secret.invalid), and a URL as Settings checks the
    container variable (app_secret.url_invalid)."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    value: SecretStr
