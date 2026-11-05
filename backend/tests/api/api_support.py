"""Helpers shared by the API tests (imported as `api_support`; tests/api is on sys.path).

Kept out of conftest.py: tests/api, tests/auth and tests/db each have a conftest.py, all
importable as `conftest`, so test modules import helpers from uniquely named support modules
(this one, tests/auth/auth_support.py, tests/db/db_support.py), never from `conftest`.
"""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hausbuch.api.auth_context import SESSION_COOKIE, AuthContext
from hausbuch.settings import Settings

ORIGIN = "https://testserver"  # HAUSBUCH_ORIGIN of the test app; plain http is localhost-only
TOKEN_A = "a" * 43
TOKEN_B = "b" * 43
TEST_IBAN_KEY = bytes(range(32))  # the same test key as tests/db/db_support.py
# A valid German IBAN (the standard example) and one with wrong check digits.
GOOD_IBAN = "DE89 3704 0044 0532 0130 00"
BAD_CHECKSUM_IBAN = "DE00 3704 0044 0532 0130 00"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/hausbuch",  # unused
        "iban_hmac_key": TEST_IBAN_KEY,
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": ORIGIN,
        "blob_store": "local",
        "blob_root": tmp_path / "blobs",
    } | overrides
    return Settings(**values)


class FakeResolver:
    """The SessionResolver protocol (§3.1) over a dict of token -> AuthContext."""

    def __init__(self, sessions: dict[str, AuthContext]) -> None:
        self.sessions = sessions

    def resolve(self, token: str, *, now: datetime) -> AuthContext | None:
        return self.sessions.get(token)


def auth_for(workspace_id: UUID) -> AuthContext:
    return AuthContext(
        workspace_id=workspace_id, user_id=uuid4(), session_id=uuid4(), passkey_id=None
    )


def client_for(app: FastAPI, token: str | None, *, origin: str | None = ORIGIN) -> TestClient:
    """A client with the session cookie of `token` (None = signed out) and, like a browser,
    an Origin header. The lifespan is not run (no `with`): tests/api/test_lifespan.py covers
    it, and a client's exit would dispose the shared engine and start draining."""
    headers = {} if origin is None else {"Origin": origin}
    if token is not None:
        headers["Cookie"] = f"{SESSION_COOKIE}={token}"
    return TestClient(app, base_url=ORIGIN, headers=headers, raise_server_exceptions=True)


# --- Request bodies -------------------------------------------------------------------

ADDRESS = {
    "street": "Lindenstraße",
    "house_number": "12",
    "postal_code": "10115",
    "city": "Berlin",
}


def property_body(**overrides: Any) -> dict[str, Any]:
    return {
        "label": "Wohnung Lindenstraße",
        "kind": "etw",
        "address": ADDRESS,
        "build_year": 1994,
        "notarized_on": "2024-03-15",
        "purchase_date": "2024-05-01",
        "purchase_price": "289000.00",
    } | overrides


def loan_body(property_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "property_id": property_id,
        "lender": "Sparkasse Musterstadt",
        "principal": "240000.00",
        "nominal_rate": "0.0365",
        "monthly_payment": "1130.00",
        "disbursed_on": "2024-04-30",
        "first_payment_on": "2024-05-30",
        "fixed_until": "2034-04-30",
    } | overrides


def ok(response, status: int = 200) -> Any:
    """The JSON body, after checking the status (the body is shown when it differs)."""
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def create(client: TestClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    return ok(client.post(path, json=body), 201)


def new_property(client: TestClient, **overrides: Any) -> dict[str, Any]:
    return create(client, "/api/properties", property_body(**overrides))


def new_unit(client: TestClient, property_id: str, **overrides: Any) -> dict[str, Any]:
    body = {"property_id": property_id, "label": "WE 3", "floor_area_m2": "68.40"} | overrides
    return create(client, "/api/units", body)


def new_tenant(client: TestClient, **overrides: Any) -> dict[str, Any]:
    return create(client, "/api/tenants", {"full_name": "Anna Beispiel"} | overrides)


def tenancy_body(unit_id: str, tenant_ids: Iterable[str], **overrides: Any) -> dict[str, Any]:
    ids = list(tenant_ids)
    parties = [{"tenant_id": ids[0], "role": "primary"}] + [
        {"tenant_id": t, "role": "co_tenant"} for t in ids[1:]
    ]
    return {
        "unit_id": unit_id,
        "parties": parties,
        "start_date": "2024-07-01",
        "rent_model": "fixed",
    } | overrides


def new_tenancy(client: TestClient, unit_id: str, tenant_id: str, **overrides: Any) -> dict:
    return create(client, "/api/tenancies", tenancy_body(unit_id, [tenant_id], **overrides))


def flat(client: TestClient) -> dict[str, dict[str, Any]]:
    """A property with one unit, one tenant and a running tenancy."""
    prop = new_property(client)
    unit = new_unit(client, prop["id"])
    tenant = new_tenant(client)
    tenancy = new_tenancy(client, unit["id"], tenant["id"])
    return {"property": prop, "unit": unit, "tenant": tenant, "tenancy": tenancy}


def upload(
    client: TestClient,
    data: bytes = b"%PDF-1.7 Kaufvertrag",
    media_type: str = "application/pdf",
    filename: str = "kaufvertrag.pdf",
    **form: str,
):
    return client.post("/api/documents", files={"file": (filename, data, media_type)}, data=form)
