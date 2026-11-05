"""Process lifecycle (§3.7): the worker is started on startup and stopped within its grace
on shutdown; requests during the drain get 503 while /healthz still answers; the engine is
disposed last. /healthz itself: 200 with the database, 503 without, no details.

These tests speak ASGI directly (lifespan and HTTP messages on one event loop), so a request
can arrive while the shutdown is waiting for the worker.
"""

import asyncio
import time

import pytest
from api_support import TOKEN_A, FakeResolver, make_settings
from db.conftest import make_engine
from sqlalchemy import create_engine

from hausbuch.api.app import create_app
from hausbuch.api.auth_context import SESSION_COOKIE
from hausbuch.storage import LocalBlobStore

pytestmark = pytest.mark.db


class Lifespan:
    """The server side of the ASGI lifespan protocol."""

    def __init__(self, app) -> None:
        self.app = app
        self.to_app: asyncio.Queue = asyncio.Queue()
        self.from_app: asyncio.Queue = asyncio.Queue()

    async def startup(self) -> None:
        scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
        self.task = asyncio.create_task(self.app(scope, self.to_app.get, self.from_app.put))
        await self.to_app.put({"type": "lifespan.startup"})
        message = await self.from_app.get()
        assert message["type"] == "lifespan.startup.complete", message

    def begin_shutdown(self) -> None:
        self.to_app.put_nowait({"type": "lifespan.shutdown"})

    async def shutdown_complete(self) -> None:
        message = await self.from_app.get()
        assert message["type"] == "lifespan.shutdown.complete", message
        await self.task


async def call(app, method: str, path: str, *, token: str | None = None):
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    headers = [(b"host", b"testserver")]
    if token is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={token}".encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 443),
        "state": {},
    }
    await app(scope, receive, send)
    start = sent[0]
    head = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], head, b"".join(m.get("body", b"") for m in sent[1:])


class RecordingWorker:
    def __init__(self, log: list, *, block: asyncio.Event | None = None, hang: bool = False):
        self.log = log
        self.block = block
        self.hang = hang
        self.stopping = asyncio.Event()

    async def start(self) -> None:
        self.log.append("start")

    async def stop(self, timeout: float) -> None:
        self.log.append(("stop", timeout))
        self.stopping.set()
        if self.hang:
            await asyncio.sleep(3600)
        if self.block is not None:
            await self.block.wait()
        self.log.append("stopped")


@pytest.fixture
def own_engine(dbname):
    """An engine of its own: the lifespan disposes it."""
    engine = make_engine(dbname)
    yield engine
    engine.dispose()


def _app(engine, tmp_path, sessions, worker, log, **settings):
    app = create_app(
        make_settings(tmp_path, **settings),
        engine,
        LocalBlobStore(tmp_path / "blobs"),
        FakeResolver(sessions),
        worker,
    )
    dispose = engine.dispose

    def recording_dispose(*args, **kwargs):
        log.append(("dispose", app.state.draining))
        return dispose(*args, **kwargs)

    engine.dispose = recording_dispose
    return app


def test_worker_start_drain_stop_dispose_in_order(own_engine, tmp_path, sessions):
    log: list = []

    async def scenario():
        release = asyncio.Event()
        worker = RecordingWorker(log, block=release)
        app = _app(own_engine, tmp_path, sessions, worker, log, shutdown_grace_s=7)
        lifespan = Lifespan(app)
        await lifespan.startup()
        assert log == ["start"]
        status, _, _ = await call(app, "GET", "/api/owners", token=TOKEN_A)
        assert status == 200

        lifespan.begin_shutdown()
        await worker.stopping.wait()  # shutdown is now waiting for the running job
        status, head, body = await call(app, "GET", "/api/owners", token=TOKEN_A)
        assert (status, head["retry-after"]) == (503, "2")
        assert b'"error":"shutting_down"' in body
        assert "x-request-id" in head
        status, _, body = await call(app, "GET", "/healthz")
        assert (status, body) == (200, b'{"status":"ok"}')  # still alive while draining
        assert not any(isinstance(e, tuple) and e[0] == "dispose" for e in log)

        release.set()
        await lifespan.shutdown_complete()

    asyncio.run(scenario())
    assert log == ["start", ("stop", 7), "stopped", ("dispose", True)]


def test_no_worker_is_started_when_disabled(own_engine, tmp_path, sessions):
    log: list = []

    async def scenario():
        worker = RecordingWorker(log)
        app = _app(own_engine, tmp_path, sessions, worker, log, run_worker=False)
        lifespan = Lifespan(app)
        await lifespan.startup()
        lifespan.begin_shutdown()
        await lifespan.shutdown_complete()

    asyncio.run(scenario())
    assert log == [("dispose", True)]


def test_phase_1_runs_without_a_worker(own_engine, tmp_path, sessions):
    log: list = []

    async def scenario():
        app = _app(own_engine, tmp_path, sessions, None, log)
        lifespan = Lifespan(app)
        await lifespan.startup()
        lifespan.begin_shutdown()
        await lifespan.shutdown_complete()

    asyncio.run(scenario())
    assert log == [("dispose", True)]


def test_a_stuck_worker_cannot_hold_the_shutdown(own_engine, tmp_path, sessions, caplog):
    log: list = []

    async def scenario():
        worker = RecordingWorker(log, hang=True)
        app = _app(own_engine, tmp_path, sessions, worker, log, shutdown_grace_s=1)
        lifespan = Lifespan(app)
        await lifespan.startup()
        started = time.monotonic()
        lifespan.begin_shutdown()
        await lifespan.shutdown_complete()
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())
    assert elapsed < 5
    assert log == ["start", ("stop", 1), ("dispose", True)]
    assert any(
        getattr(r, "fields", {}).get("event") == "worker_stop_timeout" for r in caplog.records
    )


# --- /healthz ---------------------------------------------------------------------------


def test_healthz_with_and_without_the_database(own_engine, tmp_path, sessions):
    up = _app(own_engine, tmp_path, sessions, None, [])
    status, _, body = asyncio.run(call(up, "GET", "/healthz"))
    assert (status, body) == (200, b'{"status":"ok"}')

    unreachable = create_engine("postgresql+psycopg://hausbuch_app@127.0.0.1:1/hausbuch")
    down = _app(unreachable, tmp_path, sessions, None, [])
    status, _, body = asyncio.run(call(down, "GET", "/healthz"))
    assert (status, body) == (503, b'{"status":"unavailable"}')  # no host, no error text
    unreachable.dispose()

    assert "/healthz" not in up.openapi()["paths"]
