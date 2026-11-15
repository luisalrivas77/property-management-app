"""Phase 7 step 1: address validation, the mask, the HMAC and Keccak-256
(docs/phases/phase-7.md D179 (2), §4; fixture tests/fixtures/phase7/chains.yaml `addresses`).
Every address here is a published test vector or generated for this file, never a household's.
"""

import hashlib
import os
import re

import fixture_models_phase7 as fixtures
import pytest

from hausbuch.rules.chain import address as address_rules
from hausbuch.rules.chain.address import address_hmac, eip55, mask, validate
from hausbuch.rules.chain.keccak import _sponge_256, keccak256
from hausbuch.rules.errors import WALLET_ADDRESS_INVALID, RuleError
from hausbuch.schemas.chain import Chain

CHAINS = fixtures.load("chains")

# 0041's CHECKs on wallet_address, the backstop of validate: whatever validate accepts, the
# database must accept too (else a "valid" address would be a 500).
_B58 = "[1-9A-HJ-NP-Za-km-z]"
DB_FORMAT = {
    Chain.BITCOIN: rf"^(bc1[02-9ac-hj-np-z]{{8,87}}|[13]{_B58}{{25,34}})$",
    Chain.ETHEREUM: r"^0x[0-9a-f]{40}$",
    Chain.ARBITRUM: r"^0x[0-9a-f]{40}$",
    Chain.BSC: r"^0x[0-9a-f]{40}$",
    Chain.SOLANA: rf"^{_B58}{{32,44}}$",
}
DB_MASK = r"^.{3,4}….{4}$"


# --- Keccak-256 ------------------------------------------------------------------------


def test_keccak256_of_the_empty_input():
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


def test_keccak256_is_not_nist_sha3():
    assert keccak256(b"") != hashlib.sha3_256(b"").digest()


@pytest.mark.parametrize("length", [0, 1, 40, 135, 136, 137, 271, 272, 273, 500])
def test_the_sponge_with_sha3_padding_equals_hashlib(length):
    """The permutation and the absorb loop checked on their own: with SHA-3's padding byte the
    same sponge must give hashlib.sha3_256, across the block boundary (136 bytes)."""
    data = os.urandom(length)
    assert _sponge_256(data, 0x06) == hashlib.sha3_256(data).digest()


# EIP-55's own examples (all mixed case).
EIP55_EXAMPLES = [
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
]


@pytest.mark.parametrize("example", EIP55_EXAMPLES)
def test_eip55_examples(example):
    assert eip55(example.lower()) == example
    assert validate(Chain.ETHEREUM, example) == example.lower()


@pytest.mark.parametrize("example", EIP55_EXAMPLES)
def test_eip55_one_flipped_letter_is_refused(example):
    i = next(i for i, c in enumerate(example) if i > 1 and c.isalpha())
    flipped = example[:i] + example[i].swapcase() + example[i + 1 :]
    with pytest.raises(RuleError) as caught:
        validate(Chain.ETHEREUM, flipped)
    assert caught.value.code == WALLET_ADDRESS_INVALID


def test_evm_all_upper_case_is_accepted_and_lower_cased():
    upper = "0x" + EIP55_EXAMPLES[0][2:].upper()
    assert validate(Chain.BSC, upper) == EIP55_EXAMPLES[0].lower()


# --- the fixture -----------------------------------------------------------------------


@pytest.mark.parametrize("case", CHAINS.addresses, ids=lambda c: c.id)
def test_chains_yaml_addresses(case):
    assert case.pending is None
    if not case.valid:
        with pytest.raises(RuleError) as caught:
            validate(case.chain, case.address)
        assert caught.value.code == WALLET_ADDRESS_INVALID
        assert case.address not in str(caught.value)
        return
    normalised = validate(case.chain, case.address)
    assert normalised == case.normalised
    assert mask(case.chain, normalised) == case.masked
    assert re.fullmatch(DB_FORMAT[case.chain], normalised)
    assert re.fullmatch(DB_MASK, case.masked)


# --- Bitcoin: bech32m (BIP-350), the witness rules, case --------------------------------

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _encode(version: int, program: bytes, const: int) -> str:
    """A bc1 address built here (BIP-173/350's encoder), so a wrong-checksum case is exact."""
    acc = bits = 0
    data = [version]
    for byte in program:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    hrp = [ord(c) >> 5 for c in "bc"] + [0] + [ord(c) & 31 for c in "bc"]
    polymod = address_rules._polymod(hrp + data + [0] * 6) ^ const
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return "bc1" + "".join(_CHARSET[d] for d in data + checksum)


BECH32, BECH32M = 1, 0x2BC830A3


def test_a_published_taproot_address_is_valid():
    """BIP-350's test vector of a version 1 (Taproot) mainnet address."""
    taproot = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
    assert validate(Chain.BITCOIN, taproot) == taproot
    assert re.fullmatch(DB_FORMAT[Chain.BITCOIN], taproot)


@pytest.mark.parametrize(
    ("version", "length", "const", "valid"),
    [
        (0, 20, BECH32, True),
        (0, 32, BECH32, True),
        (0, 20, BECH32M, False),  # v0 must use bech32
        (0, 25, BECH32, False),  # v0 is 20 or 32 bytes
        (1, 32, BECH32M, True),
        (1, 32, BECH32, False),  # v1+ must use bech32m
        (16, 6, BECH32M, True),  # 20 characters, the shortest 0041 stores
        (16, 5, BECH32M, False),  # valid BIP-350 but 18 characters, below 0041's CHECK
        (2, 41, BECH32M, False),  # a program is at most 40 bytes
    ],
)
def test_segwit_versions_and_checksums(version, length, const, valid):
    built = _encode(version, os.urandom(length), const)
    if valid:
        assert validate(Chain.BITCOIN, built) == built
        assert validate(Chain.BITCOIN, built.upper()) == built
    else:
        with pytest.raises(RuleError):
            validate(Chain.BITCOIN, built)


def test_an_address_outside_the_stored_length_is_refused():
    """0041's CHECK is length(address) BETWEEN 20 AND 128: BIP-350's 14-character v16 vector
    is valid bech32m but would be a 500 from the database, so validate refuses it (review of
    PR #374), with surrounding whitespace too."""
    with pytest.raises(RuleError) as refused:
        validate(Chain.BITCOIN, "   bc1sw50qgdz25j   ")
    assert refused.value.code == WALLET_ADDRESS_INVALID
    assert "bc1sw50qgdz25j" not in str(refused.value)


def test_mixed_case_bech32_is_refused():
    with pytest.raises(RuleError):
        validate(Chain.BITCOIN, "bc1qW508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")


@pytest.mark.parametrize(
    "text",
    [
        "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",  # testnet
        "2N2JD6wb56AfK4tfmM6PwdVmoYk2dCKf4Br",  # testnet P2SH (base58 version 0xc4)
        "",
        "bc1",
    ],
)
def test_not_a_mainnet_bitcoin_address(text):
    with pytest.raises(RuleError):
        validate(Chain.BITCOIN, text)


def test_a_paste_is_trimmed():
    assert validate(Chain.SOLANA, "  11111111111111111111111111111111\n") == "1" * 32


def test_solana_refuses_a_33_byte_key():
    key = os.urandom(33).replace(b"\0", b"\1")
    number = int.from_bytes(key, "big")
    text = ""
    while number:
        number, rest = divmod(number, 58)
        text = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"[rest] + text
    with pytest.raises(RuleError):
        validate(Chain.SOLANA, text)


# --- the HMAC ----------------------------------------------------------------------------


def test_the_hmac_is_keyed_domain_separated_and_chainless():
    key = b"k" * 32
    evm = validate(Chain.ETHEREUM, EIP55_EXAMPLES[0])
    assert address_hmac(evm, key) == address_hmac(validate(Chain.ARBITRUM, evm), key)
    assert address_hmac(evm, key) != address_hmac(evm, b"j" * 32)
    assert len(address_hmac(evm, key)) == 32
    # Solana addresses are case-sensitive: two that differ in case are two HMACs.
    sol = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"
    assert address_hmac(sol, key) != address_hmac(sol.swapcase(), key)


def test_every_chain_has_an_expected_shape():
    assert set(address_rules.EXPECTED_SHAPE) == set(Chain)
