"""INTERIM (chunk 3a): the auth endpoint contract of docs/phases/phase-1-step-3.md §6.3 as an
OpenAPI document, until chunk 2 ships the real routes and chunk 3b regenerates
src/api/types.ts from `hausbuch openapi`. Chunk 3b deletes this script and
src/api/interim-auth-types.ts.

Paths, methods, bodies and status codes are transcribed from the §6.3 table by hand; the
body and response *shapes* are the real models from hausbuch.schemas.auth and
hausbuch.schemas.api, so they cannot drift from chunk 0's contract. WebAuthn options are
opaque JSON (§5).

Regenerate (from backend/), then put the header comment of the current
src/api/interim-auth-types.ts back on top of the output:
    uv run python ../frontend/scripts/interim-auth-openapi.py > /tmp/auth-openapi.json
    (cd ../frontend && npx openapi-typescript /tmp/auth-openapi.json -o /tmp/auth-paths.ts)
"""

import json
import sys
from uuid import UUID

from fastapi import APIRouter, FastAPI
from pydantic import JsonValue

from hausbuch.schemas.api import ErrorResponse
from hausbuch.schemas.auth import (
    EnrollmentOptionsRequest,
    EnrollmentVerifyRequest,
    Invite,
    InviteCreate,
    LoginVerifyRequest,
    Me,
    Passkey,
    PasskeyLabel,
    PasskeyVerifyRequest,
)
from hausbuch.schemas.common import Schema

# PublicKeyCredentialCreationOptionsJSON / ...RequestOptionsJSON, as py_webauthn's
# options_to_json produces them: opaque to the API contract.
OptionsJson = dict[str, JsonValue]


class EmptyBody(Schema):
    """§6.3: `POST /auth/login/options` takes `{}`."""


ERRORS = {"4XX": {"model": ErrorResponse}, "5XX": {"model": ErrorResponse}}
router = APIRouter(prefix="/auth", responses=ERRORS)


@router.post("/enroll/options", response_model=OptionsJson)
def enroll_options(body: EnrollmentOptionsRequest): ...


@router.post("/enroll/verify", status_code=201, response_model=Me)
def enroll_verify(body: EnrollmentVerifyRequest): ...


@router.post("/login/options", response_model=OptionsJson)
def login_options(body: EmptyBody): ...


@router.post("/login/verify", response_model=Me)
def login_verify(body: LoginVerifyRequest): ...


@router.post("/logout", status_code=204)
def logout() -> None: ...


@router.post("/logout-all", status_code=204)
def logout_all() -> None: ...


@router.get("/me", response_model=Me)
def me(): ...


@router.get("/passkeys", response_model=list[Passkey])
def list_passkeys(): ...


@router.post("/passkeys/options", response_model=OptionsJson)
def passkey_options(body: EmptyBody): ...


@router.post("/passkeys/verify", status_code=201, response_model=Passkey)
def passkey_verify(body: PasskeyVerifyRequest): ...


@router.patch("/passkeys/{passkey_id}", response_model=Passkey)
def rename_passkey(passkey_id: UUID, body: PasskeyLabel): ...


@router.delete("/passkeys/{passkey_id}", status_code=204)
def delete_passkey(passkey_id: UUID) -> None: ...


@router.post("/invites", status_code=201, response_model=Invite)
def create_invite(body: InviteCreate): ...


@router.get("/invites", response_model=list[Invite])
def list_invites(): ...


@router.delete("/invites/{invite_id}", status_code=204)
def delete_invite(invite_id: UUID) -> None: ...


app = FastAPI(title="Hausbuch (interim auth contract)", version="0")
app.include_router(router, prefix="/api")
spec = app.openapi()

# FastAPI adds its own 422 HTTPValidationError to every route with a body; Hausbuch's 422 is
# ErrorResponse (§3.5), already covered by "4XX".
for operations in spec["paths"].values():
    for operation in operations.values():
        operation["responses"].pop("422", None)
for name in ("HTTPValidationError", "ValidationError"):
    spec["components"]["schemas"].pop(name, None)

json.dump(spec, sys.stdout, indent=2)
sys.stdout.write("\n")
