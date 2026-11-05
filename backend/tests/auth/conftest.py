"""Fixtures for the auth tests (phase 1 step 3, §10.2).

The database helpers are tests/db/conftest.py's (throwaway database owned by hausbuch_owner,
migrated as that role, engines that SET ROLE): that module is loaded here under its own name,
because two conftest modules cannot both be imported as `conftest`. This directory gets its
own throwaway database. Tests that need it are skipped only when HAUSBUCH_TEST_ADMIN_URL is
not set.

The app under test is the production composition root, hausbuch.api.main.build_app, on an
app-role engine, with workspaces provisioned through provision_workspace on an owner-role
engine: the same path production takes.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import psycopg
import psycopg.sql
import pytest
from authenticator import SoftAuthenticator
from fastapi.testclient import TestClient

from hausbuch.settings import Settings

_DB_CONFTEST = Path(__file__).resolve().parents[1] / "db" / "conftest.py"
_spec = importlib.util.spec_from_file_location("hausbuch_tests_db_conftest", _DB_CONFTEST)
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)

ADMIN_URL = db.ADMIN_URL
OWNER, APP = db.OWNER, db.APP
RP_ID = "testserver"
ORIGIN = "https://testserver"


def pytest_collection_modifyitems(items):
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="HAUSBUCH_TEST_ADMIN_URL not set")
    for item in items:
        if "tests/auth/" in item.nodeid and "dbname" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


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


# --- Database ----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dbname() -> Iterator[str]:
    name = db.create_database()
    try:
        db.alembic(name, "upgrade", "head")
        yield name
    finally:
        db.drop_database(name)


@pytest.fixture
def app_engine(dbname):
    engine = db.make_engine(dbname, APP)
    yield engine
    engine.dispose()


@pytest.fixture
def owner_engine(dbname):
    engine = db.make_engine(dbname, OWNER)
    yield engine
    engine.dispose()


@pytest.fixture
def sql(dbname):
    """sql(query, params, role=OWNER, workspace=None) -> rows: one committed statement acting
    as `role` (RLS applies), for arranging and inspecting rows the API does not expose."""

    def _sql(query: str, params=(), *, role: str = OWNER, workspace: uuid.UUID | None = None):
        with psycopg.connect(ADMIN_URL, dbname=dbname, autocommit=True) as conn:
            conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
            with conn.transaction():
                if workspace is not None:
                    conn.execute(
                        "SELECT set_config('app.workspace_id', %s, true)", (str(workspace),)
                    )
                cursor = conn.execute(query, params)
                return cursor.fetchall() if cursor.description else cursor.rowcount

    return _sql


# --- Provisioned workspace and the app ---------------------------------------------------


def unique_email(name: str = "luis") -> str:
    return f"{name}-{uuid.uuid4().hex[:12]}@example.org"


@dataclass
class Household:
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    token: str  # the bootstrap enrollment token


@pytest.fixture
def household(owner_engine) -> Household:
    """A workspace provisioned as the schema owner, with its first user and invite."""
    from hausbuch.auth.provisioning import provision_workspace

    email = unique_email()
    provisioned = provision_workspace(
        owner_engine, name="Haushalt Rivas", email=email, display_name="Luis", now=datetime.now(UTC)
    )
    return Household(provisioned.workspace_id, provisioned.user_id, email, provisioned.invite.token)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def api(app_engine, settings, tmp_path):
    """The production app (build_app) on the app-role engine."""
    from hausbuch.api.main import build_app
    from hausbuch.storage import LocalBlobStore

    return build_app(settings, engine=app_engine, blob_store=LocalBlobStore(tmp_path / "blobs"))


def new_client(api, **kwargs) -> TestClient:
    """A browser: its own cookie jar, https (so Secure cookies round-trip), same Origin."""
    return TestClient(api, base_url=ORIGIN, headers={"Origin": ORIGIN}, **kwargs)


@pytest.fixture
def client(api) -> TestClient:
    return new_client(api)


@pytest.fixture
def authenticator() -> SoftAuthenticator:
    return SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)


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


def session_cookie(client: TestClient) -> str | None:
    return client.cookies.get("__Host-hb_session")


@pytest.fixture
def signed_in(client, household, authenticator):
    """A browser enrolled with the bootstrap invite, holding a session."""
    response = enroll(client, household.token, authenticator)
    assert response.status_code == 201, response.text
    return client
