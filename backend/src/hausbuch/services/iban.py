"""IBAN input for the services: the plaintext is turned into mask + keyed HMAC and dropped.

Never store, return or log the plaintext. A bad IBAN raises hausbuch.rules.errors.RuleError
with its rule code (iban.malformed, iban.bad_length, iban.bad_checksum); its message never
repeats the input.
"""

from dataclasses import dataclass, field

from hausbuch.rules.iban import iban_hmac, mask_iban, normalize_iban


@dataclass(frozen=True)
class ProtectedIban:
    masked: str  # "DE89 **** 3000"
    hmac: bytes = field(repr=False)  # HMAC-SHA256 of the normalized IBAN


def protect_iban(raw: str, key: bytes) -> ProtectedIban:
    normalized = normalize_iban(raw)
    return ProtectedIban(masked=mask_iban(normalized), hmac=iban_hmac(normalized, key))
