"""Passkey sign-in: the API's auth models (docs/phases/phase-1-step-3.md §6).

Read shapes never carry a secret or a hash: no token, no token_hash, no public key. WebAuthn
option and credential payloads are opaque JSON (dict[str, JsonValue]): py_webauthn produces
and verifies them, @simplewebauthn/browser consumes and produces them.

Bodies that carry a raw enrollment token hide their input from validation errors, because the
token is a bearer secret until it is used.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import ConfigDict, Field, JsonValue, field_validator

from .common import Schema
from .workspace import AppUser, Workspace

# 32 random bytes, base64url without padding (secrets.token_urlsafe(32)): the raw token in an
# enrollment URL. Only its SHA-256 is stored (auth_invite.token_hash).
EnrollmentToken = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
# auth_passkey.label CHECK: 1-100 characters, e.g. "iPhone von Luis".
PasskeyLabelText = Annotated[str, Field(min_length=1, max_length=100)]
# A WebAuthn PublicKeyCredential as the browser library serialises it (opaque here).
CredentialJson = dict[str, JsonValue]

_HIDE_INPUT = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


# --- Read shapes ---------------------------------------------------------------------


class Passkey(Schema):
    """GET /auth/passkeys: one passkey of the signed-in user."""

    id: UUID
    label: PasskeyLabelText
    created_at: datetime
    last_used_at: datetime | None
    backed_up: bool  # synced (iCloud / Google); shown as a hint, not enforced
    aaguid: UUID | None  # the authenticator model, when it reports one


class Session(Schema):
    """The current session (Me.session). The cookie token is never part of any response."""

    id: UUID
    passkey_id: UUID | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime  # absolute
    idle_expires_at: datetime  # slid forward on use


class Invite(Schema):
    """A pending enrollment invite. enrollment_url is set only in the POST /auth/invites
    response, the one time the raw token exists; GET /auth/invites never has it."""

    id: UUID
    user: AppUser
    enrollment_url: str | None = None
    expires_at: datetime


class Me(Schema):
    """GET /auth/me, and the response of a successful sign-in or enrollment."""

    user: AppUser
    workspace: Workspace
    session: Session


# --- Request bodies -------------------------------------------------------------------


class InviteCreate(Schema):
    """POST /auth/invites: a household member to invite (creates their app_user row)."""

    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")  # AppUser.email, before lower-casing
    display_name: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def _lower_case(cls, value: str) -> str:
        return value.lower()  # app_user.email is stored lower-case


class EnrollmentOptionsRequest(Schema):
    """POST /auth/enroll/options."""

    model_config = _HIDE_INPUT

    token: EnrollmentToken


class EnrollmentVerifyRequest(Schema):
    """POST /auth/enroll/verify."""

    model_config = _HIDE_INPUT

    token: EnrollmentToken
    credential: CredentialJson
    label: PasskeyLabelText


class LoginVerifyRequest(Schema):
    """POST /auth/login/verify (POST /auth/login/options takes an empty body)."""

    credential: CredentialJson


class PasskeyVerifyRequest(Schema):
    """POST /auth/passkeys/verify: a further passkey for the signed-in user."""

    credential: CredentialJson
    label: PasskeyLabelText


class PasskeyLabel(Schema):
    """PATCH /auth/passkeys/{id}."""

    label: PasskeyLabelText
