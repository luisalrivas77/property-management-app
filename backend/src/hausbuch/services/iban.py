"""IBAN input for the services: the plaintext is turned into mask + keyed HMAC and dropped.

Never store, return or log the plaintext. A bad IBAN raises hausbuch.rules.errors.RuleError
with its rule code (iban.malformed, iban.bad_length, iban.bad_checksum); its message never
repeats the input.

Phase 6 (D156, D164, D176): an account's own number (a card issuer's customer number, a depot
number) is protected the same way by protect_ref.
"""

import hashlib
import hmac as _hmac
import re
from dataclasses import dataclass, field

from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import iban_hmac, mask_iban, normalize_iban
from hausbuch.rules.statements import UNPARSABLE


@dataclass(frozen=True)
class ProtectedIban:
    masked: str  # "DE89 **** 3000"
    hmac: bytes = field(repr=False)  # HMAC-SHA256 of the normalized IBAN


def protect_iban(raw: str, key: bytes) -> ProtectedIban:
    normalized = normalize_iban(raw)
    return ProtectedIban(masked=mask_iban(normalized), hmac=iban_hmac(normalized, key))


# The reference's HMAC is domain-separated from an IBAN's, so a number that happens to spell
# an IBAN never matches one.
_REF_DOMAIN = b"external_ref\x00"
_REF_DROPPED = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class ProtectedRef:
    masked: str  # "**** 4567" (account.external_ref_masked)
    hmac: bytes = field(repr=False)


def protect_ref(raw: str, key: bytes) -> ProtectedRef:
    """The reference upper-cased with everything but letters and digits dropped, masked to
    „**** “ + its last four and HMAC'd. Fewer than four characters raise
    RuleError(statement.unparsable); the message never repeats the input."""
    normalized = _REF_DROPPED.sub("", raw.upper())
    if len(normalized) < 4:
        raise RuleError(UNPARSABLE, "the account reference is too short")
    digest = _hmac.new(key, _REF_DOMAIN + normalized.encode("ascii"), hashlib.sha256).digest()
    return ProtectedRef(masked=f"**** {normalized[-4:]}", hmac=digest)
