"""`hausbuch perf-overview` (hausbuch.perf_probe): every read of Übersicht timed as the schema
owner on a read-only engine. Every request answers 200 and runs SQL, nothing is written, no
response content is printed, and a write inside the probe's session is refused and reported
as WRITE-REFUSED. Invented owners, tenants and amounts only (tests/db/matching_support.py)."""

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta

import psycopg
import pytest
from db.db_support import OWNER, make_engine
from db.matching_support import T0, World
from db.test_cli_evals_report import _owner_url
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import DBAPIError

from hausbuch import cli, perf_probe
from hausbuch.auth.provisioning import provision_workspace
from hausbuch.auth.sessions import open_session
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import owner as owner_table
from hausbuch.schemas.performance import IncomeInputCreate
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.income_inputs import create_income_input
from hausbuch.services.owners import create_owner

pytestmark = pytest.mark.db

TENANT_NAME = "Erika Mustermieterin"


@pytest.fixture
def world(app_engine, dbname, tmp_path) -> World:
    """A provisioned workspace (its user and a session) with the matching World's property,
    unit, account and import, a tenancy, a loan and an owner."""
    owner = make_engine(dbname, OWNER)
    try:
        provisioned = provision_workspace(
            owner,
            name="Probe",
            email=f"probe-{uuid.uuid4().hex[:8]}@example.org",
            display_name="Test",
            now=T0 - timedelta(days=30),
        )
    finally:
        owner.dispose()
    world = World(app_engine, provisioned.workspace_id)
    world.tenancy(name=TENANT_NAME)
    world.loan()
    owner_id = world.cmd(create_owner, OwnerCreate(full_name="Owner A")).id
    # Expected pay for the year the page asks the Anlage N for, so it answers 200 rather than
    # the page's own „Arbeitslohn fehlt“ refusal (422 anlage_n.no_pay).
    body = {
        "owner_id": owner_id,
        "kind": "expected_gross_pay",
        "tax_year": datetime.now(LOCAL_TIME_ZONE).year,
        "value": {"amount": "52000.00"},
    }
    world.cmd(create_income_input, IncomeInputCreate.model_validate(body))
    with workspace_transaction(app_engine, world.ws) as conn:
        open_session(
            conn,
            perf_probe.probe_settings(tmp_path / "blobs"),
            workspace_id=world.ws,
            user_id=provisioned.user_id,
            passkey_id=None,
            now=T0,
        )
    return world


def _events(world) -> int:
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(select(func.count()).select_from(event_table)).scalar_one()


def test_probe_times_every_read_of_the_page_and_writes_nothing(world, dbname):
    before = _events(world)
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    lines: list[str] = []
    try:
        passes = perf_probe.run_probe(engine, world.ws, repeat=2, out=lines.append)
    finally:
        engine.dispose()

    assert len(passes) == 2
    labels = [t.label for t in passes[0].timings]
    assert labels == [t.label for t in passes[1].timings]
    # The shell, the first wave and the per-entity wave (one property, unit and owner).
    for path in (
        "/api/auth/me",
        "/api/inbox",
        "/api/properties",
        "/api/tenants",
        "/api/reminders",
        "/api/net-worth/overview",
        "/api/health",
        "/api/owners",
        "/api/health/net-worth-series",
        "/api/household-scenarios?include_history=true",
        "/api/auth/members",
        "/api/accounts",
        "/api/properties/equity",
        "/api/properties/payback",
        "/api/properties/{p1}/units",
        "/api/properties/{p1}/loans",
        "/api/units/{u1}/tenancies",
        "/api/properties/{p1}/flat-rate-check",
        "/api/properties/{p1}/checklist",
    ):
        assert path in labels, path
    assert labels.count("/api/inbox") == labels.count("/api/health") == 2
    assert any(re.fullmatch(r"/api/properties/\{p1\}/anlage-v\?tax_year=\d{4}", x) for x in labels)
    assert any(re.fullmatch(r"/api/owners/\{o1\}/anlage-n\?tax_year=\d{4}", x) for x in labels)
    assert any(x.startswith("/api/household/cashflow?month=") for x in labels)
    assert any(x.startswith("/api/health/months?from=") for x in labels)

    for one in passes:
        for timing in one.timings:
            assert timing.status == 200, (timing.label, timing.status, timing.error)
            assert timing.statements > 0, timing.label
            assert timing.size > 0, timing.label
    assert _events(world) == before

    out = "\n".join(lines)
    assert "write-refused paths: none" in out
    assert f"requests the page fires: {len(labels)}, distinct: {len(set(labels))}" in out
    assert out.count("slowest 5:") == 2
    # No response content: no name, address, IBAN or id from the data.
    for secret in (TENANT_NAME, "Lindenstraße", "Owner A", "Beispielbank", "DE89"):
        assert secret not in out
    assert str(world.property_id) not in out and str(world.unit_id) not in out


def test_cli_perf_overview_runs_as_the_owner(world, dbname, capsys):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["perf-overview", "--workspace", str(world.ws), "--repeat", "1"]
    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "db round-trip (median of 20 x SELECT 1):" in out
    assert "pass 1 of 1" in out and "write-refused paths: none" in out
    assert TENANT_NAME not in out

    assert cli.main(["perf-overview", "--workspace", str(world.ws), "--repeat", "0"], environ)
    assert "--repeat must be at least 1" in capsys.readouterr().err


def test_a_write_in_the_probe_session_is_refused(world, dbname):
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    try:
        for _ in range(2):  # the second checkout comes after the pool's rollback
            with (
                pytest.raises(DBAPIError) as refused,
                workspace_transaction(engine, world.ws) as conn,
            ):
                conn.execute(update(owner_table).values(full_name="Changed"))
            assert isinstance(refused.value.orig, psycopg.errors.ReadOnlySqlTransaction)
            assert "Changed" not in str(refused.value)  # hide_parameters
        with workspace_transaction(world.engine, world.ws) as conn:
            names = conn.execute(select(owner_table.c.full_name)).scalars().all()
        assert names == ["Owner A"]
    finally:
        engine.dispose()


def test_a_get_that_writes_is_reported_as_write_refused(world, dbname, tmp_path):
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    try:
        counter = perf_probe.SqlCounter(engine)
        member = perf_probe.pick_member(engine, world.ws)
        assert member.has_session
        app = perf_probe.build_app(engine, member, tmp_path / "blobs")

        def writes() -> dict:
            with workspace_transaction(engine, world.ws) as conn:
                conn.execute(insert(owner_table).values(workspace_id=world.ws, full_name="New"))
            return {}

        app.add_api_route("/api/probe-writes", writes, methods=["GET"])

        async def one() -> perf_probe.Timing:
            async with perf_probe.probe_client(app) as client:
                run = perf_probe.PageRun(client, counter, T0.date(), perf_probe._Labels())
                await run.get("/api/probe-writes", "/api/probe-writes")
                return run.result.timings[0]

        timing = asyncio.run(one())
    finally:
        engine.dispose()
    assert timing.status == perf_probe.WRITE_REFUSED
    assert timing.error == "ReadOnlySqlTransaction"
    assert timing.statements > 0


def test_profile_prints_code_locations_and_times_only(world, dbname, capsys):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["perf-overview", "--workspace", str(world.ws), "--profile", "/api/health"]
    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    _, profiles = out.split("write-refused paths: none", 1)

    # The last (warm) pass only: /api/health twice, /api/health/months and the series once.
    headings = re.findall(r"^profile of (\S+) \(last pass", profiles, re.MULTILINE)
    assert headings.count("/api/health") == 2
    assert "/api/health/net-worth-series" in headings
    assert any(h.startswith("/api/health/months?from=") for h in headings)
    assert len(headings) == 4
    assert out.count("  profiled") == 4  # the timing lines of pass 2 only

    assert profiles.count("top 30 by cumulative time:") == 4
    assert profiles.count("top 20 by own time:") == 4
    assert "ncalls  tottime_ms  cumtime_ms  module:function:line" in profiles
    assert re.search(r"hausbuch\.services\.health:health:\d+", profiles)
    assert re.search(r"hausbuch\.rules\.health\.month:household_month:\d+", profiles)
    assert "net worth, valuation, prices, holdings and household month: calls" in profiles

    # Every profile row is a count, two times and a code location, nothing else.
    row = re.compile(r"^\s+\d+(/\d+)?\s+\d+\.\d(\s+\d+\.\d)?\s+\S+:\S.*:\d+$")
    for line in profiles.splitlines():
        if line.startswith(" ") and "ncalls" not in line and not line.startswith("  …"):
            assert row.match(line), line
    for secret in (TENANT_NAME, "Lindenstraße", "Owner A", "Beispielbank", "DE89", "52000"):
        assert secret not in out
    assert str(world.ws) not in profiles and str(world.property_id) not in profiles

    argv = [*argv, "--repeat", "1"]
    assert cli.main(argv, environ) == cli.EXIT_ERROR
    assert "--profile needs --repeat 2" in capsys.readouterr().err


def test_body_digest_ignores_key_order_and_spacing():
    a = perf_probe.body_digest(b'{"b": [1, {"y": 2, "x": "\xc3\xa4"}], "a": null}')
    b = perf_probe.body_digest(b'{"a":null,"b":[1,{"x":"\xc3\xa4","y":2}]}')
    assert a == b and re.fullmatch(r"[0-9a-f]{16}", a)
    canonical = json.dumps(
        {"a": None, "b": [1, {"x": "ä", "y": 2}]}, sort_keys=True, separators=(",", ":")
    )
    assert a == hashlib.sha256(canonical.encode()).hexdigest()[:16]
    assert perf_probe.body_digest(b'{"a": 1}') != perf_probe.body_digest(b'{"a": 2}')
    assert perf_probe.body_digest(b"not json") == hashlib.sha256(b"not json").hexdigest()[:16]


def _digests(out: str) -> dict[str, list[str]]:
    """Per route, the digests of the last pass, in the order fired."""
    last = out.split("pass 2 of 2: warm", 1)[1].split("total:", 1)[0]
    found: dict[str, list[str]] = {}
    for line in last.splitlines()[2:]:
        fields = line.split()
        found.setdefault(fields[0], []).append(fields[-1])
    return found


def test_digest_column_on_the_last_pass_is_stable(world, dbname, capsys):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["perf-overview", "--workspace", str(world.ws), "--digest"]
    assert cli.main(argv, environ) == cli.EXIT_OK
    first = capsys.readouterr().out
    first_pass = first.split("pass 1 of 2", 1)[1].split("total:", 1)[0]
    assert "digest" not in first_pass  # the first pass prints none
    header = first.split("pass 2 of 2: warm\n", 1)[1].splitlines()[0]
    assert header.split()[-1] == "digest"

    digests = _digests(first)
    assert len(digests) == 24
    for route, values in digests.items():
        assert all(re.fullmatch(r"[0-9a-f]{16}", v) for v in values), route
    # A route fired twice in one pass answers the same body.
    assert len(set(digests["/api/inbox"])) == len(set(digests["/api/health"])) == 1

    # Unchanged data, a second run: the same digest per path (the gate for a speed-up).
    assert cli.main(argv, environ) == cli.EXIT_OK
    assert _digests(capsys.readouterr().out) == digests
    assert TENANT_NAME not in first


def test_check_kept_serves_the_later_pass_from_the_store_and_finds_no_difference(world, dbname):
    """Phase 10 §7.4: with the kept answers on, every keepable request of the second pass is
    a hit, and every kept answer equals its recomputation."""
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    lines: list[str] = []
    try:
        passes = perf_probe.run_probe(engine, world.ws, repeat=2, check_kept=True, out=lines.append)
    finally:
        engine.dispose()
    second = passes[1].timings
    hits = [t.label for t in second if t.hit]
    assert "/api/properties" in hits and "/api/accounts" in hits
    assert "/api/inbox" in hits  # kept until its expiry since §7.5 (b); unkept until then
    assert not any(t.hit for t in second if t.label == "/api/auth/me")
    out = "\n".join(lines)
    kept = sum(t.hit for t in second + passes[1].extra)  # the other kept routes too (§7.5 (a))
    assert f"kept answers: {kept} of {kept} keepable requests" in out
    assert ", different: 0, failed: 0, moved meanwhile (not compared): 0" in out
    assert TENANT_NAME not in out


def test_check_kept_writes_no_language_on_the_read_only_engine(world, dbname):
    """Phase 10 §7.13 (e) 3: the probe's kept answers do not store a member's language
    (member_locale), so its read-only engine refuses nothing: every request of the page
    answers 200 in the first pass (where the language was first seen), no other kept route is
    refused, and the report names no write-refused path."""
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    lines: list[str] = []
    try:
        passes = perf_probe.run_probe(engine, world.ws, repeat=2, check_kept=True, out=lines.append)
    finally:
        engine.dispose()
    first = passes[0]
    for timing in first.timings:
        assert timing.status == 200, (timing.label, timing.status, timing.error)
    for timing in first.extra:  # a route whose record the workspace lacks may answer 404
        assert timing.status != perf_probe.WRITE_REFUSED, timing.label
    assert "write-refused paths: none" in "\n".join(lines)


def test_check_kept_also_compares_every_other_kept_route(world, dbname):
    """Phase 10 §7.5 (a): with --check-kept each pass also requests every kept route the page
    does not fire, with the workspace's own ids; a route whose kind of id the workspace lacks
    is named as skipped. Together they cover KEPT_ROUTES, and none of it reaches the page's
    timing table or prints an id."""
    from hausbuch.api.kept_answers import KEPT_ROUTES, kept_route

    engine = perf_probe.read_only_engine(_owner_url(dbname))
    lines: list[str] = []
    try:
        passes = perf_probe.run_probe(engine, world.ws, repeat=2, check_kept=True, out=lines.append)
    finally:
        engine.dispose()
    first, second = passes
    assert [t.label for t in first.extra] == [t.label for t in second.extra]
    assert first.skipped == second.skipped
    page = {kept_route(t.label.split("?")[0]) for t in second.timings} - {None}
    extra = {kept_route(t.label.split("?")[0]) for t in second.extra}
    skipped = {template for template, _ in second.skipped}
    assert page | extra | skipped == set(KEPT_ROUTES)
    assert not (page & extra) and not (page & skipped) and not (extra & skipped)
    # The world has no exchange, wallet, bank line, stored month-end, suggestion or return
    # input.
    assert {missing for _, missing in second.skipped} == {
        "bank line",
        "exchange account",
        "wallet",
        "stored month-end",
        "suggestion",
        "return input",
    }
    assert "/api/loans/{l1}/schedule" in [t.label for t in second.extra]
    assert any(t.label.startswith("/api/transfers?month=") for t in second.extra)
    # The world's data, not the requests: its property has no owners' shares (404), and it was
    # bought in 2024, before the first tax rule set, so its performance answers 422
    # projection.no_rule_set. Printed as findings, never dropped.
    findings = {(t.label, t.status) for t in second.extra if t.status != 200}
    assert findings == {
        ("/api/properties/{p1}/ownership", 404),
        ("/api/properties/{p1}/performance/actuals", 422),
        ("/api/properties/{p1}/performance/projection", 422),
    }
    for timing in second.extra:
        assert timing.hit == (timing.status == 200), timing.label
    out = "\n".join(lines)
    assert f"kept routes the page does not fire: {len(second.extra)} requested, " in out
    assert "skipped: /api/wallets/{account_id}/lots (no wallet)" in out
    assert "  not 200: /api/properties/{p1}/ownership  404" in out
    assert out.count("  not 200: ") == len(findings)
    assert ", different: 0, failed: 0, moved meanwhile (not compared): 0" in out
    assert str(world.property_id) not in out and str(world.account_id) not in out
    assert TENANT_NAME not in out


def test_the_probe_requests_no_other_kept_route_without_check_kept(world, dbname):
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    try:
        passes = perf_probe.run_probe(engine, world.ws, repeat=2, out=lambda _: None)
    finally:
        engine.dispose()
    assert not any(one.extra or one.skipped for one in passes)


def test_check_kept_fails_on_a_kept_answer_that_differs(world, dbname, monkeypatch):
    from hausbuch.api import kept_answers

    real = kept_answers.KeptAnswers.compare_all

    async def tampered(self):
        for _, entry in self.items():
            entry.body = entry.body + b" "
        return await real(self)

    monkeypatch.setattr(kept_answers.KeptAnswers, "compare_all", tampered)
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    try:
        with pytest.raises(perf_probe.ProbeError, match="kept answers differ"):
            perf_probe.run_probe(engine, world.ws, repeat=2, check_kept=True, out=lambda _: None)
    finally:
        engine.dispose()


def test_the_probe_keeps_nothing_without_check_kept(world, dbname):
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    try:
        passes = perf_probe.run_probe(engine, world.ws, repeat=2, out=lambda _: None)
    finally:
        engine.dispose()
    assert not any(t.hit for one in passes for t in one.timings)


def test_plan_times_the_plan_reads_and_the_fixed_previews_and_writes_nothing(world, dbname):
    """Phase 10 §7.10 (b): --plan adds Haushalt's plan, budget and goal reads and, once a month
    is closed, four fixed what-if previews, so a speed-up of the preview is digest-proven like
    the page. Every one answers 200 and nothing is written."""
    before = _events(world)
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    try:
        passes = perf_probe.run_probe(
            engine, world.ws, repeat=2, plan=True, digest=True, out=lambda _: None
        )
    finally:
        engine.dispose()
    last = {t.label: t for t in passes[-1].timings}
    for label in ("/api/household-plan", "/api/household-budget", "/api/household-goal"):
        assert last[label].status == 200, label
        assert last[label].digest is not None
    previews = [t for t in passes[-1].timings if t.label.startswith("POST ")]
    assert all(t.status == 200 for t in previews), [(t.label, t.status) for t in previews]
    assert _events(world) == before
    # Without --plan none of them is requested.
    engine = perf_probe.read_only_engine(_owner_url(dbname))
    try:
        plain = perf_probe.run_probe(engine, world.ws, repeat=1, out=lambda _: None)
    finally:
        engine.dispose()
    labels = {t.label for t in plain[0].timings}
    assert "/api/household-plan" not in labels
    assert not any(label.startswith("POST ") for label in labels)
