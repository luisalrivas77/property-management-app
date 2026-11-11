"""IBAN normalization, masking and keyed hashing (tests/fixtures/phase1/iban.yaml).

The plaintext IBAN is accepted on input only. What is stored is the mask ("DE89 **** 3000")
for display plus an HMAC-SHA256 under a key from the secrets manager, for matching.
"""

import hashlib
import hmac
import re

from .errors import RuleError

MALFORMED = "iban.malformed"
BAD_LENGTH = "iban.bad_length"
BAD_CHECKSUM = "iban.bad_checksum"

# Total IBAN length per country code, from the SWIFT IBAN registry (release 101). Every entry
# was cross-checked against two independent derivations of that registry: python-stdnum 2.2
# (stdnum/iban.dat) and schwifty 2026.10.1 (iban_registry/generated.json); they agree on all
# 89. Territories that use their parent's prefix (Åland -> FI, Jersey -> GB, Réunion -> FR,
# ...) have no entry of their own, as in the registry.
IBAN_LENGTHS: dict[str, int] = {
    # SEPA
    "AD": 24, "AL": 28, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GI": 23,
    "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LI": 21, "LT": 20,
    "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MT": 31, "NL": 18,
    "NO": 15, "PL": 28, "PT": 25, "RO": 24, "RS": 22, "SE": 24, "SI": 19, "SK": 24,
    "SM": 27, "VA": 22,
    # Rest of the registry
    "AE": 23, "AZ": 28, "BA": 20, "BH": 22, "BI": 27, "BR": 29, "BY": 28, "CR": 22,
    "DJ": 27, "DO": 28, "EG": 29, "FK": 18, "FO": 18, "GE": 22, "GL": 18, "GT": 28,
    "HN": 28, "IL": 23, "IQ": 23, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32,
    "LY": 25, "MN": 20, "MR": 27, "MU": 30, "NI": 28, "OM": 23, "PK": 24, "PS": 29,
    "QA": 29, "RU": 33, "SA": 24, "SC": 31, "SD": 18, "SO": 23, "ST": 25, "SV": 28,
    "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VG": 24, "XK": 20, "YE": 30,
}  # fmt: skip

_STRUCTURE = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]+")


def normalize_iban(raw: str) -> str:
    """Strip all whitespace and upper-case, then validate.

    Checks, in order: structure and a known country (iban.malformed), the registry length
    for that country (iban.bad_length), the ISO 13616 mod-97 checksum (iban.bad_checksum).
    """
    compact = "".join(raw.split())
    if not compact.isascii():
        raise RuleError(MALFORMED, "IBAN contains non-ASCII characters")
    iban = compact.upper()
    if not _STRUCTURE.fullmatch(iban) or iban[:2] not in IBAN_LENGTHS:
        raise RuleError(MALFORMED, "not an IBAN")
    if len(iban) != IBAN_LENGTHS[iban[:2]]:
        raise RuleError(BAD_LENGTH, f"an IBAN for {iban[:2]} has {IBAN_LENGTHS[iban[:2]]} chars")
    if _mod97(iban) != 1:
        raise RuleError(BAD_CHECKSUM, "IBAN check digits do not match")
    return iban


def mask_iban(normalized: str) -> str:
    """Display form: country + check digits + " **** " + last four, e.g. "DE89 **** 3000"."""
    _require_normalized(normalized)
    return f"{normalized[:4]} **** {normalized[-4:]}"


_IBAN_LIKE = re.compile(r"[A-Za-z]{2}[0-9]{2}(?: ?[A-Za-z0-9]){11,32}")


def _masked(text: str) -> str | None:
    try:
        return mask_iban(normalize_iban(text))
    except RuleError:
        return None


def mask_ibans_in_text(text: str) -> str:
    """Free text with every IBAN in it masked: the whole text when it is one IBAN, else each
    IBAN-like run that normalizes (a valid IBAN) replaced by its mask; anything that does not
    normalize is left as it is. Used for a statement line's `raw` before it is stored and for
    the purpose text a payment proof prints (phase 3 D144)."""
    whole = _masked(text)
    if whole is not None:
        return whole
    return _IBAN_LIKE.sub(lambda m: _masked(m.group(0)) or m.group(0), text)


def iban_hmac(normalized: str, key: bytes) -> bytes:
    """HMAC-SHA256 over the normalized IBAN as ASCII (32 bytes), for matching without storing
    the plaintext."""
    _require_normalized(normalized)
    return hmac.new(key, normalized.encode("ascii"), hashlib.sha256).digest()


def _mod97(iban: str) -> int:
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97


def _require_normalized(value: str) -> None:
    # A hash of a non-normalized IBAN would silently never match its normalized twin.
    if normalize_iban(value) != value:
        raise ValueError("pass the output of normalize_iban")
