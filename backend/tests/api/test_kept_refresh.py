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
    return KeptRefresher(kept, quiet_s=30, pause_s=0.01, idle_s=0.01)


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

    # Review fix C2: the run waits for a continuous idle time, so a page's next request sent
    # just after the previous one ended still comes first.
    refresher.idle_s = 0.5

    async def scenario():
        def person(name: str, release: asyncio.Event):
            async def app(scope, receive, send):  # a person's request, still running
                order.append(f"{name} starts")
                await release.wait()
                order.append(f"{name} ends")

            scope = {"type": "http", "method": "POST", "path": "/api/owners", "headers": []}
            return asyncio.create_task(KeptAnswersMiddleware(app, kept)(scope, None, None))

        first_done, second_done = asyncio.Event(), asyncio.Event()
        first = person("first", first_done)
        await asyncio.sleep(0)
        assert kept.in_flight == 1
        run = asyncio.create_task(refresher.refresh(ws, version))
        await asyncio.sleep(0.2)
        assert order == ["first starts"]  # the run waits
        first_done.set()
        await first
        await asyncio.sleep(0.1)  # the page's next request, shortly after
        second = person("second", second_done)
        await asyncio.sleep(0.2)
        assert order == ["first starts", "first ends", "second starts"]
        second_done.set()
        await second
        return await run

    run = asyncio.run(scenario())
    assert order == [
        "first starts",
        "first ends",
        "second starts",
        "second ends",
        "refresh request",
    ]
    assert (run.result, run.stored) == ("done", 1)
    assert kept.in_flight == 0


@pytest.mark.db
def test_a_refresh_keeps_the_language_a_person_last_asked_for(
    client_a, kept, refresher, clock, app_engine, auth_a
):
    """Review fix C1: the refresh asks an older path in its own language, and that internal
    request does not change the language the warm step and the checks use."""
    ws = auth_a.workspace_id
    ok(client_a.get("/api/properties", headers={"Accept-Language": "de"}))
    ok(client_a.get("/api/owners", headers={"Accept-Language": "en"}))
    assert kept.locale_of(auth_a.user_id) == "en"
    _changed_and_quiet(refresher, clock, app_engine, ws)
    ok(client_a.get("/api/owners", headers={"Accept-Language": "en"}))  # kept again by a person
    (run,) = asyncio.run(refresher.tick())
    assert (run.result, run.requested, run.skipped) == ("done", 1, 1)  # the `de` path only
    assert kept.locale_of(auth_a.user_id) == "en"


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


# --- §7.13 (e) 4: after a person's own write, and just after midnight -----------------------


@pytest.mark.db
def test_a_persons_own_write_is_refreshed_after_the_short_quiet(
    client_a, kept, refresher, clock, auth_a
):
    ws = auth_a.workspace_id
    woken: list[int] = []
    kept.on_person_write = lambda: woken.append(1)
    ok(client_a.get("/api/owners"))
    assert woken == []  # a read wakes nothing
    assert asyncio.run(refresher.tick()) == []  # first sighting
    ok(client_a.post("/api/owners", json={"full_name": "Owner A"}), 201)
    assert woken == [1]
    assert asyncio.run(refresher.tick()) == []  # the move is seen, the short quiet starts
    assert refresher.watch[ws].writer == auth_a.user_id
    clock.advance(3)
    assert asyncio.run(refresher.tick()) == []
    clock.advance(1.5)
    (run,) = asyncio.run(refresher.tick())  # 4.5 s: refreshed, not after 30 s
    assert (run.result, run.requested, run.stored) == ("done", 1, 1)
    owners = ok(client_a.get("/api/owners"))
    assert [o["full_name"] for o in owners] == ["Owner A"]


@pytest.mark.db
def test_a_write_outside_a_persons_request_keeps_the_long_quiet(
    client_a, kept, refresher, clock, app_engine, auth_a
):
    ws = auth_a.workspace_id
    ok(client_a.get("/api/owners"))
    assert asyncio.run(refresher.tick()) == []
    _write(app_engine, ws)  # the worker's write: no person waits for it
    assert asyncio.run(refresher.tick()) == []
    clock.advance(5)
    assert asyncio.run(refresher.tick()) == []
    assert refresher.watch[ws].writer is None
    clock.advance(26)
    (run,) = asyncio.run(refresher.tick())
    assert run.result == "done"


@pytest.mark.db
def test_the_writing_members_pages_are_asked_first(client_a, kept, refresher, auth_a):
    ws = auth_a.workspace_id
    other = auth_for(ws)  # a second member of the same household
    stamp = kept.stamp(ws)
    for auth, path in ((auth_a, "/api/owners"), (other, "/api/properties")):
        key = Key(path, (), ws, auth.user_id, stamp, "de", kept.app_version, kept.rule_version)
        kept.record(key, path, auth)
    run = asyncio.run(refresher.refresh(ws, stamp.version, first=auth_a.user_id))
    assert run.templates == ["/api/owners", "/api/properties"]
    kept.clear()
    run = asyncio.run(refresher.refresh(ws, stamp.version, first=other.user_id))
    assert run.templates == ["/api/properties", "/api/owners"]


@pytest.mark.db
def test_just_after_midnight_the_full_warm_runs(client_a, kept, refresher, clock, auth_a, dbname):
    from db.db_support import _admin

    with _admin(dbname) as conn:  # an enrolled member: a user with a passkey
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )
        conn.execute(
            "INSERT INTO auth_passkey (workspace_id, user_id, credential_id, public_key, "
            "backup_eligible, backed_up, label) VALUES (%s, %s, %s, %s, false, false, 'k')",
            (auth_a.workspace_id, auth_a.user_id, uuid.uuid4().bytes, b"k"),
        )
    new_property(client_a)
    clock.now = datetime(2026, 11, 21, 22, 59, 50, tzinfo=UTC)  # 23:59:50 in Berlin
    assert asyncio.run(refresher.day_turn()) is None  # the first look sets the day
    clock.advance(5)
    assert asyncio.run(refresher.day_turn()) is None  # the same day
    clock.advance(10)  # 00:00:05 in Berlin, still 21 Nov in UTC

    # The warm asks for every enrolled member in the database, and other tests may leave some
    # behind, so the assertions look at this member's own answers, never at a total.
    def own_days(part: str) -> set[date]:
        return {
            getattr(key.stamp, part) for key, _ in kept.items() if key.user_id == auth_a.user_id
        }

    run = asyncio.run(refresher.day_turn())
    assert run is not None and run.members >= 1 and run.requests > 0
    assert date(2026, 11, 22) in own_days("berlin_day")
    assert asyncio.run(refresher.day_turn()) is None
    clock.now = datetime(2026, 11, 22, 0, 0, 5, tzinfo=UTC)  # the UTC midnight
    assert asyncio.run(refresher.day_turn()) is not None
    assert date(2026, 11, 22) in own_days("utc_day")
