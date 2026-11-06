"""Registration and authentication ceremonies over py_webauthn 3.0.1 (§6.1, §6.3).

Verification parameters, on both ceremonies: the expected origin is exactly
settings.origin, the RP id is settings.rp_id, user presence and user verification are
required, and the challenge comes from the signed ceremony cookie. Registration asks for
attestation "none" and accepts only fmt "none" (no attestation verification, no MDS).
Authentication checks the signature against the stored COSE key, requires the userHandle to
be the passkey's user, and stores the new sign counter atomically (passkeys.record_assertion).

Flow of every verify: lookup (read-only, hausbuch.auth.lookup) -> py_webauthn verification
(no transaction open) -> one workspace_transaction for every write.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import webauthn
from pydantic import JsonValue
from sqlalchemy import Engine, select, text
from webauthn.helpers import options_to_json_dict
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AttestationFormat,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from hausbuch.api.auth_context import AuthContext
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import app_user
from hausbuch.schemas.auth import CredentialJson, Me, Passkey
from hausbuch.schemas.events import EventType, UserEnrolled, UserPasskeyEnrolled, UserSignedIn
from hausbuch.settings import Settings

from . import lookup, tokens
from .ceremony import CEREMONY_MAX_AGE_S, Ceremony, Subject, new_ceremony
from .errors import AuthenticationFailed
from .events import append_user_event
from .invites import redeem_invite
from .passkeys import NewCredential, add_passkey, credential_ids, record_assertion
from .sessions import load_me, open_session

log = logging.getLogger(__name__)

RP_NAME = "Hausbuch"
MAX_CREDENTIAL_ID_BYTES = 1023  # WebAuthn's limit, and the auth_passkey CHECK
_TIMEOUT_MS = CEREMONY_MAX_AGE_S * 1000  # the browser prompt lives as long as the cookie
_READ_ONLY = text("SET TRANSACTION READ ONLY")
_TRANSPORTS = {t.value for t in AuthenticatorTransport}
# Malformed input raises these from py_webauthn's parsers (cbor2 and binascii errors are
# ValueErrors). Anything else is a bug and stays a 500.
_INVALID = (WebAuthnException, ValueError, TypeError, KeyError, IndexError)

Options = dict[str, JsonValue]


@dataclass(frozen=True)
class SignedIn:
    token: str  # the new session's cookie value
    me: Me


# --- py_webauthn wrappers --------------------------------------------------------------


def registration_options(
    settings: Settings,
    challenge: bytes,
    *,
    user_id: UUID,
    user_name: str,
    display_name: str,
    exclude: list[bytes],
) -> Options:
    options = webauthn.generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=RP_NAME,
        user_id=user_id.bytes,
        user_name=user_name,
        user_display_name=display_name,
        challenge=challenge,
        timeout=_TIMEOUT_MS,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in exclude],
    )
    return options_to_json_dict(options)


def authentication_options(settings: Settings, challenge: bytes) -> Options:
    """Empty allowCredentials: the authenticator offers its discoverable credentials."""
    options = webauthn.generate_authentication_options(
        rp_id=settings.rp_id,
        challenge=challenge,
        timeout=_TIMEOUT_MS,
        allow_credentials=[],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json_dict(options)


def verify_registration(
    settings: Settings, credential: CredentialJson, challenge: bytes
) -> NewCredential:
    try:
        verified = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=settings.origin,
            require_user_presence=True,
            require_user_verification=True,
        )
    except _INVALID as exc:
        raise _failed("registration.invalid", exc) from None
    if verified.fmt != AttestationFormat.NONE:
        raise AuthenticationFailed("registration.attestation_not_none")
    if not 1 <= len(verified.credential_id) <= MAX_CREDENTIAL_ID_BYTES:
        raise AuthenticationFailed("registration.credential_id_length")
    aaguid = UUID(verified.aaguid)
    return NewCredential(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        aaguid=None if aaguid.int == 0 else aaguid,  # all zeros = the model is not reported
        transports=_transports(credential),
        backup_eligible=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
        backed_up=verified.credential_backed_up,
    )


def credential_id_of(credential: CredentialJson) -> bytes:
    """The credential id an assertion names (rawId), checked against id."""
    raw_id = credential.get("rawId")
    if not isinstance(raw_id, str) or credential.get("id") != raw_id:
        raise AuthenticationFailed("assertion.malformed")
    try:
        value = webauthn.base64url_to_bytes(raw_id)
    except _INVALID:
        raise AuthenticationFailed("assertion.malformed") from None
    if not 1 <= len(value) <= MAX_CREDENTIAL_ID_BYTES:
        raise AuthenticationFailed("assertion.malformed")
    return value


def verify_assertion(
    settings: Settings,
    credential: CredentialJson,
    challenge: bytes,
    passkey: lookup.PasskeyRecord,
) -> int:
    """The assertion's new sign counter, once it has verified against the stored key."""
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=settings.origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except _INVALID as exc:
        raise _failed("assertion.invalid", exc) from None
    if not verified.user_verified:  # required above; kept explicit (§6.1)
        raise AuthenticationFailed("assertion.not_user_verified")
    if _user_handle(credential) != passkey.user_id.bytes:
        raise AuthenticationFailed("assertion.user_handle")
    return verified.new_sign_count


# --- Ceremonies -------------------------------------------------------------------------


def enrollment_options(
    engine: Engine, settings: Settings, token: str, *, now: datetime
) -> tuple[Options, Ceremony]:
    """POST /auth/enroll/options: creation options for the invited user."""
    invite = _usable_invite(engine, token, now)
    options, ceremony = _creation_options(
        engine, settings, invite.workspace_id, invite.user_id, Subject(invite_id=invite.id), now
    )
    return options, ceremony


def enroll(
    engine: Engine,
    settings: Settings,
    *,
    token: str,
    credential: CredentialJson,
    label: str,
    ceremony: Ceremony,
    now: datetime,
) -> SignedIn:
    """POST /auth/enroll/verify: redeem the invite, store the passkey, open a session; one
    transaction, so a failure leaves the invite usable and nothing stored."""
    invite = _usable_invite(engine, token, now)
    if ceremony.subject != Subject(invite_id=invite.id):
        raise AuthenticationFailed("ceremony.subject")
    new = verify_registration(settings, credential, ceremony.challenge)
    with workspace_transaction(engine, invite.workspace_id) as conn:
        if not redeem_invite(conn, invite.id, now=now):
            raise AuthenticationFailed("invite.used")
        passkey = add_passkey(
            conn,
            workspace_id=invite.workspace_id,
            user_id=invite.user_id,
            credential=new,
            label=label,
            now=now,
        )
        correlation = uuid4()
        who = {"workspace_id": invite.workspace_id, "user_id": invite.user_id, "now": now}
        append_user_event(
            conn,
            EventType.USER_ENROLLED,
            UserEnrolled(invite_id=invite.id),
            correlation_id=correlation,
            **who,
        )
        append_user_event(
            conn,
            EventType.USER_PASSKEY_ENROLLED,
            UserPasskeyEnrolled(passkey_id=passkey.id, label=passkey.label),
            correlation_id=correlation,
            **who,
        )
        return _open(
            conn,
            settings,
            invite.workspace_id,
            invite.user_id,
            passkey.id,
            now,
            correlation_id=correlation,
        )


def login_options(settings: Settings, *, now: datetime) -> tuple[Options, Ceremony]:
    """POST /auth/login/options."""
    ceremony = new_ceremony("authentication", None, now=now)
    return authentication_options(settings, ceremony.challenge), ceremony


def sign_in(
    engine: Engine,
    settings: Settings,
    *,
    credential: CredentialJson,
    ceremony: Ceremony,
    now: datetime,
) -> SignedIn:
    """POST /auth/login/verify (§6.3)."""
    if ceremony.subject is not None:
        raise AuthenticationFailed("ceremony.subject")
    passkey = lookup.find_passkey(engine, credential_id_of(credential))
    if passkey is None:
        raise AuthenticationFailed("assertion.unknown_credential")
    new_count = verify_assertion(settings, credential, ceremony.challenge, passkey)
    with workspace_transaction(engine, passkey.workspace_id) as conn:
        if not record_assertion(conn, passkey.id, new_count, now):
            raise AuthenticationFailed("assertion.sign_count_not_increased")
        return _open(conn, settings, passkey.workspace_id, passkey.user_id, passkey.id, now)


def passkey_options(
    engine: Engine, settings: Settings, auth: AuthContext, *, now: datetime
) -> tuple[Options, Ceremony]:
    """POST /auth/passkeys/options: a further passkey for the signed-in user."""
    subject = Subject(user_id=auth.user_id, workspace_id=auth.workspace_id)
    return _creation_options(engine, settings, auth.workspace_id, auth.user_id, subject, now)


def register_passkey(
    engine: Engine,
    settings: Settings,
    auth: AuthContext,
    *,
    credential: CredentialJson,
    label: str,
    ceremony: Ceremony,
    now: datetime,
) -> Passkey:
    """POST /auth/passkeys/verify."""
    if ceremony.subject != Subject(user_id=auth.user_id, workspace_id=auth.workspace_id):
        raise AuthenticationFailed("ceremony.subject")
    new = verify_registration(settings, credential, ceremony.challenge)
    with workspace_transaction(engine, auth.workspace_id) as conn:
        passkey = add_passkey(
            conn,
            workspace_id=auth.workspace_id,
            user_id=auth.user_id,
            credential=new,
            label=label,
            now=now,
        )
        append_user_event(
            conn,
            EventType.USER_PASSKEY_ENROLLED,
            UserPasskeyEnrolled(passkey_id=passkey.id, label=passkey.label),
            workspace_id=auth.workspace_id,
            user_id=auth.user_id,
            now=now,
        )
        return passkey


# --- Helpers ----------------------------------------------------------------------------


def _usable_invite(engine: Engine, token: str, now: datetime) -> lookup.InviteRecord:
    if not tokens.is_well_formed(token):
        raise AuthenticationFailed("invite.malformed")
    invite = lookup.find_invite(engine, tokens.token_hash(token))
    if invite is None or not invite.usable(now):
        raise AuthenticationFailed("invite.unusable")  # unknown, used and expired look alike
    return invite


def _creation_options(
    engine: Engine,
    settings: Settings,
    workspace_id: UUID,
    user_id: UUID,
    subject: Subject,
    now: datetime,
) -> tuple[Options, Ceremony]:
    with workspace_transaction(engine, workspace_id) as conn:
        conn.execute(_READ_ONLY)
        user = conn.execute(
            select(app_user.c.email, app_user.c.display_name).where(app_user.c.id == user_id)
        ).one()
        exclude = credential_ids(conn, user_id)
    ceremony = new_ceremony("registration", subject, now=now)
    options = registration_options(
        settings,
        ceremony.challenge,
        user_id=user_id,
        user_name=user.email,
        display_name=user.display_name,
        exclude=exclude,
    )
    return options, ceremony


def _open(
    conn, settings: Settings, workspace_id, user_id, passkey_id, now, *, correlation_id=None
) -> SignedIn:
    """Open the session and record user.signed_in (§10), in the caller's transaction: the
    workspace is known from the passkey or the invite by then."""
    opened = open_session(
        conn, settings, workspace_id=workspace_id, user_id=user_id, passkey_id=passkey_id, now=now
    )
    append_user_event(
        conn,
        EventType.USER_SIGNED_IN,
        UserSignedIn(passkey_id=passkey_id),
        workspace_id=workspace_id,
        user_id=user_id,
        now=now,
        correlation_id=correlation_id,
    )
    me = load_me(conn, workspace_id=workspace_id, user_id=user_id, session_id=opened.session.id)
    return SignedIn(token=opened.token, me=me)


def _user_handle(credential: CredentialJson) -> bytes | None:
    response: Any = credential.get("response")
    handle = response.get("userHandle") if isinstance(response, dict) else None
    if not isinstance(handle, str) or not handle:
        return None
    try:
        return webauthn.base64url_to_bytes(handle)
    except _INVALID:
        return None


def _transports(credential: CredentialJson) -> list[str] | None:
    response: Any = credential.get("response")
    values = response.get("transports") if isinstance(response, dict) else None
    if not isinstance(values, list):
        return None
    return sorted({v for v in values if isinstance(v, str) and v in _TRANSPORTS})


def _failed(reason: str, exc: Exception) -> AuthenticationFailed:
    # py_webauthn's messages can quote client data (e.g. the origin it saw); log the type only.
    log.debug("webauthn verification failed: %s (%s)", reason, type(exc).__name__)
    return AuthenticationFailed(reason)
