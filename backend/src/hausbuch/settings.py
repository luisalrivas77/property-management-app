"""Runtime settings, read from the environment by an explicit load_settings() call.

Nothing is read at import time, so importing hausbuch never depends on the environment.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

MIN_IBAN_HMAC_KEY_BYTES = 32


class SettingsError(ValueError):
    """A required setting is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    # Both hold secrets (the URL may carry a password), so neither appears in repr().
    database_url: str = field(repr=False)
    iban_hmac_key: bytes = field(repr=False)


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    database_url = environ.get("HAUSBUCH_DATABASE_URL", "").strip()
    if not database_url:
        raise SettingsError("HAUSBUCH_DATABASE_URL is not set")

    key_hex = environ.get("HAUSBUCH_IBAN_HMAC_KEY", "").strip()
    if not key_hex:
        raise SettingsError("HAUSBUCH_IBAN_HMAC_KEY is not set")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        raise SettingsError("HAUSBUCH_IBAN_HMAC_KEY must be hex-encoded") from None
    if len(key) < MIN_IBAN_HMAC_KEY_BYTES:
        raise SettingsError(
            f"HAUSBUCH_IBAN_HMAC_KEY must be at least {MIN_IBAN_HMAC_KEY_BYTES} bytes "
            f"({2 * MIN_IBAN_HMAC_KEY_BYTES} hex characters), got {len(key)}"
        )
    return Settings(database_url=database_url, iban_hmac_key=key)
