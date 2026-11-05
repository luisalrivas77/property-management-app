"""Edge cases for hausbuch.rules.iban beyond iban.yaml."""

import pytest

from hausbuch.rules import iban as rules

KEY = b"hausbuch-test-key-not-a-secret"

# Real-format IBANs (registry examples), independently checked with python-stdnum.
OTHER_SEPA = [
    "BE71096123456769",  # 16
    "FR1420041010050500013M02606",  # 27, letter inside the BBAN
    "NL91ABNA0417164300",  # 18
    "NO9386011117947",  # 15, shortest in SEPA
    "CH9300762011623852957",  # 21
    "ES9121000418450200051332",  # 24
    "IT60X0542811101000000123456",  # 27
    "PL61109010140000071219812874",  # 28
    "MT84MALT011000012345MTLCAST001S",  # 31, longest in SEPA
    "LU280019400644750000",  # 20
]


@pytest.mark.parametrize("value", OTHER_SEPA)
def test_other_sepa_countries(value):
    assert rules.normalize_iban(value) == value
    masked = rules.mask_iban(value)
    assert masked == f"{value[:4]} **** {value[-4:]}"


@pytest.mark.parametrize("value", OTHER_SEPA)
def test_one_char_longer_is_bad_length(value):
    with pytest.raises(rules.RuleError) as excinfo:
        rules.normalize_iban(value + "0")
    assert excinfo.value.code == rules.BAD_LENGTH


def test_lower_case_bban_letters_and_any_whitespace():
    raw = "\tfr14 2004 1010 0505 0001 3m02 606\n"
    assert rules.normalize_iban(raw) == "FR1420041010050500013M02606"


@pytest.mark.parametrize(
    "raw",
    [
        "XX89370400440532013000",  # unknown country code
        "D189370400440532013000",  # digit in the country code
        "DEX9370400440532013000",  # letter in the check digits
        "DE89-3704-0044-0532-0130-00",  # punctuation is not whitespace
        "DE89370400440532013000ä",  # non-ASCII
        "",
    ],
)
def test_malformed(raw):
    with pytest.raises(rules.RuleError) as excinfo:
        rules.normalize_iban(raw)
    assert excinfo.value.code == rules.MALFORMED


def test_length_is_checked_before_checksum():
    # Too long and the checksum is wrong too: length wins.
    with pytest.raises(rules.RuleError) as excinfo:
        rules.normalize_iban("DE893704004405320130001")
    assert excinfo.value.code == rules.BAD_LENGTH


def test_hmac_is_32_bytes_and_keyed():
    value = "DE89370400440532013000"
    digest = rules.iban_hmac(value, KEY)
    assert len(digest) == 32
    assert digest != rules.iban_hmac(value, KEY + b"x")
    assert digest != rules.iban_hmac("AT611904300234573201", KEY)


@pytest.mark.parametrize("fn", [rules.mask_iban, lambda v: rules.iban_hmac(v, KEY)])
def test_mask_and_hmac_refuse_non_normalized_input(fn):
    with pytest.raises(ValueError):
        fn("de89 3704 0044 0532 0130 00")
