"""Address validation, the mask and the HMAC (docs/phases/phase-7.md D179, §4); built in step 1.

`validate` returns the normalised form (EVM: lower-case hex, D180 amended 15 Nov 2026, after
the EIP-55 checksum of a mixed-case input is checked; Bitcoin: bech32 lower case or the
base58check form as written; Solana: base58 of 32 bytes) or raises RuleError
`wallet.address_invalid` naming the chain's expected shape in plain words. `mask` gives the
stored, logged and shown form (the first 4 characters, „…“, the last 4; migration 0041's
CHECK). `address_hmac` is the keyed HMAC of the normalised form that every match compares
(D179 (2), D184). Fixture tests/fixtures/phase7/chains.yaml `addresses`.

No message here ever repeats the input: an address is the one plaintext identifier of the
schema and leaves `wallet_address` only masked (D179).

Formats:
- Bitcoin mainnet only: bech32 (BIP-173) for witness version 0 with a 20- or 32-byte program,
  bech32m (BIP-350) for versions 1 to 16 (Taproot), all lower or all upper case (stored lower);
  or base58check (P2PKH version 0x00, P2SH 0x05).
- EVM (Ethereum, Arbitrum, BNB Smart Chain): `0x` and 40 hex digits. All lower or all upper
  case is accepted as is; mixed case must carry a valid EIP-55 checksum (Keccak-256, vendored
  in keccak.py, since hashlib's sha3_256 is NIST SHA-3).
- Solana: base58 decoding to exactly 32 bytes.
"""

import hashlib
import hmac as _hmac

from hausbuch.rules.errors import WALLET_ADDRESS_INVALID, RuleError
from hausbuch.schemas.chain import Chain

from .keccak import keccak256

EVM_CHAINS = frozenset({Chain.ETHEREUM, Chain.ARBITRUM, Chain.BSC})

# The plain-words shape per chain, the message of wallet.address_invalid (the frontend shows
# its own German text for the code; this one is the API's message).
EXPECTED_SHAPE: dict[Chain, str] = {
    Chain.BITCOIN: (
        "Eine Bitcoin-Adresse beginnt mit bc1 (SegWit, Taproot), 1 oder 3 und hat eine "
        "gültige Prüfsumme."
    ),
    Chain.ETHEREUM: "Eine Ethereum-Adresse ist 0x und 40 Hex-Zeichen (0–9, a–f).",
    Chain.ARBITRUM: "Eine Arbitrum-Adresse ist 0x und 40 Hex-Zeichen (0–9, a–f).",
    Chain.BSC: "Eine Adresse auf BNB Smart Chain ist 0x und 40 Hex-Zeichen (0–9, a–f).",
    Chain.SOLANA: "Eine Solana-Adresse sind 32 bis 44 Zeichen in Base58 (ohne 0, O, I und l).",
}

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3
_HEX = frozenset("0123456789abcdefABCDEF")
# Domain separation: an address's HMAC never equals an IBAN's or an account number's under
# the same key (services/iban).
_HMAC_DOMAIN = b"wallet_address\x00"
# Migration 0041's CHECK on wallet_address.address: a normalised address outside it is refused
# here, so a valid but very short Bitcoin vector (BIP-350's 14-character v16 address) is a
# 422, not a 500 from the database (review of PR #374, 16 Nov 2026).
_STORED_LENGTH = range(20, 129)


def validate(chain: Chain, address: str) -> str:
    """The normalised address, or RuleError(wallet.address_invalid). Surrounding whitespace
    of a paste is dropped; the normalised form is 20 to 128 characters (0041's CHECK)."""
    text = address.strip()
    if chain is Chain.BITCOIN:
        normalised = _bitcoin(text)
    elif chain in EVM_CHAINS:
        normalised = _evm(text)
    elif chain is Chain.SOLANA:
        data = _b58decode(text) if 32 <= len(text) <= 44 else None
        normalised = text if data is not None and len(data) == 32 else None
    else:  # pragma: no cover - every Chain is handled above
        normalised = None
    if normalised is None or len(normalised) not in _STORED_LENGTH:
        raise RuleError(WALLET_ADDRESS_INVALID, EXPECTED_SHAPE[chain])
    return normalised


def mask(chain: Chain, address: str) -> str:
    """D179: the first four and the last four characters of the normalised address."""
    del chain  # one rule for every chain today; kept for a chain whose prefix differs
    return f"{address[:4]}…{address[-4:]}"


def address_hmac(normalised: str, key: bytes) -> bytes:
    """HMAC-SHA256 of the normalised address under HAUSBUCH_IBAN_HMAC_KEY (D179 (2)). The
    chain is not in the input: one 0x address has one HMAC on every EVM chain, and
    wallet_address is unique per (workspace, chain, HMAC)."""
    return _hmac.new(key, _HMAC_DOMAIN + normalised.encode("ascii"), hashlib.sha256).digest()


# --- EVM -----------------------------------------------------------------------------------


def _evm(text: str) -> str | None:
    if len(text) != 42 or not text.startswith("0x") or not set(text[2:]) <= _HEX:
        return None
    digits = text[2:]
    if digits != digits.lower() and digits != digits.upper() and not _eip55_ok(digits):
        return None
    return "0x" + digits.lower()


def eip55(address: str) -> str:
    """The EIP-55 mixed-case form of a 0x address (for display and tests)."""
    digits = address[2:].lower()
    digest = keccak256(digits.encode("ascii")).hex()
    return "0x" + "".join(
        c.upper() if c.isalpha() and int(digest[i], 16) >= 8 else c for i, c in enumerate(digits)
    )


def _eip55_ok(digits: str) -> bool:
    return eip55("0x" + digits)[2:] == digits


# --- Bitcoin -------------------------------------------------------------------------------


def _bitcoin(text: str) -> str | None:
    if text[:3].lower() == "bc1":
        return _segwit(text)
    if text[:1] in ("1", "3") and 26 <= len(text) <= 35:
        return text if _base58check_ok(text) else None
    return None


def _polymod(values: list[int]) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            check ^= gen[i] if (top >> i) & 1 else 0
    return check


def _convert_bits(data: list[int], from_bits: int, to_bits: int) -> list[int] | None:
    """BIP-173's convertbits without padding (decoding a witness program)."""
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << to_bits) - 1
    for value in data:
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maxv)
    if bits >= from_bits or (acc << (to_bits - bits)) & maxv:
        return None
    return out


def _segwit(text: str) -> str | None:
    if text != text.lower() and text != text.upper():
        return None  # BIP-173: mixed case is invalid
    lower = text.lower()
    if len(lower) > 90:
        return None
    data_part = lower[3:]
    if len(data_part) < 7 or any(c not in _BECH32 for c in data_part):
        return None
    values = [_BECH32.index(c) for c in data_part]
    hrp = [ord(c) >> 5 for c in "bc"] + [0] + [ord(c) & 31 for c in "bc"]
    check = _polymod(hrp + values)
    version = values[0]
    if version > 16:
        return None
    if check != (_BECH32_CONST if version == 0 else _BECH32M_CONST):
        return None
    program = _convert_bits(values[1:-6], 5, 8)
    if program is None or not 2 <= len(program) <= 40:
        return None
    if version == 0 and len(program) not in (20, 32):
        return None
    return lower


def _b58decode(text: str) -> bytes | None:
    number = 0
    for char in text:
        index = _B58.find(char)
        if index < 0:
            return None
        number = number * 58 + index
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def _base58check_ok(text: str) -> bool:
    data = _b58decode(text)
    if data is None or len(data) != 25 or data[0] not in (0x00, 0x05):
        return False
    digest = hashlib.sha256(hashlib.sha256(data[:-4]).digest()).digest()
    return digest[:4] == data[-4:]
