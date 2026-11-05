"""A software WebAuthn authenticator for tests (phase 1 step 3, §10.2).

Built from `cryptography` (an ES256 key) and `cbor2`, it produces exactly what a browser
hands to @simplewebauthn/browser's caller: for registration, clientDataJSON and an
attestationObject (fmt "none", authenticator data with UP|UV|AT|BE|BS flags, AAGUID,
credential id and COSE key); for authentication, clientDataJSON, authenticatorData and a DER
ECDSA signature over authData || SHA-256(clientDataJSON), plus the userHandle.

Every field a test wants to break is a keyword: origin, rp_id, challenge, flags, the sign
counter, the userHandle, the attestation format. So the real py_webauthn verify_* code paths
run, and the rejections are py_webauthn's (or ours), not a mock's.
"""

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from uuid import UUID

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40
DEFAULT_AAGUID = UUID("fbfc3007-154e-4ecc-8c0b-6e020557d7bd")  # any non-zero model id
_UNSET = object()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class SoftAuthenticator:
    """One discoverable ES256 credential for one RP. `counts=False` behaves like a synced
    passkey (the counter is always 0)."""

    rp_id: str
    origin: str
    counts: bool = True
    backup_eligible: bool = True
    backed_up: bool = True
    aaguid: UUID = DEFAULT_AAGUID
    transports: list[str] = field(default_factory=lambda: ["internal", "hybrid"])
    sign_count: int = 0
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    user_handle: bytes | None = None  # learnt from the registration options

    def clone(self) -> "SoftAuthenticator":
        """Same key and credential id, same counter: a cloned authenticator."""
        return SoftAuthenticator(
            rp_id=self.rp_id,
            origin=self.origin,
            counts=self.counts,
            backup_eligible=self.backup_eligible,
            backed_up=self.backed_up,
            aaguid=self.aaguid,
            transports=list(self.transports),
            sign_count=self.sign_count,
            credential_id=self.credential_id,
            key=self.key,
            user_handle=self.user_handle,
        )

    # --- registration -----------------------------------------------------------------

    def register(
        self,
        options: dict,
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        challenge: str | None = None,
        flags: int | None = None,
        fmt: str = "none",
        type_: str = "webauthn.create",
    ) -> dict:
        """navigator.credentials.create() for these creation options (JSON, as py_webauthn's
        options_to_json_dict produces them)."""
        self.user_handle = unb64(options["user"]["id"])
        client_data = self._client_data(type_, challenge or options["challenge"], origin)
        if flags is None:
            flags = UP | UV | AT | (BE if self.backup_eligible else 0)
            flags |= BS if self.backed_up else 0
        auth_data = (
            self._rp_hash(rp_id)
            + bytes([flags])
            + struct.pack(">I", self.sign_count)
            + self.aaguid.bytes
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self.cose_public_key()
        )
        if fmt == "none":
            statement: dict = {}
        else:  # "packed" self-attestation: a valid statement that is not "none"
            signature = self._sign(auth_data + hashlib.sha256(client_data).digest())
            statement = {"alg": -7, "sig": signature}
        attestation = cbor2.dumps({"fmt": fmt, "attStmt": statement, "authData": auth_data})
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "attestationObject": b64(attestation),
                "transports": list(self.transports),
            },
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
        }

    # --- authentication ---------------------------------------------------------------

    def assertion(
        self,
        options: dict,
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        challenge: str | None = None,
        flags: int | None = None,
        sign_count: int | None = None,
        user_handle: bytes | None | object = _UNSET,
        type_: str = "webauthn.get",
    ) -> dict:
        """navigator.credentials.get() for these request options. Advances the counter
        (unless counts=False) unless `sign_count` pins it."""
        if sign_count is None:
            if self.counts:
                self.sign_count += 1
            sign_count = self.sign_count if self.counts else 0
        client_data = self._client_data(type_, challenge or options["challenge"], origin)
        if flags is None:
            flags = UP | UV | (BE if self.backup_eligible else 0) | (BS if self.backed_up else 0)
        auth_data = self._rp_hash(rp_id) + bytes([flags]) + struct.pack(">I", sign_count)
        signature = self._sign(auth_data + hashlib.sha256(client_data).digest())
        handle = self.user_handle if user_handle is _UNSET else user_handle
        response = {
            "clientDataJSON": b64(client_data),
            "authenticatorData": b64(auth_data),
            "signature": b64(signature),
        }
        if handle is not None:
            response["userHandle"] = b64(handle)
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": response,
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
        }

    # --- primitives -------------------------------------------------------------------

    def cose_public_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _client_data(self, type_: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps(
            {
                "type": type_,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def _rp_hash(self, rp_id: str | None) -> bytes:
        return hashlib.sha256((rp_id or self.rp_id).encode()).digest()

    def _sign(self, data: bytes) -> bytes:
        return self.key.sign(data, ec.ECDSA(hashes.SHA256()))
