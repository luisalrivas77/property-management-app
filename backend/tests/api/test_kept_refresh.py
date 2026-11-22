"""Phase 10 §7.5 (c): kept answers computed after a change, not at the next load.

The store records what each member's own requests found kept; once a write has been followed
by the quiet time, the refresh asks those paths again so the next visit is served kept. The
gates: the refreshed answers equal a recomputation byte for byte; a person's request is
answered first; a write during a run stops it; nothing is recorded that the store would not
keep; the task stops with the app (tests/api/test_lifespan.py).
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from api_support import auth_for, new_property, ok
from sqlalchemy import text

from hausbuch.api.kept_answers import (
    MAX_RECORDED_PATHS,
    RECORDED_FOR,
    Entry,
    KeptAnswers,
    KeptAnswersMiddleware,
    Key,
    Stamp,
    read_data_version,
    warm_member,
)
from hausbuch.api.kept_refresh import KeptRefresher
from hausbuch.db.session import workspace_transaction


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def kept(api_app, clock):
    store = api_app.state.kept_answers
    store.clock = clock
    return store


@pytest.fixture
def refresher(kept) -> KeptRefresher:
    return KeptRefresher(kept, quiet_s=30, pause_s=0.01)


def _write(app_engine, ws) -> None:
    with workspace_transaction(app_engine, ws) as conn:
        conn.execute(
            text("INSERT INTO owner (workspace_id, full_name) VALUES (:ws, 'X')"), {"ws": ws}
        )


def _paths(kept, ws) -> set[str]:
    return {path for _, paths in kept.recorded(ws) for path, _, _ in paths}


def _changed_and_quiet(refresher, clock, app_engine, ws) -> None:
    """The task has seen the workspace; a write follows and the version stays put 31 s."""
    assert asyncio.run(refresher.tick()) == []
    _write(app_engine, ws)
    assert asyncio.run(refresher.tick()) == []  # the move is seen, the quiet time starts
    clock.advance(29)
    assert asyncio.run(refresher.tick()) == []  # not quiet long enough
    clock.advance(2)


@pytest.mark.db
def test_a_write_and_the_quiet_time_leave_the_recorded_routes_kept_and_equal(
    client_a, kept, refresher, clock, app_engine, auth_a
):
    ws = auth_a.workspace_id
    new_property(client_a)
    ok(client_a.get("/api/properties"))
    ok(client_a.get("/api/owners"))
    ok(client_a.get("/api/properties"))  # a hit is recorded too
    assert _paths(kept, ws) == {"/api/properties", "/api/owners"}
    _changed_and_quiet(refresher, clock, app_engine, ws)

    (run,) = asyncio.run(refresher.tick())
    assert (run.result, run.requested, run.stored, run.failed) == ("done", 2, 2, 0)
    assert asyncio.run(refresher.tick()) == []  # done for this version

    outcomes = asyncio.run(kept.compare_all())
    assert sorted(o.path for o in outcomes) == ["/api/owners", "/api/properties"]
    assert {o.result for o in outcomes} == {"same"}
    hits = kept.counters.hits
    owners = ok(client_a.get("/api/owners"))
    assert kept.counters.hits == hits + 1
    assert [o["full_name"] for o in owners] == ["X"]  # the write, served kept


@pytest.mark.db
def test_a_run_when_everything_is_kept_requests_nothing(client_a, kept, refresher, clock, auth_a):
    ok(client_a.get("/api/properties"))
    assert asyncio.run(refresher.tick()) == []  # first sighting
    clock.advance(31)
    (run,) = asyncio.run(refresher.tick())
    assert (run.result, run.requested, run.skipped) == ("done", 0, 1)


@pytest.mark.db
def test_only_what_a_persons_request_found_kept_is_recorded(client_a, kept, auth_a):
    ws = auth_a.workspace_id
    ok(client_a.get("/api/events"))  # not a kept route
    missing = f"/api/properties/{uuid.uuid4()}/units"
    assert client_a.get(missing).status_code == 404  # never kept
    assert kept.recorded(ws) == []
    asyncio.run(warm_member(kept, auth_a, date(2026, 11, 21)))  # internal requests
    assert len(kept) > 0
    assert kept.recorded(ws) == []
    ok(client_a.get("/api/properties"))  # a hit of a warmed answer
    assert _paths(kept, ws) == {"/api/properties"}


@pytest.mark.db
def test_a_write_during_a_run_stops_it(client_a, kept, refresher, clock, app_engine, auth_a):
    ws = auth_a.workspace_id
    ok(client_a.get("/api/properties"))
    ok(client_a.get("/api/owners"))
    _changed_and_quiet(refresher, clock, app_engine, ws)
    real = kept.internal_get

    async def then_write(*args, **kwargs):
        response = await real(*args, **kwargs)
        _write(app_engine, ws)
        return response

    kept.internal_get = then_write
    (run,) = asyncio.run(refresher.tick())
    assert (run.result, run.requested) == ("moved", 1)
    kept.internal_get = real
    assert asyncio.run(refresher.tick()) == []  # a new quiet time starts
    clock.advance(31)
    (run,) = asyncio.run(refresher.tick())
    assert (run.result, run.requested, run.stored) == ("done", 2, 2)


@pytest.mark.db
def test_a_persons_request_is_answered_first(client_a, kept, refresher, clock, app_engine, auth_a):
    ws = auth_a.workspace_id
    ok(client_a.get("/api/properties"))
    _write(app_engine, ws)
    version = read_data_version(app_engine, ws)
    order: list[str] = []
    real = kept.internal_get

    async def recording(*args, **kwargs):
        order.append("refresh request")
        return await real(*args, **kwargs)

    kept.internal_get = recording

    async def scenario():
        release = asyncio.Event()

        async def person(scope, receive, send):  # a person's save, still running
            order.append("person starts")
            await release.wait()
            order.append("person ends")

        scope = {"type": "http", "method": "POST", "path": "/api/owners", "headers": []}
        request = asyncio.create_task(KeptAnswersMiddleware(person, kept)(scope, None, None))
        await asyncio.sleep(0)
        assert kept.in_flight == 1
        run = asyncio.create_task(refresher.refresh(ws, version))
        await asyncio.sleep(0.2)
        assert order == ["person starts"]  # the run waits
        release.set()
        await request
        return await run

    run = asyncio.run(scenario())
    assert order == ["person starts", "person ends", "refresh request"]
    assert (run.result, run.stored) == ("done", 1)
    assert kept.in_flight == 0


@pytest.mark.db
def test_every_persons_request_is_counted_and_internal_ones_apart(client_a, kept, auth_a):
    seen: list[tuple[int, int]] = []
    real = kept.stamp

    def spy(ws):
        seen.append((kept.in_flight, kept.internal_in_flight))
        return real(ws)

    kept.stamp = spy
    ok(client_a.get("/api/properties"))
    assert seen and set(seen) == {(1, 0)}
    seen.clear()
    asyncio.run(kept.internal_get(auth_a, "/api/owners"))
    assert seen and set(seen) == {(0, 1)}
    assert (kept.in_flight, kept.internal_in_flight) == (0, 0)


def test_nothing_runs_without_the_store():
    store = KeptAnswers(enabled=False)
    ws = uuid.uuid4()
    store.record(_key(1, ws), "/api/properties", auth_for(ws))
    assert asyncio.run(KeptRefresher(store, quiet_s=30).tick()) == []
    assert asyncio.run(KeptRefresher(KeptAnswers(), quiet_s=30).tick()) == []  # no app


def _key(n: int, ws: uuid.UUID) -> Key:
    stamp = Stamp(1, date(2026, 11, 21), date(2026, 11, 21))
    return Key(f"/api/x{n}", (), ws, ws, stamp, "de", "v", "r")


def test_the_record_is_bounded_per_member_and_in_time():
    clock = Clock()
    store = KeptAnswers(clock=clock)
    ws = uuid.uuid4()
    auth = auth_for(ws)
    for n in range(MAX_RECORDED_PATHS + 5):
        store.record(_key(n, ws), "/api/properties", auth)
    ((_, paths),) = store.recorded(ws)
    assert len(paths) == MAX_RECORDED_PATHS
    assert paths[0][0] == f"/api/x{MAX_RECORDED_PATHS + 4}"  # the most recent first
    assert "/api/x0" not in {path for path, _, _ in paths}
    clock.advance(RECORDED_FOR.total_seconds() + 1)
    assert store.recorded_workspaces() == set()


def test_an_expired_answer_is_not_kept_so_a_run_recomputes_it():
    clock = Clock()
    store = KeptAnswers(clock=clock)
    ws = uuid.uuid4()
    entry = Entry("/api/inbox", auth_for(ws), 200, (), b"[]", clock.now + timedelta(seconds=5))
    store.put(_key(1, ws), entry)
    assert store.is_kept(_key(1, ws))
    clock.advance(5)
    assert not store.is_kept(_key(1, ws))
