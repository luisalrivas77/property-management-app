"""Phase 10 §7.4: kept answers are served only while nothing they depend on has changed.

A kept answer that outlived a change would show a stale euro figure without a word, the
worst outcome the spec names. These tests pin each way the key must move: a write, a commit
during the computation, another member, another day, another language; and that a non-200
answer, a signed-out request and a revoked session are never served from the store.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from api_support import (
    TOKEN_A,
    auth_for,
    client_for,
    new_property,
    new_unit,
    ok,
)
from sqlalchemy import text

from hausbuch.api.kept_answers import (
    KEPT_ROUTES,
    Entry,
    KeptAnswers,
    Key,
    Stamp,
    canonical_query,
    kept_route,
    warm_member,
)
from hausbuch.db.session import workspace_transaction
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.kept_answers import enrolled_members, run_check, run_warm

pytestmark = pytest.mark.db


@pytest.fixture
def kept(api_app):
    store = api_app.state.kept_answers
    store.clock = lambda: datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    return store


def _write(app_engine, ws) -> None:
    """A committed write outside the API, as the worker or another member would make."""
    with workspace_transaction(app_engine, ws) as conn:
        conn.execute(
            text("INSERT INTO owner (workspace_id, full_name) VALUES (:ws, 'X')"), {"ws": ws}
        )


def test_the_routes_match_their_templates():
    assert kept_route("/api/properties") == "/api/properties"
    pid = str(uuid.uuid4())
    assert kept_route(f"/api/properties/{pid}/units") == "/api/properties/{property_id}/units"
    assert kept_route(f"/api/properties/{pid}") is None
    assert kept_route("/api/inbox") is None
    assert kept_route("/api/auth/me") is None
    assert kept_route("/api/auth/members") is None
    assert kept_route("/api/properties/equity") == "/api/properties/equity"
    assert len(set(KEPT_ROUTES)) == len(KEPT_ROUTES)


def test_the_second_read_is_served_from_the_store_byte_for_byte(client_a, kept):
    new_property(client_a)
    first = client_a.get("/api/properties")
    assert first.status_code == 200
    assert kept.counters.stored == 1
    second = client_a.get("/api/properties")
    assert kept.counters.hits == 1
    assert second.content == first.content
    assert second.headers["content-type"] == first.headers["content-type"]
    assert second.headers["content-length"] == first.headers["content-length"]


def test_a_write_through_the_api_is_seen_at_once(client_a, kept):
    new_property(client_a, label="Erste")
    assert [p["label"] for p in ok(client_a.get("/api/properties"))] == ["Erste"]
    new_property(client_a, label="Zweite")
    labels = sorted(p["label"] for p in ok(client_a.get("/api/properties")))
    assert labels == ["Erste", "Zweite"]
    assert kept.counters.hits == 0


def test_a_write_outside_the_api_is_seen_at_once(client_a, kept, app_engine, auth_a):
    ok(client_a.get("/api/owners"))
    _write(app_engine, auth_a.workspace_id)
    owners = ok(client_a.get("/api/owners"))
    assert [o["full_name"] for o in owners] == ["X"]
    assert kept.counters.hits == 0


def test_a_commit_during_the_computation_is_not_kept(client_a, kept, app_engine, auth_a):
    """The version is read before the route computes; a commit right after that read (while
    the route runs) moves it, so the answer, which may hold either side, is not kept."""
    real_stamp = kept.stamp
    calls = []

    def stamp_then_commit(ws):
        result = real_stamp(ws)
        calls.append(result)
        if len(calls) == 1:
            _write(app_engine, ws)
        return result

    kept.stamp = stamp_then_commit
    ok(client_a.get("/api/owners"))
    kept.stamp = real_stamp
    assert kept.counters.discarded == 1
    assert kept.counters.stored == 0
    assert len(kept) == 0
    assert [o["full_name"] for o in ok(client_a.get("/api/owners"))] == ["X"]
    assert kept.counters.stored == 1


def test_answers_are_kept_per_member(api_app, kept, sessions, auth_a):
    """Two members of one workspace: each computes their own answer."""
    other = auth_for(auth_a.workspace_id)
    sessions["c" * 43] = other
    first, second = client_for(api_app, TOKEN_A), client_for(api_app, "c" * 43)
    ok(first.get("/api/properties"))
    ok(second.get("/api/properties"))
    assert kept.counters.hits == 0
    assert kept.counters.stored == 2
    ok(second.get("/api/properties"))
    assert kept.counters.hits == 1


def test_another_workspace_never_gets_a_kept_answer(client_a, client_b, kept):
    new_property(client_a, label="Nur A")
    ok(client_a.get("/api/properties"))
    assert ok(client_b.get("/api/properties")) == []
    assert kept.counters.hits == 0


def test_a_non_200_answer_is_not_kept(client_a, kept):
    path = f"/api/properties/{uuid.uuid4()}/units"
    assert client_a.get(path).status_code == 404
    assert client_a.get(path).status_code == 404
    assert kept.counters.stored == 0
    assert kept.counters.hits == 0
    assert len(kept) == 0


def test_a_new_day_is_computed_again(client_a, kept):
    ok(client_a.get("/api/properties"))
    kept.clock = lambda: datetime(2026, 11, 21, 22, 59, tzinfo=UTC)  # 23:59 in Berlin
    ok(client_a.get("/api/properties"))
    assert kept.counters.hits == 1
    kept.clock = lambda: datetime(2026, 11, 21, 23, 1, tzinfo=UTC)  # 00:01 in Berlin
    ok(client_a.get("/api/properties"))
    assert kept.counters.hits == 1
    kept.clock = lambda: datetime(2026, 11, 22, 0, 1, tzinfo=UTC)  # a new day in UTC too
    ok(client_a.get("/api/properties"))
    assert kept.counters.hits == 1
    assert kept.counters.stored == 3


def test_midnight_during_the_computation_is_not_kept(client_a, kept):
    days = iter(
        [datetime(2026, 11, 21, 22, 59, tzinfo=UTC), datetime(2026, 11, 21, 23, 1, tzinfo=UTC)]
    )
    kept.clock = lambda: next(days)
    ok(client_a.get("/api/properties"))
    assert kept.counters.discarded == 1
    assert len(kept) == 0


def test_another_language_is_computed_again(client_a, kept):
    ok(client_a.get("/api/reminders", headers={"Accept-Language": "de"}))
    ok(client_a.get("/api/reminders", headers={"Accept-Language": "en"}))
    assert kept.counters.hits == 0
    ok(client_a.get("/api/reminders", headers={"Accept-Language": "en"}))
    assert kept.counters.hits == 1


def test_the_query_order_does_not_matter_but_its_values_do(client_a, kept):
    assert canonical_query(b"to=2026-10&from=2026-10") == canonical_query(
        b"from=2026-10&to=2026-10"
    )
    ok(client_a.get("/api/health/months?from=2026-10&to=2026-10"))
    ok(client_a.get("/api/health/months?to=2026-10&from=2026-10"))
    assert kept.counters.hits == 1
    ok(client_a.get("/api/health/months?from=2026-09&to=2026-10"))
    assert kept.counters.hits == 1


def test_signed_out_and_revoked_sessions_never_get_a_kept_answer(api_app, kept, sessions):
    client = client_for(api_app, TOKEN_A)
    ok(client.get("/api/properties"))
    assert client_for(api_app, None).get("/api/properties").status_code == 401
    del sessions[TOKEN_A]
    assert client.get("/api/properties").status_code == 401
    assert kept.counters.hits == 0


def test_a_route_not_listed_is_never_kept(client_a, kept):
    ok(client_a.get("/api/inbox"))
    ok(client_a.get("/api/inbox"))
    assert kept.counters.stored == 0
    assert len(kept) == 0


def _key(n: int, ws: uuid.UUID, version: int = 1) -> Key:
    stamp = Stamp(version, date(2026, 11, 21), date(2026, 11, 21))
    return Key(f"/api/x{n}", (), ws, ws, stamp, "de", "v", "r")


def _entry(size: int) -> Entry:
    return Entry("/api/properties", auth_for(uuid.uuid4()), 200, (), b"x" * size)


def test_the_store_drops_the_least_recently_used_answer_over_its_cap():
    store = KeptAnswers(max_bytes=4000)
    ws = uuid.uuid4()
    for n in range(4):
        assert store.put(_key(n, ws), _entry(1000))
    assert store.get(_key(0, ws)) is not None  # 0 is now the most recently used
    assert store.put(_key(4, ws), _entry(1000))
    assert store.get(_key(1, ws)) is None  # the least recently used went first
    assert store.get(_key(0, ws)) is not None
    assert store.size_bytes <= 4000
    assert not store.put(_key(5, ws), _entry(1001))  # over a quarter of the cap: not kept


def test_storing_a_newer_version_drops_the_workspaces_older_answers():
    store = KeptAnswers()
    ws, other = uuid.uuid4(), uuid.uuid4()
    store.put(_key(0, ws, 1), _entry(10))
    store.put(_key(1, other, 1), _entry(10))
    store.put(_key(2, ws, 2), _entry(10))
    assert store.get(_key(0, ws, 1)) is None
    assert store.get(_key(1, other, 1)) is not None
    assert len(store) == 2


def test_warm_keeps_the_pages_answers_and_the_page_hits_them(
    api_app, client_a, kept, app_engine, settings, blob_store, auth_a
):
    prop = new_property(client_a)
    new_unit(client_a, prop["id"])
    deps = JobDeps(
        engine=app_engine,
        settings=settings,
        blob_store=blob_store,
        model_client=None,
        clock=kept.clock,
        kept_answers=kept,
    )
    run = asyncio.run(run_warm(deps, members=[auth_a]))
    assert run.members == 1
    assert run.failed == 0
    assert run.requests == len(kept)
    stored = kept.counters.stored
    ok(client_a.get("/api/properties"))
    ok(client_a.get(f"/api/properties/{prop['id']}/units"))
    assert kept.counters.hits == 2
    assert kept.counters.stored == stored


def test_the_checks_find_kept_answers_equal_to_a_recomputation(client_a, kept, auth_a):
    new_property(client_a)
    asyncio.run(warm_member(kept, auth_a, datetime(2026, 11, 21).date()))
    outcomes = asyncio.run(kept.compare_all())
    assert outcomes
    assert {o.result for o in outcomes} == {"same"}
    deps = JobDeps(
        engine=None, settings=None, blob_store=None, model_client=None, kept_answers=kept
    )
    assert asyncio.run(run_check(deps, now=datetime(2026, 11, 21, 15, tzinfo=UTC))) == "same"


def test_a_check_that_finds_a_difference_empties_the_store(client_a, kept, auth_a, caplog):
    ok(client_a.get("/api/properties"))
    ((_, entry),) = kept.items()
    entry.body = entry.body.replace(b"[", b"[ ", 1)  # a kept answer gone wrong
    outcome = asyncio.run(kept.check(0))
    assert outcome is not None and outcome.result == "different"
    assert len(kept) == 0
    assert any(
        getattr(r, "fields", {}).get("event") == "kept_answer_mismatch" for r in caplog.records
    )


def test_a_check_after_a_write_does_not_compare(client_a, kept, app_engine, auth_a):
    ok(client_a.get("/api/properties"))
    _write(app_engine, auth_a.workspace_id)
    assert asyncio.run(kept.check(0)) is None
    assert len(kept) == 1


def test_enrolled_members_are_the_users_with_a_passkey(app_engine, settings, blob_store, dbname):
    from db.db_support import _admin

    ws = uuid.uuid4()
    with _admin(dbname) as conn, conn.transaction():
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'W')", (ws,))
        users = [
            conn.execute(
                "INSERT INTO app_user (workspace_id, email, display_name) VALUES (%s, %s, 'U') "
                "RETURNING id",
                (ws, f"{uuid.uuid4().hex}@example.org"),
            ).fetchone()[0]
            for _ in range(2)
        ]
        conn.execute(
            "INSERT INTO auth_passkey (workspace_id, user_id, credential_id, public_key, "
            "backup_eligible, backed_up, label) VALUES (%s, %s, %s, %s, false, false, 'k')",
            (ws, users[0], uuid.uuid4().bytes, b"k"),
        )
    deps = JobDeps(engine=app_engine, settings=settings, blob_store=blob_store, model_client=None)
    members = [m for m in enrolled_members(deps) if m.workspace_id == ws]
    assert [m.user_id for m in members] == [users[0]]


def test_no_store_no_warm():
    deps = JobDeps(engine=None, settings=None, blob_store=None, model_client=None)
    assert asyncio.run(run_warm(deps, members=[])).members == 0
    assert asyncio.run(run_check(deps)) is None


def test_the_request_line_names_the_route_and_whether_it_was_kept(client_a, kept, caplog):
    """A kept answer runs no route, so the request line takes the template the middleware
    matched; `kept` says stored or hit, and the line still carries no path id."""
    import logging

    from hausbuch.api.logging import REQUEST_LOGGER

    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)
    prop = new_property(client_a)
    for _ in range(2):
        ok(client_a.get(f"/api/properties/{prop['id']}/units"))
    lines = [r.fields for r in caplog.records if r.name == REQUEST_LOGGER][-2:]
    assert [line["kept"] for line in lines] == ["stored", "hit"]
    for line in lines:
        assert line["route"] == "/api/properties/{property_id}/units"
        assert line["status"] == 200
        assert prop["id"] not in str(line)
