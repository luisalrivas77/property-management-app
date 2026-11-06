"""/healthz reports the job worker (§5.1): `worker: ok | stopped`, no database details."""

import pytest
from api_support import FakeResolver, client_for, make_settings

from hausbuch.api.app import create_app
from hausbuch.api.main import build_app
from hausbuch.jobs.worker import JobWorker
from hausbuch.storage import LocalBlobStore

pytestmark = pytest.mark.db


class Worker:
    def __init__(self, running: bool) -> None:
        self.running = running

    async def start(self) -> None: ...

    async def stop(self, timeout: float) -> None: ...


def _get(app):
    client = client_for(app, None)
    try:
        response = client.get("/healthz")
    finally:
        client.close()
    return response.status_code, response.json()


@pytest.mark.parametrize(("running", "shown"), [(True, "ok"), (False, "stopped")])
def test_healthz_shows_the_worker(app_engine, tmp_path, running, shown):
    app = create_app(
        make_settings(tmp_path),
        app_engine,
        LocalBlobStore(tmp_path / "blobs"),
        FakeResolver({}),
        Worker(running),
    )
    assert _get(app) == (200, {"status": "ok", "worker": shown})


def test_healthz_without_a_worker_has_no_worker_field(app_engine, tmp_path):
    app = create_app(
        make_settings(tmp_path), app_engine, LocalBlobStore(tmp_path / "blobs"), FakeResolver({})
    )
    assert _get(app) == (200, {"status": "ok"})


def test_build_app_builds_the_worker_unless_disabled(app_engine, tmp_path):
    store = LocalBlobStore(tmp_path / "blobs")
    with_worker = build_app(make_settings(tmp_path), engine=app_engine, blob_store=store)
    assert isinstance(with_worker.state.worker, JobWorker)
    # Built but not started (the lifespan starts it): stopped.
    assert _get(with_worker) == (200, {"status": "ok", "worker": "stopped"})

    without = build_app(
        make_settings(tmp_path, run_worker=False), engine=app_engine, blob_store=store
    )
    assert without.state.worker is None
