"""Fixtures for the auth tests (phase 1 step 3, §10.2).

The database helpers are tests/db/db_support.py's (throwaway database owned by hausbuch_owner,
migrated as that role, engines that SET ROLE). This directory gets its own throwaway database.
Tests that need it are skipped only when HAUSBUCH_TEST_ADMIN_URL is not set. Plain helpers
(ceremonies, clients, settings) live in auth_support.py: test modules import them from there,
never from `conftest` (§10.7).

The app under test is the production composition root, hausbuch.api.main.build_app, on an
app-role engine, with workspaces provisioned through provision_workspace on an owner-role
engine: the same path production takes.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import psycopg.sql
import pytest
from auth_support import (
    ORIGIN,
    RP_ID,
    Household,
    enroll,
    make_settings,
    new_client,
    unique_email,
)
from authenticator import SoftAuthenticator
from db import db_support as db
from db.db_support import ADMIN_URL, APP, OWNER
from fastapi.testclient import TestClient

from hausbuch.settings import Settings


def pytest_collection_modifyitems(items):
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="HAUSBUCH_TEST_ADMIN_URL not set")
    for item in items:
        if "tests/auth/" in item.nodeid and "dbname" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


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


@pytest.fixture
def client(api) -> TestClient:
    return new_client(api)


@pytest.fixture
def authenticator() -> SoftAuthenticator:
    return SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)


@pytest.fixture
def signed_in(client, household, authenticator):
    """A browser enrolled with the bootstrap invite, holding a session."""
    response = enroll(client, household.token, authenticator)
    assert response.status_code == 201, response.text
    return client
