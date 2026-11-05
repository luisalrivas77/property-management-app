"""The signed ceremony cookie (§6.3), without a database: round trip, and every way open_sealed
refuses (missing, malformed, tampered, other key, wrong kind, expired)."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hausbuch.auth.ceremony import (
    CEREMONY_MAX_AGE_S,
    CHALLENGE_BYTES,
    Subject,
    _b64,
    _mac,
    new_ceremony,
    open_sealed,
    seal,
)
from hausbuch.auth.errors import AuthenticationFailed

KEY = bytes(range(32))
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _reason(value, kind="authentication", now=NOW, key=KEY) -> str:
    with pytest.raises(AuthenticationFailed) as excinfo:
        open_sealed(key, value, kind=kind, now=now)
    return excinfo.value.reason


@pytest.mark.parametrize(
    "subject",
    [None, Subject(invite_id=uuid4()), Subject(user_id=uuid4(), workspace_id=uuid4())],
    ids=["sign-in", "invite", "user"],
)
def test_round_trip(subject):
    ceremony = new_ceremony("registration", subject, now=NOW)
    assert len(ceremony.challenge) == CHALLENGE_BYTES
    assert ceremony.exp == int(NOW.timestamp()) + CEREMONY_MAX_AGE_S
    assert open_sealed(KEY, seal(KEY, ceremony), kind="registration", now=NOW) == ceremony


def test_challenges_are_fresh():
    assert new_ceremony("authentication", None, now=NOW).challenge != (
        new_ceremony("authentication", None, now=NOW).challenge
    )


@pytest.mark.parametrize("value", [None, "", "abc", "a.b.c", "x" * 2000 + ".y"])
def test_missing_or_malformed(value):
    assert _reason(value) == "ceremony.missing"


def test_tampered_payload_or_mac():
    value = seal(KEY, new_ceremony("authentication", None, now=NOW))
    body, mac = value.split(".")
    forged_body = _b64(json.dumps({"kind": "authentication"}).encode())
    assert _reason(f"{forged_body}.{mac}") == "ceremony.tampered"
    assert _reason(f"{body}.{mac[:-1]}") == "ceremony.tampered"
    assert _reason(f"{body}.!!!") == "ceremony.tampered"
    assert _reason(value, key=b"k" * 32) == "ceremony.tampered"


def test_validly_signed_garbage_is_malformed():
    body = _b64(b"not json")
    assert _reason(f"{body}.{_b64(_mac(KEY, body))}") == "ceremony.malformed"
    body = _b64(json.dumps({"kind": "authentication", "challenge": "AA", "subject": None,
                            "exp": int(NOW.timestamp()) + 60}).encode())  # fmt: skip
    assert _reason(f"{body}.{_b64(_mac(KEY, body))}") == "ceremony.malformed"  # short challenge


def test_wrong_kind():
    value = seal(KEY, new_ceremony("registration", None, now=NOW))
    assert _reason(value, kind="authentication") == "ceremony.wrong_kind"


def test_expiry_is_five_minutes():
    value = seal(KEY, new_ceremony("authentication", None, now=NOW))
    edge = NOW + timedelta(seconds=CEREMONY_MAX_AGE_S)
    assert open_sealed(KEY, value, kind="authentication", now=edge - timedelta(seconds=1))
    assert _reason(value, now=edge) == "ceremony.expired"
