"""The ceremony cookie: a WebAuthn challenge carried by the browser, signed by us (§6.3).

No challenge table: the payload {kind, challenge, subject, exp} is base64url JSON, signed
with HMAC-SHA256 under HAUSBUCH_SESSION_KEY, and lives 5 minutes in an HttpOnly cookie. The
verify endpoint checks signature, expiry, kind and subject, uses the challenge as
expected_challenge, and clears the cookie. Accepted tradeoff (Q4): within those 5 minutes a
stolen cookie plus a stolen assertion could be replayed; the sign counter catches that for
authenticators that count, and an attacker holding both already has a session.
"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .errors import AuthenticationFailed

CEREMONY_COOKIE = "__Host-hb_ceremony"
INSECURE_CEREMONY_COOKIE = "hb_ceremony"  # dev over http://localhost (no __Host- without Secure)
CEREMONY_MAX_AGE_S = 300
CHALLENGE_BYTES = 32
MAX_COOKIE_LENGTH = 1024  # a real one is ~300 characters; longer is refused unparsed

CeremonyKind = Literal["registration", "authentication"]
_DOMAIN = b"hausbuch.ceremony.v1\x00"  # domain separation: this key also signs nothing else


@dataclass(frozen=True)
class Subject:
    """Who the ceremony is for: an invite being redeemed, or a signed-in user adding a passkey.
    A sign-in has no subject (discoverable credentials name the user themselves)."""

    invite_id: UUID | None = None
    user_id: UUID | None = None
    workspace_id: UUID | None = None

    def as_json(self) -> dict[str, str]:
        return {k: str(v) for k, v in vars(self).items() if v is not None}


@dataclass(frozen=True)
class Ceremony:
    kind: CeremonyKind
    challenge: bytes
    subject: Subject | None
    exp: int  # unix seconds


def new_ceremony(kind: CeremonyKind, subject: Subject | None, *, now: datetime) -> Ceremony:
    return Ceremony(
        kind=kind,
        challenge=secrets.token_bytes(CHALLENGE_BYTES),
        subject=subject,
        exp=int(now.timestamp()) + CEREMONY_MAX_AGE_S,
    )


def seal(key: bytes, ceremony: Ceremony) -> str:
    """The cookie value: <payload>.<mac>, both base64url."""
    payload = {
        "kind": ceremony.kind,
        "challenge": _b64(ceremony.challenge),
        "subject": None if ceremony.subject is None else ceremony.subject.as_json(),
        "exp": ceremony.exp,
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_b64(_mac(key, body))}"


def open_sealed(key: bytes, value: str | None, *, kind: CeremonyKind, now: datetime) -> Ceremony:
    """The ceremony in the cookie, or AuthenticationFailed. Checks, in order: present and
    well-formed, signature, kind, expiry."""
    if not value or len(value) > MAX_COOKIE_LENGTH or value.count(".") != 1:
        raise AuthenticationFailed("ceremony.missing")
    body, mac = value.split(".")
    try:
        given = _unb64(mac)
    except ValueError:
        raise AuthenticationFailed("ceremony.tampered") from None
    if not hmac.compare_digest(given, _mac(key, body)):
        raise AuthenticationFailed("ceremony.tampered")
    try:
        payload = json.loads(_unb64(body))
        ceremony = Ceremony(
            kind=payload["kind"],
            challenge=_unb64(payload["challenge"]),
            subject=_subject(payload["subject"]),
            exp=int(payload["exp"]),
        )
    except (ValueError, KeyError, TypeError):
        raise AuthenticationFailed("ceremony.malformed") from None
    if ceremony.kind != kind:
        raise AuthenticationFailed("ceremony.wrong_kind")
    if int(now.timestamp()) >= ceremony.exp:
        raise AuthenticationFailed("ceremony.expired")
    if len(ceremony.challenge) != CHALLENGE_BYTES:
        raise AuthenticationFailed("ceremony.malformed")
    return ceremony


def _subject(value: object) -> Subject | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not set(value) <= {"invite_id", "user_id", "workspace_id"}:
        raise ValueError("bad subject")
    return Subject(**{k: UUID(v) for k, v in value.items()})


def _mac(key: bytes, body: str) -> bytes:
    return hmac.new(key, _DOMAIN + body.encode("ascii"), hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not isinstance(text, str) or not text.isascii():
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
