"""The chunk 0 API stub (phase 1 step 3, §2, §3.1, §3.7): create_app's shape, the router
includes, the Worker protocol, and the current_auth dependency over a fake SessionResolver."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from hausbuch.api import app as app_module
from hausbuch.api.app import API_PREFIX, ROUTE_MODULES, Worker, create_app
from hausbuch.api.auth_context import (
    INSECURE_SESSION_COOKIE,
    SESSION_COOKIE,
    AuthContext,
    SessionResolver,
    current_auth,
    session_cookie_name,
)
from hausbuch.schemas.enums import ActorType
from hausbuch.settings import Settings
from hausbuch.storage import LocalBlobStore

AUTH = AuthContext(workspace_id=uuid4(), user_id=uuid4(), session_id=uuid4(), passkey_id=None)
TOKEN = "t" * 43


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/hausbuch",
        "iban_hmac_key": bytes(32),
        "session_key": bytes(range(32)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": Path("/nonexistent"),
    } | overrides
    return Settings(**values)


class FakeResolver:
    def __init__(self, sessions: dict[str, AuthContext]) -> None:
        self.sessions = sessions
        self.calls: list[tuple[str, datetime]] = []

    def resolve(self, token: str, *, now: datetime) -> AuthContext | None:
        self.calls.append((token, now))
        return self.sessions.get(token)


class FakeWorker:
    async def start(self) -> None: ...

    async def stop(self, timeout: float) -> None: ...


@pytest.fixture
def engine():
    engine = create_engine("postgresql+psycopg://")  # never connects in these tests
    yield engine
    engine.dispose()


def _app(engine, tmp_path, resolver=None, worker=None, **settings) -> FastAPI:
    return create_app(
        _settings(**settings),
        engine,
        LocalBlobStore(tmp_path),
        resolver or FakeResolver({}),
        worker,
    )


def test_create_app_keeps_its_dependencies_on_state(engine, tmp_path):
    resolver, worker, store = FakeResolver({}), FakeWorker(), LocalBlobStore(tmp_path)
    settings = _settings()
    app = create_app(settings, engine, store, resolver, worker)
    assert isinstance(app, FastAPI)
    assert app.state.settings is settings
    assert app.state.engine is engine
    assert app.state.blob_store is store
    assert app.state.session_resolver is resolver
    assert app.state.worker is worker
    assert create_app(settings, engine, store, resolver).state.worker is None  # phase 1


def test_every_router_is_included_under_api(engine, tmp_path, monkeypatch):
    names = [m.__name__.rsplit(".", 1)[1] for m in ROUTE_MODULES]
    assert names == [
        "owners",
        "properties",
        "units",
        "tenants",
        "tenancies",
        "loans",
        "documents",
        "proposals",
        "accounts",
        "statements",
        "allocations",
        "tax",
        "history",
        "workspace",
        "auth",
    ]
    for module in ROUTE_MODULES:
        router = APIRouter()
        name = module.__name__.rsplit(".", 1)[1]
        router.add_api_route(f"/{name}-probe", lambda name=name: {"router": name})
        monkeypatch.setattr(module, "router", router)
    client = TestClient(_app(engine, tmp_path))
    for name in names:
        assert client.get(f"{API_PREFIX}/{name}-probe").json() == {"router": name}
        assert client.get(f"/{name}-probe").status_code == 404


def test_every_endpoint_is_under_api_and_no_docs_are_served(engine, tmp_path):
    app = _app(engine, tmp_path)
    # Chunk 1 filled the domain routers (the stub asserted paths == {}); /healthz is the one
    # endpoint outside /api, and it is left out of the schema.
    assert all(path.startswith(f"{API_PREFIX}/") for path in app.openapi()["paths"])
    assert app.openapi()["info"]["title"] == "Hausbuch"
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_worker_and_resolver_protocols():
    assert isinstance(FakeWorker(), Worker)
    assert isinstance(FakeResolver({}), SessionResolver)
    assert not isinstance(object(), Worker)
    assert app_module.create_app.__annotations__["worker"] == Worker | None


def test_auth_context_actor_names_the_user_not_the_person():
    assert AUTH.actor.actor_type is ActorType.USER
    assert AUTH.actor.actor_id == f"user:{AUTH.user_id}"


# --- current_auth -----------------------------------------------------------------


def _whoami_client(engine, tmp_path, resolver, **settings) -> TestClient:
    app = _app(engine, tmp_path, resolver, **settings)

    @app.get("/whoami")
    def whoami(auth: AuthContext = Depends(current_auth)) -> dict:  # noqa: B008
        return {"workspace_id": str(auth.workspace_id), "user_id": str(auth.user_id)}

    return TestClient(app)


def test_current_auth_resolves_the_session_cookie(engine, tmp_path):
    resolver = FakeResolver({TOKEN: AUTH})
    client = _whoami_client(engine, tmp_path, resolver)
    response = client.get("/whoami", headers={"Cookie": f"{SESSION_COOKIE}={TOKEN}"})
    assert response.status_code == 200
    assert response.json() == {"workspace_id": str(AUTH.workspace_id), "user_id": str(AUTH.user_id)}
    [(token, now)] = resolver.calls
    assert token == TOKEN
    assert now.tzinfo is not None and abs(datetime.now(UTC) - now).total_seconds() < 60


@pytest.mark.parametrize(
    "cookie",
    [None, f"{SESSION_COOKIE}=unknown", f"{SESSION_COOKIE}=", f"{INSECURE_SESSION_COOKIE}={TOKEN}"],
    ids=["no-cookie", "unknown-token", "empty-token", "wrong-cookie-name"],
)
def test_current_auth_refuses_without_a_valid_session(engine, tmp_path, cookie):
    client = _whoami_client(engine, tmp_path, FakeResolver({TOKEN: AUTH}))
    response = client.get("/whoami", headers={"Cookie": cookie} if cookie else {})
    assert response.status_code == 401
    body = response.json()  # an ErrorResponse since chunk 1 (the stub returned {"detail": ...})
    assert body["error"] == "unauthenticated"
    assert set(body) == {"error", "request_id"}


def test_an_oversized_token_is_refused_without_a_lookup(engine, tmp_path):
    resolver = FakeResolver({})
    client = _whoami_client(engine, tmp_path, resolver)
    response = client.get("/whoami", headers={"Cookie": f"{SESSION_COOKIE}={'x' * 129}"})
    assert response.status_code == 401
    assert resolver.calls == []


def test_dev_cookie_name_without_secure(engine, tmp_path):
    insecure = {"origin": "http://localhost:3000", "rp_id": "localhost", "cookie_secure": False}
    assert session_cookie_name(_settings(**insecure)) == INSECURE_SESSION_COOKIE
    assert session_cookie_name(_settings()) == SESSION_COOKIE
    client = _whoami_client(engine, tmp_path, FakeResolver({TOKEN: AUTH}), **insecure)
    cookie = {"Cookie": f"{INSECURE_SESSION_COOKIE}={TOKEN}"}
    assert client.get("/whoami", headers=cookie).status_code == 200
    assert client.get("/whoami", headers={"Cookie": f"{SESSION_COOKIE}={TOKEN}"}).status_code == 401


def test_no_request_input_can_name_the_workspace(engine, tmp_path):
    """The workspace comes from the session only: a query parameter or header is ignored."""
    client = _whoami_client(engine, tmp_path, FakeResolver({TOKEN: AUTH}))
    other = uuid4()
    response = client.get(
        f"/whoami?workspace_id={other}",
        headers={"Cookie": f"{SESSION_COOKIE}={TOKEN}", "X-Workspace-Id": str(other)},
    )
    assert response.json()["workspace_id"] == str(AUTH.workspace_id)
