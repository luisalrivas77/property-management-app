"""Helpers shared by the auth tests (imported as `auth_support`; tests/auth is on sys.path while
its tests are collected). The fixtures built on them stay in tests/auth/conftest.py.

Kept out of conftest.py: tests/api, tests/auth and tests/db each have a conftest.py, all
importable as `conftest`, so `from conftest import ...` picks whichever was loaded last
(docs/phases/phase-1-step-3.md §10.7). The database helpers are tests/db/db_support.py's.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import psycopg
from authenticator import SoftAuthenticator
from db.db_support import ADMIN_URL
from fastapi.testclient import TestClient

from hausbuch.settings import Settings

RP_ID = "testserver"
ORIGIN = "https://testserver"


def make_settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": bytes(range(32)),
        "session_key": bytes(range(32, 64)),
        "rp_id": RP_ID,
        "origin": ORIGIN,
        "blob_store": "local",
        "blob_root": Path("/nonexistent"),
    } | overrides
    return Settings(**values)


def role_url(dbname: str, role: str | None) -> str:
    """A SQLAlchemy URL on `dbname` that logs in as the test superuser and acts as `role`
    from the start (libpq startup option `-c role=...`), so current_user is `role`, which is
    what every guard checks. The test roles are NOLOGIN; this is how a CLI or main.py test
    gets a real URL for them. role=None: the superuser itself."""
    info = psycopg.conninfo.conninfo_to_dict(ADMIN_URL)
    user, password = quote(info.get("user", "")), quote(info.get("password", ""))
    host, port = info.get("host", "localhost"), info.get("port", "5432")
    url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{dbname}"
    return url if role is None else f"{url}?options={quote(f'-c role={role}')}"


# --- Provisioned workspace and the app ---------------------------------------------------


def unique_email(name: str = "luis") -> str:
    return f"{name}-{uuid.uuid4().hex[:12]}@example.org"


@dataclass
class Household:
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    token: str  # the bootstrap enrollment token


def new_client(api, **kwargs) -> TestClient:
    """A browser: its own cookie jar, https (so Secure cookies round-trip), same Origin."""
    return TestClient(api, base_url=ORIGIN, headers={"Origin": ORIGIN}, **kwargs)


# --- Ceremony helpers ---------------------------------------------------------------------


def enroll(client: TestClient, token: str, device: SoftAuthenticator, label="iPhone", **kw):
    options = client.post("/api/auth/enroll/options", json={"token": token})
    assert options.status_code == 200, options.text
    credential = device.register(options.json(), **kw)
    return client.post(
        "/api/auth/enroll/verify", json={"token": token, "credential": credential, "label": label}
    )


def login(client: TestClient, device: SoftAuthenticator, **kw):
    options = client.post("/api/auth/login/options")
    assert options.status_code == 200, options.text
    credential = device.assertion(options.json(), **kw)
    return client.post("/api/auth/login/verify", json={"credential": credential})


def add_passkey(client: TestClient, device: SoftAuthenticator, label="Laptop", **kw):
    options = client.post("/api/auth/passkeys/options")
    assert options.status_code == 200, options.text
    credential = device.register(options.json(), **kw)
    return client.post("/api/auth/passkeys/verify", json={"credential": credential, "label": label})


def put_cookie(client: TestClient, name: str, value: str) -> None:
    """Replace a cookie the way a forging browser would. A bare cookies.set() would add a
    second cookie beside the server-set one, and the server would read the original."""
    client.cookies.delete(name)
    client.cookies.set(name, value, domain=RP_ID, path="/")


def session_cookie(client: TestClient) -> str | None:
    return client.cookies.get("__Host-hb_session")
