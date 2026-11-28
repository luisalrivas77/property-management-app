"""Phase 10 §7.4: kept answers are served only while nothing they depend on has changed.

A kept answer that outlived a change would show a stale euro figure without a word, the
worst outcome the spec names. These tests pin each way the key must move: a write, a commit
during the computation, another member, another day, another language; and that a non-200
answer, a signed-out request and a revoked session are never served from the store.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from api_support import (
    TOKEN_A,
    auth_for,
    client_for,
    create,
    loan_body,
    new_property,
    new_tenancy,
    new_tenant,
    new_unit,
    ok,
    upload,
)
from sqlalchemy import text

from hausbuch.api.kept_answers import (
    EXPECTED_REFUSALS,
    KEPT_ROUTES,
    MAX_BYTES,
    NOT_WARMED,
    WARMED,
    Entry,
    KeptAnswers,
    Key,
    Stamp,
    canonical_query,
    kept_route,
    read_data_version,
    warm_member,
)
from hausbuch.auth.events import append_user_event
from hausbuch.db.session import workspace_transaction
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.kept_answers import enrolled_members, run_check, run_warm
from hausbuch.schemas.events import (
    Empty,
    EventType,
    UserPasskeyEnrolled,
    UserPasskeyRef,
    UserSessionsRevoked,
    UserSignedIn,
)

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
    assert kept_route("/api/inbox") == "/api/inbox"  # until its expiry (§7.5 (b))
    assert kept_route("/api/inbound-mail") is None
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


def test_a_repeated_parameter_keeps_the_order_of_its_values():
    # FastAPI takes the last value of a repeated parameter, so `a=1&a=2` and `a=2&a=1` are
    # two answers; only the order of different names is free.
    assert canonical_query(b"a=1&a=2") != canonical_query(b"a=2&a=1")
    assert canonical_query(b"a=1&b=2") == canonical_query(b"b=2&a=1")
    assert canonical_query(b"b=1&a=x&b=2") == canonical_query(b"a=x&b=1&b=2")


def test_signed_out_and_revoked_sessions_never_get_a_kept_answer(api_app, kept, sessions):
    client = client_for(api_app, TOKEN_A)
    ok(client.get("/api/properties"))
    assert client_for(api_app, None).get("/api/properties").status_code == 401
    del sessions[TOKEN_A]
    assert client.get("/api/properties").status_code == 401
    assert kept.counters.hits == 0


def test_a_route_not_listed_is_never_kept(client_a, kept):
    ok(client_a.get("/api/events"))  # until §7.5 (b) this test asked /api/inbox
    ok(client_a.get("/api/events"))
    assert kept.counters.stored == 0
    assert len(kept) == 0


def _expiring_inbox(monkeypatch, changes_at: datetime) -> list[int]:
    """GET /inbox and /inbox/lines answer an empty list that may change at `changes_at`;
    the list counts the computations."""
    from hausbuch.schemas.api import InboxLines
    from hausbuch.services import review
    from hausbuch.services.timed_items import TimedItems

    computed: list[int] = []

    def timed(conn, **kwargs):
        computed.append(1)
        return TimedItems([], changes_at)

    def lines(conn, **kwargs):
        computed.append(1)
        return InboxLines(transactions=[], imports=[]), changes_at

    monkeypatch.setattr(review, "inbox_timed", timed)
    monkeypatch.setattr(review, "inbox_lines_timed", lines)
    return computed


@pytest.mark.parametrize("path", ["/api/inbox", "/api/inbox/lines"])
def test_the_inbox_is_kept_until_its_expiry(client_a, kept, monkeypatch, path):
    changes_at = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    computed = _expiring_inbox(monkeypatch, changes_at)
    first = ok(client_a.get(path))
    kept.clock = lambda: changes_at - timedelta(microseconds=1)
    assert ok(client_a.get(path)) == first
    assert (len(computed), kept.counters.hits) == (1, 1)
    ((_, entry),) = kept.items()
    assert entry.expires_at == changes_at
    kept.clock = lambda: changes_at  # expired: computed again, the old answer dropped
    assert ok(client_a.get(path)) == first
    assert (len(computed), kept.counters.hits) == (2, 1)
    assert kept.counters.discarded == 1  # its new answer expired at once: not kept
    assert len(kept) == 0


def test_an_expired_answer_is_never_compared_by_the_checks(client_a, kept, monkeypatch):
    changes_at = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    _expiring_inbox(monkeypatch, changes_at)
    ok(client_a.get("/api/inbox"))
    ((_, entry),) = kept.items()
    entry.body = b"[ ]"  # it would differ from a recomputation
    kept.clock = lambda: changes_at
    assert asyncio.run(kept.check(0)) is None
    assert [o.result for o in asyncio.run(kept.compare_all())] == ["moved"]
    assert kept.counters.mismatches == 0


def test_a_route_without_an_expiry_keeps_none(client_a, kept):
    ok(client_a.get("/api/properties"))
    ((_, entry),) = kept.items()
    assert entry.expires_at is None


def test_the_store_drops_an_expired_answer():
    now = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    store = KeptAnswers(clock=lambda: now)
    ws = uuid.uuid4()
    entry = _entry(10)
    entry.expires_at = now + timedelta(seconds=1)
    store.put(_key(0, ws), entry)
    assert store.get(_key(0, ws)) is entry
    store.clock = lambda: now + timedelta(seconds=1)
    assert store.get(_key(0, ws)) is None
    assert (len(store), store.size_bytes) == (0, 0)


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


def _get_routes(app) -> dict[str, object]:
    """Every GET route of the app by its full template (FastAPI nests each router)."""

    def walk(routes, prefix=""):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes, prefix + route.include_context.prefix + inner.prefix)
            elif "GET" in (getattr(route, "methods", None) or ()):
                yield prefix + route.path, route

    return dict(walk(app.routes))


def test_every_kept_template_is_a_get_route_and_shadows_no_other(api_app):
    """A listed template that matches no route keeps nothing; one whose `{id}` segment also
    matches another route's literal path would keep that route unaudited."""
    import re

    routes = _get_routes(api_app)
    assert set(KEPT_ROUTES) <= set(routes)
    for template in routes:
        path = re.sub(r"\{[^}]+\}", str(uuid.UUID(int=7)), template)
        assert kept_route(path) == (template if template in KEPT_ROUTES else None), template


# The query each kept route needs (its required parameters, as the screens send them).
_QUERY: dict[str, dict[str, str]] = {
    "/api/properties/{property_id}/anlage-v": {"tax_year": "2026"},
    "/api/properties/{property_id}/anlage-v/sources": {"tax_year": "2026"},
    "/api/owners/{owner_id}/anlage-n": {"tax_year": "2026"},
    "/api/tax/return-estimate": {"tax_year": "2026"},
    "/api/tax/questionnaire": {"tax_year": "2026"},
    "/api/tax-rate": {"tax_year": "2026", "result": "1000.00"},
    "/api/health/months": {"from": "2026-10", "to": "2026-10"},
    "/api/household/cashflow": {"month": "2026-10"},
    "/api/accounts/auto-rate": {"month": "2026-10"},
    "/api/accounts/{account_id}/month": {"month": "2026-10"},
    "/api/transfers": {"month": "2026-10"},
    "/api/household-plan/comparison": {"month": "2026-10"},
    "/api/tenancies/{tenancy_id}/rent": {"at": "2026-11-01"},
}
# Kept routes this test cannot reach: their ids need a statement import, a document read by
# the model or a typed return input, which these fixtures do not make.
_NOT_REQUESTED = {
    "/api/imports/{import_id}",
    "/api/transactions/{transaction_id}/allocations",
    "/api/proposals/{proposal_id}",
    "/api/return-inputs/{return_input_id}/evidence",
}
# Tables a kept answer must never read: the data version does not cover them (module
# docstring), so an answer that read them would outlive a sign-in or a revoked passkey.
_UNVERSIONED_TABLES = ("auth_session", "auth_passkey", "member_locale")  # 0058 too


def _exempt_types() -> tuple[str, ...]:
    """The newest migration's own list (0055, which extends 0051's), so this test and the
    migration cannot drift apart."""
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).parents[2] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location(
        "m0055", versions / "0055_sign_out_keeps_data_version.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EXEMPT_TYPES


def _sign_in_events(passkey_id: uuid.UUID):
    """One event of each type migrations 0051 and 0055 exempt from the data version
    (EXEMPT_TYPES)."""
    return (
        (EventType.USER_SIGNED_IN, UserSignedIn(passkey_id=passkey_id)),
        (EventType.USER_PASSKEY_ENROLLED, UserPasskeyEnrolled(passkey_id=passkey_id, label="A")),
        (EventType.USER_PASSKEY_RENAMED, UserPasskeyRef(passkey_id=passkey_id)),
        (EventType.USER_PASSKEY_REMOVED, UserPasskeyRef(passkey_id=passkey_id)),
        (EventType.USER_SIGNED_OUT, Empty()),
        (EventType.USER_SESSIONS_REVOKED, UserSessionsRevoked(count=1)),
    )


def _account(client, **fields) -> str:
    body = {"bank": "Beispiel", "opening_balance": "0.00", "opening_balance_on": "2026-01-01"}
    return create(client, "/api/accounts", body | fields)["id"]


def test_no_kept_route_reads_the_session_or_passkey_tables(
    client_a, kept, app_engine, auth_a, dbname
):
    """Every KEPT_ROUTES entry is requested as the warm step requests it (`internal_get`), and
    no SQL statement it sends names a table outside the data version. Between its two
    computations the test writes what the version does not cover (one of each sign-in,
    passkey and sign-out event migrations 0051 and 0055 exempt, a session and a passkey row),
    and the second answer must be the same bytes: a route that read any of it, by any name,
    would serve it stale."""
    from db.db_support import _admin
    from sqlalchemy import event

    ws, user = auth_a.workspace_id, auth_a.user_id
    with _admin(dbname) as conn:  # the member exists, so created_by (FK to app_user) is real
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (user, ws, f"{user}@example.test"),
        )
        # The member's passkey and session, which the unversioned writes below move.
        passkey = conn.execute(
            "INSERT INTO auth_passkey (workspace_id, user_id, credential_id, public_key, "
            "backup_eligible, backed_up, label) VALUES (%s, %s, %s, %s, false, false, 'k') "
            "RETURNING id",
            (ws, user, uuid.uuid4().bytes, b"k"),
        ).fetchone()[0]
        session = conn.execute(
            "INSERT INTO auth_session (workspace_id, user_id, passkey_id, token_hash, "
            "expires_at, idle_expires_at) VALUES (%s, %s, %s, %s, now() + interval '1 hour', "
            "now() + interval '1 hour') RETURNING id",
            (ws, user, passkey, uuid.uuid4().bytes + uuid.uuid4().bytes),
        ).fetchone()[0]

    exempt = {type.value for type, _ in _sign_in_events(passkey)}
    assert exempt == set(_exempt_types())

    def unversioned_writes(n: int) -> None:
        """What a sign-in, a sign-out and the passkey screens write and the data version does
        not cover (the events migrations 0051 and 0055 exempt, and the two auth tables): each
        exempt event of the member, a session slide and a passkey sign count. A kept answer
        that read any of it would differ on its second computation."""
        with workspace_transaction(app_engine, ws) as conn:
            for type, payload in _sign_in_events(passkey):
                append_user_event(
                    conn, type, payload, workspace_id=ws, user_id=user, now=datetime.now(UTC)
                )
            conn.execute(
                text("UPDATE auth_session SET last_seen_at = now() WHERE id = :id"),
                {"id": session},
            )
            conn.execute(
                text("UPDATE auth_passkey SET sign_count = :n WHERE id = :id"),
                {"id": passkey, "n": n},
            )

    # A flat bought in 2026, a year the tax and projection rules know.
    prop = new_property(client_a, notarized_on="2026-01-15", purchase_date="2026-03-01")
    unit = new_unit(client_a, prop["id"])
    tenant = new_tenant(client_a)
    tenancy = new_tenancy(client_a, unit["id"], tenant["id"], start_date="2026-03-01")
    loan = loan_body(
        prop["id"],
        disbursed_on="2026-02-27",
        first_payment_on="2026-03-30",
        fixed_until="2036-02-28",
    )
    ids = {
        "property_id": prop["id"],
        "unit_id": unit["id"],
        "tenant_id": tenant["id"],
        "tenancy_id": tenancy["id"],
        "loan_id": create(client_a, "/api/loans", loan)["id"],
        "owner_id": create(client_a, "/api/owners", {"full_name": "Owner A"})["id"],
        "document_id": ok(upload(client_a), 201)["id"],
        "tax_year": "2026",  # /api/tax/{tax_year}/guide (phase 10 D290)
    }
    whole = {"owner_id": ids["owner_id"], "share": {"numerator": 1, "denominator": 1}}
    ownership = f"/api/properties/{ids['property_id']}/ownership"
    ok(client_a.put(ownership, json={"shares": [whole]}))
    pay = {"kind": "expected_gross_pay", "tax_year": 2026, "value": {"amount": "50000.00"}}
    create(client_a, "/api/income-inputs", pay | {"owner_id": ids["owner_id"]})
    bank = _account(client_a, label="Giro", iban="DE50100000000000000001")
    exchange = _account(client_a, label="Börse", kind="exchange", provider="BISON")
    wallet = _account(client_a, label="Wallet", kind="wallet", provider="Testwallet")
    ok(client_a.post("/api/net-worth/snapshots"))
    ids["run_id"] = ok(client_a.get("/api/net-worth/snapshots"))[0]["net_worth_run_id"]
    account_for = {
        "/api/accounts/{account_id}/exchange": exchange,
        "/api/accounts/{account_id}/movements": exchange,
    }

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    requested = set()
    event.listen(app_engine, "before_cursor_execute", record)
    try:
        for template in KEPT_ROUTES:
            if template in _NOT_REQUESTED:
                continue
            wallet_route = template.startswith("/api/wallets/")
            account = account_for.get(template, wallet if wallet_route else bank)
            path = template.format(**ids, account_id=account)
            statements.clear()
            response = asyncio.run(
                kept.internal_get(auth_a, path, _QUERY.get(template), bypass=True)
            )
            for statement in statements:
                for table in _UNVERSIONED_TABLES:
                    assert table not in statement, (template, table)
                lowered = statement.lower()
                assert "procrastinate_" not in lowered, template  # the job queue: unversioned
                for clock in ("now()", "current_timestamp", "clock_timestamp", "localtime"):
                    assert clock not in lowered, (template, clock)
            assert response.status == 200, (template, response.body[:300])
            assert statements, template
            # Between the two computations only what the data version does not cover moves.
            unversioned_writes(len(requested) + 1)
            again = asyncio.run(kept.internal_get(auth_a, path, _QUERY.get(template), bypass=True))
            assert again.body == response.body, template  # the 15:00 check would empty the store
            requested.add(template)
    finally:
        event.remove(app_engine, "before_cursor_execute", record)
    assert requested | _NOT_REQUESTED == set(KEPT_ROUTES)
    assert not requested & _NOT_REQUESTED


def test_every_kept_route_is_warmed_or_named_with_a_reason():
    """§7.13 (e) 1: a kept route the warm does not ask is listed with why; a route added to
    KEPT_ROUTES without either fails here."""
    assert set(WARMED) | set(NOT_WARMED) == set(KEPT_ROUTES)
    assert not set(WARMED) & set(NOT_WARMED)
    assert set(NOT_WARMED) <= set(KEPT_ROUTES), set(NOT_WARMED) - set(KEPT_ROUTES)
    assert all(reason.strip() for reason in NOT_WARMED.values())
    assert set(EXPECTED_REFUSALS) <= set(WARMED)


def test_the_warm_asks_every_warmed_route_as_the_pages_ask_it(client_a, kept, auth_a, dbname):
    """In a world with a property, a unit, a loan, two owners (one with pay, one without), a
    bank account, an exchange and a wallet, the warm asks exactly the WARMED templates; an
    owner with no pay answers anlage_n.no_pay, which Übersicht expects (§7.13 (e) 7)."""
    from db.db_support import _admin

    with _admin(dbname) as conn:  # the member exists, so created_by (FK to app_user) is real
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )
    prop = new_property(client_a, notarized_on="2026-01-15", purchase_date="2026-03-01")
    new_unit(client_a, prop["id"])
    loan = loan_body(
        prop["id"],
        disbursed_on="2026-02-27",
        first_payment_on="2026-03-30",
        fixed_until="2036-02-28",
    )
    create(client_a, "/api/loans", loan)
    paid = create(client_a, "/api/owners", {"full_name": "Owner A"})["id"]
    create(client_a, "/api/owners", {"full_name": "Owner B"})
    pay = {"kind": "expected_gross_pay", "tax_year": 2026, "value": {"amount": "50000.00"}}
    create(client_a, "/api/income-inputs", pay | {"owner_id": paid})
    _account(client_a, label="Giro", iban="DE50100000000000000001")
    _account(client_a, label="Börse", kind="exchange", provider="BISON")
    _account(client_a, label="Wallet", kind="wallet", provider="Testwallet")

    result = asyncio.run(warm_member(kept, auth_a, date(2026, 11, 21)))

    assert result.failed == 0
    assert result.expected == 1  # Owner B: anlage_n.no_pay
    assert result.templates == set(WARMED), set(WARMED) ^ result.templates
    assert result.requests == len(kept) + result.expected  # every other answer is kept
    # Haushalt › Monat's twelve months up to the last full one, as the page asks them.
    months = {key.query for key, _ in kept.items() if key.path == "/api/household/cashflow"}
    assert (("month", "2025-11"),) in months
    assert (("month", "2026-10"),) in months
    # Konten › Umsätze's month card: the running month.
    assert any(
        key.path.endswith("/month") and key.query == (("month", "2026-11"),)
        for key, _ in kept.items()
    )
    # §7.13 (e) 3: the added routes keep the store far below its cap (one member, one language).
    print(f"kept after the warm: {len(kept)} answers, {kept.size_bytes} bytes")
    assert kept.size_bytes < MAX_BYTES // 50


def _stored_key(**changes) -> Key:
    ws = uuid.UUID(int=7)
    fields = {
        "path": "/api/household/cashflow",
        "query": (("month", "2026-10"),),
        "workspace_id": ws,
        "user_id": ws,
        "stamp": Stamp(3, date(2026, 11, 21), date(2026, 11, 21)),
        "locale": "de",
        "app_version": "v1",
        "rule_version": "r1",
    }
    return Key(**(fields | changes))


def test_a_miss_names_the_key_parts_that_differed_once(caplog):
    """§7.13 (e) 2: a person's miss within a day of a stored answer for the same path logs
    which part of the key differed, by name, with a version, day or language as its values
    and a query by its parameter names only; each stored key is explained once."""
    now = [datetime(2026, 11, 21, 10, tzinfo=UTC)]
    store = KeptAnswers(clock=lambda: now[0])
    stored = _stored_key()
    store.put(stored, _entry(10))
    asked = _stored_key(locale="en", stamp=Stamp(4, date(2026, 11, 21), date(2026, 11, 21)))
    caplog.set_level("INFO", logger="hausbuch.api.kept_answers")
    parts = store.explain_miss(asked, "/api/household/cashflow")
    assert parts == {"locale": ["de", "en"], "version": ["3", "4"]}
    logged = [
        r.fields
        for r in caplog.records
        if getattr(r, "fields", {}).get("event") == "kept_answer_miss_explained"
    ]
    assert logged == [
        {
            "event": "kept_answer_miss_explained",
            "route": "/api/household/cashflow",
            "parts": ["locale", "version"],
            "differences": {"locale": ["de", "en"], "version": ["3", "4"]},
            "stored_minutes_ago": 0,
        }
    ]
    assert store.explain_miss(asked, "/api/household/cashflow") is None  # once


def test_a_miss_on_another_query_names_the_parameter_never_its_value():
    store = KeptAnswers(clock=lambda: datetime(2026, 11, 21, 10, tzinfo=UTC))
    store.put(_stored_key(), _entry(10))
    asked = _stored_key(query=(("month", "2026-09"),))
    assert store.explain_miss(asked, "/api/household/cashflow") == {"query": ["month"]}


def test_a_miss_a_day_after_the_answer_was_stored_is_not_explained():
    now = [datetime(2026, 11, 21, 10, tzinfo=UTC)]
    store = KeptAnswers(clock=lambda: now[0])
    store.put(_stored_key(), _entry(10))
    now[0] += timedelta(days=1, minutes=1)
    berlin = Stamp(3, date(2026, 11, 22), date(2026, 11, 22))
    assert store.explain_miss(_stored_key(stamp=berlin), "/api/household/cashflow") is None


def test_a_miss_after_a_write_is_explained_though_the_answer_was_dropped():
    """`put` under a newer version drops the older answers; the side record still names the
    version as what differed."""
    store = KeptAnswers(clock=lambda: datetime(2026, 11, 21, 10, tzinfo=UTC))
    store.put(_stored_key(), _entry(10))
    newer = Stamp(4, date(2026, 11, 21), date(2026, 11, 21))
    store.put(_stored_key(path="/api/properties", stamp=newer), _entry(10))
    assert store.get(_stored_key()) is None
    parts = store.explain_miss(_stored_key(stamp=newer), "/api/household/cashflow")
    assert parts == {"version": ["3", "4"]}


def test_a_persons_miss_in_another_language_is_explained(client_a, kept, caplog):
    ok(client_a.get("/api/properties"))
    caplog.set_level("INFO", logger="hausbuch.api.kept_answers")
    ok(client_a.get("/api/properties", headers={"accept-language": "en"}))
    explained = [
        r.fields
        for r in caplog.records
        if getattr(r, "fields", {}).get("event") == "kept_answer_miss_explained"
    ]
    assert [e["parts"] for e in explained] == [["locale"]]
    assert explained[0]["route"] == "/api/properties"


def _member_exists(dbname: str, auth) -> None:
    """The fake resolver's member as a real app_user row (member_locale refers to it)."""
    from db.db_support import _admin

    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth.user_id, auth.workspace_id, f"{auth.user_id}@example.test"),
        )


def _stored_locales(dbname: str, ws: uuid.UUID) -> list[tuple[str, datetime]]:
    from db.db_support import _admin

    with _admin(dbname) as conn:
        return conn.execute(
            "SELECT locale, updated_at FROM member_locale WHERE workspace_id = %s", (ws,)
        ).fetchall()


def test_a_members_language_is_stored_once_and_only_when_it_changes(client_a, kept, auth_a, dbname):
    """§7.13 (e) 3: the language of a person's kept request is stored after the answer, only
    when it changed; the row is outside the data version, so the version stays."""
    _member_exists(dbname, auth_a)
    ws = auth_a.workspace_id
    version = read_data_version(kept.engine, ws)
    ok(client_a.get("/api/properties", headers={"Accept-Language": "en"}))
    [(locale, first)] = _stored_locales(dbname, ws)
    assert locale == "en"
    ok(client_a.get("/api/reminders", headers={"Accept-Language": "en"}))
    kept._locales.clear()  # a start: the first request writes, the row stays as it was
    ok(client_a.get("/api/properties", headers={"Accept-Language": "en"}))
    assert _stored_locales(dbname, ws) == [("en", first)]
    ok(client_a.get("/api/properties", headers={"Accept-Language": "de"}))
    [(locale, changed)] = _stored_locales(dbname, ws)
    assert locale == "de" and changed >= first
    assert read_data_version(kept.engine, ws) == version


def test_the_warm_after_a_start_asks_in_the_stored_language(
    client_a, kept, app_engine, settings, blob_store, auth_a, dbname
):
    _member_exists(dbname, auth_a)
    new_property(client_a)
    ok(client_a.get("/api/properties", headers={"Accept-Language": "en"}))
    kept.clear()
    kept._locales.clear()  # a new process: nothing in memory
    deps = JobDeps(
        engine=app_engine,
        settings=settings,
        blob_store=blob_store,
        model_client=None,
        clock=kept.clock,
        kept_answers=kept,
    )
    run = asyncio.run(run_warm(deps, members=[auth_a]))
    assert run.failed == 0
    assert {key.locale for key, _ in kept.items()} == {"en"}
    assert kept.locale_of(auth_a.user_id) == "en"


def test_the_language_a_process_saw_wins_over_the_stored_one(kept, auth_a, client_a, dbname):
    _member_exists(dbname, auth_a)
    ok(client_a.get("/api/properties", headers={"Accept-Language": "en"}))
    kept._locales[auth_a.user_id] = "de"  # seen since the row was written
    assert kept.load_locales([auth_a.workspace_id]) == 0
    assert kept.locale_of(auth_a.user_id) == "de"
