"""API test fixtures (docs/phases/phase-1-step-3.md §10.1).

The app is built by create_app on the real test database (tests/db/conftest.py's throwaway
database, migrated as the owner, used as hausbuch_app), a LocalBlobStore under tmp_path and a
FakeResolver: the SessionResolver protocol over a dict, so domain tests need no WebAuthn.
Workspaces come from `fresh_workspaces` (a superuser insert); provisioning through
provision_workspace arrives with chunk 2 and is not needed by a fake resolver.

client_a and client_b are signed in to two different workspaces and send the configured
Origin by default, as the browser does. Helpers live in api_support.py.
"""

from collections.abc import Iterator

import pytest
from api_support import TOKEN_A, TOKEN_B, FakeResolver, auth_for, client_for, make_settings
from db.conftest import (  # noqa: F401  (pytest finds fixtures by name in this module)
    ADMIN_URL,
    app_engine,
    dbname,
    fresh_workspaces,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hausbuch.api.app import create_app
from hausbuch.api.auth_context import AuthContext
from hausbuch.settings import Settings
from hausbuch.storage import LocalBlobStore


def pytest_collection_modifyitems(items):
    """Like tests/db: tests marked `db` are skipped without HAUSBUCH_TEST_ADMIN_URL."""
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="HAUSBUCH_TEST_ADMIN_URL not set")
    for item in items:
        if "tests/api/" in item.nodeid and item.get_closest_marker("db"):
            item.add_marker(skip)


@pytest.fixture
def sessions(fresh_workspaces) -> dict[str, AuthContext]:  # noqa: F811
    a, b = fresh_workspaces
    return {TOKEN_A: auth_for(a), TOKEN_B: auth_for(b)}


@pytest.fixture
def auth_a(sessions) -> AuthContext:
    return sessions[TOKEN_A]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def blob_store(tmp_path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def api_app(app_engine, settings, blob_store, sessions) -> FastAPI:  # noqa: F811
    return create_app(settings, app_engine, blob_store, FakeResolver(sessions), worker=None)


def _client(app: FastAPI, token: str | None) -> Iterator[TestClient]:
    client = client_for(app, token)
    yield client
    client.close()


@pytest.fixture
def client_a(api_app) -> Iterator[TestClient]:
    yield from _client(api_app, TOKEN_A)


@pytest.fixture
def client_b(api_app) -> Iterator[TestClient]:
    yield from _client(api_app, TOKEN_B)


@pytest.fixture
def anonymous(api_app) -> Iterator[TestClient]:
    yield from _client(api_app, None)
