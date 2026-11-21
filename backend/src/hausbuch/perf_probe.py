"""`hausbuch perf-overview`: time every API read the Übersicht page makes, read-only.

Luis found Übersicht slow (21 Nov 2026); this measures before anything is changed. The probe
drives the real FastAPI app in process (create_app, routing, middlewares, the services, the
workspace transaction and serialisation, exactly as a request in production), through httpx's
ASGITransport, as one member of one workspace. Nothing else of production is replaced but the
session check: a probe resolver runs the same session lookup the real resolver runs (one READ
ONLY transaction over auth_session, here with a token hash that matches nothing) and then
answers with a real member and session of the workspace. The real resolver also writes
last_seen_at at most every five minutes per session; that write is not reproduced.

Read-only is enforced by the database, not by trust: every pooled connection is switched to
`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` when it is opened (committed, so no
rollback reverts it) and checked with SHOW before it is used. A statement that would write
fails with SQLSTATE 25006; the probe sees it through the engine's handle_error event and
prints that path as WRITE-REFUSED, which is a finding of its own. No lifespan runs: no job
worker, no start-up task.

Nothing from a response is printed or logged: per request only its route (ids replaced by
p1, u1, o1 …), status, time, SQL statement and transaction counts, time in SQL and the body's
size in bytes. Request logging is switched off while the probe runs, and a database error is
named by its class only (SQLAlchemy's text carries bound values).

The sequence follows the frontend as of 21 Nov 2026: the shell (AuthGate's GET /auth/me, the
menu's inbox count from app/(app)/useInboxCount.ts), then app/(app)/page.tsx with
components/overviewTiles.tsx, overviewTodos.tsx, reminders.tsx and healthFigures.tsx, each
request in the order the page fires it, duplicates included (the page fires /inbox,
/properties and /health twice). The page fires them in parallel; the probe runs them one
after another, so the sum is the server time, not the page's wall time.

`--profile <path substring>` (repeatable) runs each matching request of the last pass under
cProfile and prints, per request, the top functions by cumulative and by own time as
`module:function:line` with their call counts and times, and how often the net-worth,
valuation, price and household-month functions ran. Only code locations and numbers are
printed, never an argument or a value. Python 3.12's cProfile is interpreter-wide, so it also
sees the worker thread FastAPI runs a sync endpoint in, and the probe's own client code.

`--digest` adds a column to the last pass: the first 16 hex characters of the SHA-256 of each
response body, its JSON canonicalised first (sorted keys, no spaces), so a speed-up can be
checked to return the same bodies as before, path by path, without printing any body.
"""

import asyncio
import cProfile
import hashlib
import json
import logging
import pstats
import re
import statistics
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

from sqlalchemy import Engine, event, select, text

from hausbuch.api.auth_context import AuthContext, session_cookie_name
from hausbuch.auth import lookup
from hausbuch.db.session import create_app_engine, workspace_transaction
from hausbuch.db.tables import app_user, auth_session
from hausbuch.settings import Settings

READ_ONLY_SQLSTATE = "25006"  # read_only_sql_transaction
WRITE_REFUSED = "WRITE-REFUSED"
PROBE_ORIGIN = "https://probe.invalid"
POOL_SIZE = 5  # as the API's request pool (api/app.py REQUEST_POOL_SIZE)
ROUND_TRIPS = 20
SLOWEST = 5
TOP_CUMULATIVE = 30
TOP_OWN = 20
WATCHED_SHOWN = 40
# The functions whose call counts are printed per profiled request: the net worth and its
# valuation, the price reads, the holdings, the household month and the health service.
WATCHED_MODULES = (
    "hausbuch.services.net_worth",
    "hausbuch.rules.net_worth",
    "hausbuch.rules.valuation",
    "hausbuch.services.prices.overview",
    "hausbuch.services.holdings",
    "hausbuch.services.health",
    "hausbuch.rules.health.month",
    "hausbuch.rules.household.cashflow",
)
WATCHED_FUNCTIONS = (
    ("hausbuch.services.scenarios", "estimate_on_day"),
    ("hausbuch.services.depot", "latest_report_checks"),
)


class ProbeError(RuntimeError):
    """A refusal before anything is timed (no member, a connection not read-only)."""


# --- The read-only engine -------------------------------------------------------------


def _read_only_on_connect(dbapi_connection, _connection_record) -> None:
    """Every new physical connection: read-only for the whole session, committed (the RLS
    check before this listener rolled back, and the pool rolls back on return; an uncommitted
    SET would be reverted), then checked."""
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        dbapi_connection.commit()
        cursor.execute("SHOW default_transaction_read_only")
        (value,) = cursor.fetchone()
    dbapi_connection.rollback()
    if value != "on":
        raise ProbeError("the connection could not be made read-only; nothing was timed")


def make_read_only(engine: Engine) -> Engine:
    """Install the read-only switch on `engine` (after create_app_engine's RLS check)."""
    event.listen(engine, "connect", _read_only_on_connect)
    return engine


def assert_read_only(engine: Engine) -> None:
    """Refuse to time anything unless a pooled connection reports read-only."""
    with engine.connect() as conn:
        value = conn.execute(text("SHOW default_transaction_read_only")).scalar_one()
        conn.rollback()
    if value != "on":
        raise ProbeError("the engine is not read-only (make_read_only); nothing was timed")


def read_only_engine(url: str) -> Engine:
    """A pooled engine (as the API's, so a warm request reuses its connection) on `url`,
    read-only on every connection. hide_parameters keeps values out of any error text."""
    engine = create_app_engine(url, pool_size=POOL_SIZE, max_overflow=0, hide_parameters=True)
    return make_read_only(engine)


# --- SQL counting ---------------------------------------------------------------------


@dataclass
class SqlCount:
    statements: int = 0
    sql_s: float = 0.0
    transactions: int = 0  # commits and rollbacks: one round trip each, unseen by cursors
    write_refused: int = 0
    error: str | None = None  # the class of the last database error, never its text


class SqlCounter:
    """Engine events: statements and their time, commits and rollbacks, and errors. The
    requests run one after another, so one running count is enough."""

    def __init__(self, engine: Engine) -> None:
        self.count = SqlCount()
        self._started: list[float] = []
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)
        event.listen(engine, "handle_error", self._error)
        event.listen(engine, "commit", self._transaction)
        event.listen(engine, "rollback", self._transaction)

    def reset(self) -> SqlCount:
        done, self.count = self.count, SqlCount()
        self._started.clear()
        return done

    def _before(self, *_args: Any) -> None:
        self._started.append(time.perf_counter())

    def _after(self, *_args: Any) -> None:
        started = self._started.pop() if self._started else time.perf_counter()
        self.count.statements += 1
        self.count.sql_s += time.perf_counter() - started

    def _error(self, context: Any) -> None:
        if self._started:  # the statement failed: its time still counts
            self.count.statements += 1
            self.count.sql_s += time.perf_counter() - self._started.pop()
        original = context.original_exception
        self.count.error = type(original).__name__
        if getattr(original, "sqlstate", None) == READ_ONLY_SQLSTATE:
            self.count.write_refused += 1

    def _transaction(self, *_args: Any) -> None:
        self.count.transactions += 1


# --- The app as one member ------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    auth: AuthContext
    has_session: bool  # False: no auth_session row, so GET /auth/me cannot answer


def pick_member(engine: Engine, workspace_id: UUID) -> Member:
    """The member whose session was seen last (read only), else the workspace's first user
    with a session id that exists nowhere."""
    with workspace_transaction(engine, workspace_id) as conn:
        row = conn.execute(
            select(auth_session.c.id, auth_session.c.user_id, auth_session.c.passkey_id)
            .order_by(auth_session.c.last_seen_at.desc())
            .limit(1)
        ).one_or_none()
        if row is not None:
            auth = AuthContext(
                workspace_id=workspace_id,
                user_id=row.user_id,
                session_id=row.id,
                passkey_id=row.passkey_id,
            )
            return Member(auth, has_session=True)
        user_id = conn.execute(
            select(app_user.c.id).order_by(app_user.c.created_at, app_user.c.id).limit(1)
        ).scalar_one_or_none()
    if user_id is None:
        raise ProbeError("the workspace has no user (check --workspace)")
    auth = AuthContext(
        workspace_id=workspace_id, user_id=user_id, session_id=uuid4(), passkey_id=None
    )
    return Member(auth, has_session=False)


class ProbeResolver:
    """The SessionResolver protocol: the real resolver's lookup (one READ ONLY transaction,
    a hash that matches nothing), then the probe's member."""

    def __init__(self, engine: Engine, auth: AuthContext) -> None:
        self.engine = engine
        self.auth = auth

    def resolve(self, token: str, *, now: datetime) -> AuthContext | None:
        lookup.find_session(self.engine, b"\x00" * 32)
        return self.auth


def probe_settings(blob_root: Path) -> Settings:
    """Settings for the probe's app: the routes read only the database. The keys are random,
    so a key typed under Einstellungen › Zugangsschlüssel reads as unreadable (the inbox may
    show that card); the database URL is unused (the engine is passed)."""
    return Settings(
        database_url="postgresql+psycopg://unused",
        iban_hmac_key=bytes(32),
        session_key=uuid4().bytes + uuid4().bytes,
        rp_id="probe.invalid",
        origin=PROBE_ORIGIN,
        blob_store="local",
        blob_root=blob_root,
        run_worker=False,
    )


def build_app(engine: Engine, member: Member, blob_root: Path):
    """create_app with the probe's resolver and no worker; its lifespan is never run."""
    from hausbuch.api.app import create_app
    from hausbuch.storage import LocalBlobStore

    settings = probe_settings(blob_root)
    resolver = ProbeResolver(engine, member.auth)
    return create_app(settings, engine, LocalBlobStore(blob_root), resolver, worker=None)


# --- The page's requests --------------------------------------------------------------


@dataclass
class Timing:
    label: str  # the route with ids as p1, u1, o1 …
    status: int | str
    ms: float
    statements: int
    sql_ms: float
    transactions: int
    size: int
    error: str | None = None
    profiled: bool = False
    digest: str | None = None  # --digest, the last pass only


@dataclass
class Pass:
    timings: list[Timing] = field(default_factory=list)
    profiles: list[tuple[str, pstats.Stats]] = field(default_factory=list)


class _Labels:
    """Ids as short ordinals per kind, the same within one run."""

    def __init__(self) -> None:
        self._ids: dict[tuple[str, str], str] = {}

    def __call__(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._ids:
            count = sum(1 for k, _ in self._ids if k == kind)
            self._ids[key] = f"{kind}{count + 1}"
        return self._ids[key]


def last_full_month(today: date) -> str:
    """components/cashflow.tsx lastFullMonth: the month before today's."""
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{year:04d}-{month:02d}"


def body_digest(body: bytes) -> str:
    """The first 16 hex characters of the SHA-256 of a response body; a JSON body is
    canonicalised first (sorted keys, no spaces), so key order never changes it."""
    try:
        # Exactly json.dumps(sort_keys=True, separators=(",", ":")), so it can be reproduced.
        canonical = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")).encode()
    except ValueError:
        canonical = body
    return hashlib.sha256(canonical).hexdigest()[:16]


class PageRun:
    """One pass over the page's requests, in the order the page fires them."""

    def __init__(
        self,
        client: Any,
        counter: SqlCounter,
        today: date,
        labels: _Labels,
        profile: Sequence[str] = (),
        digest: bool = False,
    ) -> None:
        self.client = client
        self.digest = digest
        self.counter = counter
        self.today = today
        self.labels = labels
        self.profile = tuple(profile)
        self.result = Pass()

    async def get(self, path: str, label: str, query: dict[str, Any] | None = None) -> Any:
        url = path + (f"?{urlencode(query)}" if query else "")
        shown = label + (f"?{urlencode(query)}" if query else "")
        profiler = cProfile.Profile() if any(x in shown for x in self.profile) else None
        self.counter.reset()
        started = time.perf_counter()
        if profiler is not None:
            profiler.enable()
        try:
            response = await self.client.get(url)
            body = response.content
        finally:
            if profiler is not None:
                profiler.disable()
        ms = (time.perf_counter() - started) * 1000
        if profiler is not None:
            self.result.profiles.append((shown, pstats.Stats(profiler)))
        sql = self.counter.reset()
        status: int | str = WRITE_REFUSED if sql.write_refused else response.status_code
        error = sql.error if response.status_code >= 500 or sql.write_refused else None
        timing = Timing(
            label=shown,
            status=status,
            ms=ms,
            statements=sql.statements,
            sql_ms=sql.sql_s * 1000,
            transactions=sql.transactions,
            size=len(body),
            error=error,
            profiled=profiler is not None,
            digest=body_digest(body) if self.digest else None,
        )
        self.result.timings.append(timing)
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def run(self) -> Pass:
        year = self.today.year
        month = last_full_month(self.today)
        # The shell: AuthGate, then the menu's count.
        await self.get("/api/auth/me", "/api/auth/me")
        await self.get("/api/inbox", "/api/inbox")
        # The page's first wave (page.tsx, overviewTiles.tsx, overviewTodos.tsx,
        # reminders.tsx, healthFigures.tsx), in the order the components mount.
        properties = await self.get("/api/properties", "/api/properties") or []
        await self.get("/api/tenants", "/api/tenants")
        await self.get("/api/reminders", "/api/reminders")
        await self.get("/api/net-worth/overview", "/api/net-worth/overview")
        await self.get("/api/health", "/api/health")
        await self.get("/api/household/cashflow", "/api/household/cashflow", {"month": month})
        months = {"from": month, "to": month}
        await self.get("/api/health/months", "/api/health/months", months)
        await self.get("/api/inbox", "/api/inbox")
        # The property tile (PR #513): page.tsx's useLoad(loadPaidIn), then loadPayback.
        await self.get("/api/properties/equity", "/api/properties/equity")
        await self.get("/api/properties/payback", "/api/properties/payback")
        await self.get("/api/properties", "/api/properties")
        owners = await self.get("/api/owners", "/api/owners") or []
        await self.get("/api/tax/return-estimate", "/api/tax/return-estimate", {"tax_year": year})
        await self.get("/api/health", "/api/health")
        await self.get("/api/health/net-worth-series", "/api/health/net-worth-series")
        scenarios = {"include_history": "true"}
        await self.get("/api/household-scenarios", "/api/household-scenarios", scenarios)
        await self.get("/api/auth/members", "/api/auth/members")
        await self.get("/api/accounts", "/api/accounts")
        # The second wave: per property, per unit, per owner.
        for prop in properties:
            pid, p = prop["id"], self.labels("p", prop["id"])
            base = f"/api/properties/{pid}"
            units = await self.get(f"{base}/units", f"/api/properties/{{{p}}}/units") or []
            await self.get(f"{base}/loans", f"/api/properties/{{{p}}}/loans")
            for unit in units:
                u = self.labels("u", unit["id"])
                path = f"/api/units/{unit['id']}/tenancies"
                await self.get(path, f"/api/units/{{{u}}}/tenancies")
        for prop in properties:
            pid, p = prop["id"], self.labels("p", prop["id"])
            base = f"/api/properties/{pid}"
            await self.get(f"{base}/flat-rate-check", f"/api/properties/{{{p}}}/flat-rate-check")
            checklist = await self.get(f"{base}/checklist", f"/api/properties/{{{p}}}/checklist")
            if checklist is not None:
                await self.get(
                    f"{base}/anlage-v",
                    f"/api/properties/{{{p}}}/anlage-v",
                    {"tax_year": checklist["tax_year"]},
                )
        for owner in owners:
            o = self.labels("o", owner["id"])
            path = f"/api/owners/{owner['id']}/anlage-n"
            await self.get(path, f"/api/owners/{{{o}}}/anlage-n", {"tax_year": year})
        return self.result


# --- Measuring ------------------------------------------------------------------------


def round_trip_ms(engine: Engine, n: int = ROUND_TRIPS) -> float:
    """Median of `n` × SELECT 1 on one pooled connection: the network's share of each
    statement, to subtract (the probe runs from the operator's computer, not the private
    network)."""
    samples = []
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        for _ in range(n):
            started = time.perf_counter()
            conn.execute(text("SELECT 1"))
            samples.append((time.perf_counter() - started) * 1000)
        conn.rollback()
    return statistics.median(samples)


def empty_transaction_ms(engine: Engine, workspace_id: UUID, n: int = ROUND_TRIPS) -> float:
    """Median of `n` empty workspace transactions, as every request opens them: the pool's
    checkout (with its pre-ping), BEGIN, the workspace SET and the COMMIT. The SET and the
    COMMIT are seen by the counter; the pre-ping and BEGIN are not, so this is what a
    transaction costs beyond its counted trips."""
    samples = []
    for _ in range(n + 1):
        started = time.perf_counter()
        with workspace_transaction(engine, workspace_id):
            pass
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples[1:])


def open_pool(engine: Engine) -> float:
    """Open the pool's connections before the first timed request; their connect time (TLS,
    login, the RLS and read-only checks) is printed apart, as production pays it once."""
    started = time.perf_counter()
    connections = [engine.connect() for _ in range(POOL_SIZE)]
    for conn in connections:
        conn.close()
    return (time.perf_counter() - started) * 1000


@contextmanager
def _quiet_logging() -> Iterator[None]:
    """No request log line, no error log (they could carry ids or SQL), while timing."""
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


@asynccontextmanager
async def probe_client(app: Any) -> AsyncIterator[Any]:
    """An httpx AsyncClient on the app through ASGITransport (no lifespan), with the probe's
    session cookie and a browser's Origin and language."""
    import httpx2  # dev dependency (the operator runs `uv run`); kept out of the API's imports

    cookie = f"{session_cookie_name(app.state.settings)}={'p' * 43}"
    headers = {"Cookie": cookie, "Origin": PROBE_ORIGIN, "Accept-Language": "de"}
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url=PROBE_ORIGIN, headers=headers
    ) as client:
        yield client


async def _passes(
    app: Any,
    counter: SqlCounter,
    repeat: int,
    profile: Sequence[str] = (),
    digest: bool = False,
) -> list[Pass]:
    """`repeat` passes; the last one (a warm one when repeat >= 2) profiles the requests
    whose route contains one of `profile`, and digests every body when `digest`."""
    from hausbuch.services.documents import LOCAL_TIME_ZONE

    labels = _Labels()
    passes = []
    async with probe_client(app) as client:
        for number in range(1, repeat + 1):
            today = datetime.now(LOCAL_TIME_ZONE).date()
            last = number == repeat
            run = PageRun(client, counter, today, labels, profile if last else (), digest and last)
            passes.append(await run.run())
    return passes


def run_probe(
    engine: Engine,
    workspace_id: UUID,
    *,
    repeat: int,
    profile: Sequence[str] = (),
    digest: bool = False,
    out: Callable[[str], None] = print,
) -> list[Pass]:
    """Time the page `repeat` times on `engine` (read-only: read_only_engine or
    make_read_only, checked here) and print the report. `profile`: route substrings whose
    requests of the last pass run under cProfile (needs repeat >= 2 to be a warm pass).
    `digest`: the last pass prints each body's digest (body_digest).
    The engine is the caller's to dispose."""
    if profile and repeat < 2:
        raise ProbeError("--profile needs --repeat 2 or more: it profiles the warm pass")
    assert_read_only(engine)
    counter = SqlCounter(engine)
    connect_ms = open_pool(engine)
    rtt = round_trip_ms(engine)
    empty_tx = empty_transaction_ms(engine, workspace_id)
    member = pick_member(engine, workspace_id)
    with tempfile.TemporaryDirectory(prefix="hausbuch-probe-") as blobs, _quiet_logging():
        app = build_app(engine, member, Path(blobs) / "blobs")
        counter.reset()
        passes = asyncio.run(_passes(app, counter, repeat, profile, digest))
    report(passes, rtt=rtt, empty_tx=empty_tx, connect_ms=connect_ms, member=member, out=out)
    return passes


# --- The report -----------------------------------------------------------------------


def _line(t: Timing, width: int) -> str:
    status = f"{t.status}" + (f" ({t.error})" if t.error else "")
    return (
        f"{t.label:<{width}}  {status:<6}  {t.ms:9.1f}  {t.statements:5d}  {t.sql_ms:9.1f}"
        f"  {t.transactions:3d}  {t.size:9d}"
        + (f"  {t.digest}" if t.digest else "")
        + ("  profiled" if t.profiled else "")
    )


def report(
    passes: Sequence[Pass],
    *,
    rtt: float,
    empty_tx: float,
    connect_ms: float,
    member: Member,
    out: Callable[[str], None] = print,
) -> None:
    out(f"db round-trip (median of {ROUND_TRIPS} x SELECT 1): {rtt:.1f} ms")
    out(
        f"empty workspace transaction (median of {ROUND_TRIPS}: checkout with pre-ping, BEGIN, "
        f"SET, COMMIT): {empty_tx:.1f} ms, so {_hidden(rtt, empty_tx):.1f} ms per transaction "
        "beyond its counted SET and COMMIT"
    )
    out(f"opening {POOL_SIZE} pooled connections (paid once in production): {connect_ms:.0f} ms")
    out(
        "session: the real per-request lookup is timed in every line; the last_seen_at write "
        "(at most every 5 minutes) is not"
    )
    if not member.has_session:
        out("note: the workspace has no session row, so /api/auth/me cannot answer here")
    width = max((len(t.label) for p in passes for t in p.timings), default=20)
    header = (
        f"{'path':<{width}}  {'status':<6}  {'ms':>9}  {'stmts':>5}  {'sql_ms':>9}"
        f"  {'tx':>3}  {'bytes':>9}"
    )
    for number, one in enumerate(passes, start=1):
        kind = "first (in-process warm-up, not a container cold start)" if number == 1 else "warm"
        out("")
        out(f"pass {number} of {len(passes)}: {kind}")
        digested = any(t.digest for t in one.timings)
        out(header + (f"  {'digest':<16}" if digested else ""))
        for timing in one.timings:
            out(_line(timing, width))
        _summary(one, rtt, empty_tx, out)
    if passes:
        timings = passes[-1].timings
        out("")
        out(f"requests the page fires: {len(timings)}, distinct: {len({t.label for t in timings})}")
        refused = sorted({t.label for p in passes for t in p.timings if t.status == WRITE_REFUSED})
        out(f"write-refused paths: {', '.join(refused) if refused else 'none'}")
    for one in passes:
        for label, stats in one.profiles:
            _profile_report(label, stats, out)


def _hidden(rtt: float, empty_tx: float) -> float:
    """A transaction's trips the counter does not see (pre-ping, BEGIN): an empty one less
    its SET and COMMIT."""
    return max(0.0, empty_tx - 2 * rtt)


def _summary(one: Pass, rtt: float, empty_tx: float, out: Callable[[str], None]) -> None:
    total = sum(t.ms for t in one.timings)
    sql_ms = sum(t.sql_ms for t in one.timings)
    statements = sum(t.statements for t in one.timings)
    transactions = sum(t.transactions for t in one.timings)
    trips = statements + transactions
    hidden = transactions * _hidden(rtt, empty_tx)
    out(
        f"total: {total:.0f} ms over {len(one.timings)} requests, {statements} statements "
        f"({sql_ms:.0f} ms in SQL), {transactions} commits/rollbacks; about "
        f"{trips * rtt + hidden:.0f} ms of it is network latency ({trips} x {rtt:.1f} ms "
        f"counted trips + {transactions} x {_hidden(rtt, empty_tx):.1f} ms per transaction)"
    )
    out(f"slowest {SLOWEST}:")
    for timing in sorted(one.timings, key=lambda t: t.ms, reverse=True)[:SLOWEST]:
        out(f"  {timing.ms:9.1f} ms  {timing.statements:5d} stmts  {timing.label}")


# --- The profile ----------------------------------------------------------------------

_PYTHON_LIB = re.compile(r"/python3\.\d+/")


def _module(filename: str) -> str:
    """A code file as a dotted module name: hausbuch's from its package, a library's from
    site-packages, the standard library's from its lib folder; `~` for a built-in."""
    if filename == "~":
        return "~"
    path = filename.replace("\\", "/")
    if "/hausbuch/" in path:
        path = "hausbuch/" + path.rsplit("/hausbuch/", 1)[1]
    elif "/site-packages/" in path:
        path = path.rsplit("/site-packages/", 1)[1]
    elif _PYTHON_LIB.search(path):
        path = _PYTHON_LIB.split(path)[-1]
    else:
        path = path.rsplit("/", 1)[-1]
    path = path.removesuffix(".py").removesuffix("/__init__")
    return path.replace("/", ".")


def _where(key: tuple[str, int, str]) -> str:
    filename, line, name = key
    return f"{_module(filename)}:{name}:{line}"


def _calls(primitive: int, total: int) -> str:
    return str(total) if primitive == total else f"{total}/{primitive}"


def _watched(key: tuple[str, int, str]) -> bool:
    """A named function of a watched module (not a generator expression or lambda inside it,
    which the two top lists still show)."""
    module, name = _module(key[0]), key[2]
    if name.startswith("<"):
        return False
    return module in WATCHED_MODULES or (module, name) in WATCHED_FUNCTIONS


def _profile_report(label: str, stats: pstats.Stats, out: Callable[[str], None]) -> None:
    """Per profiled request: code locations and numbers only, never an argument or value."""
    rows = stats.stats  # type: ignore[attr-defined]  # {key: (cc, nc, tt, ct, callers)}
    total = getattr(stats, "total_tt", 0.0) * 1000
    out("")
    out(f"profile of {label} (last pass; slower than its timing line would be unprofiled)")
    out(f"profiled time {total:.0f} ms in {len(rows)} functions")
    header = f"{'ncalls':>12}  {'tottime_ms':>10}  {'cumtime_ms':>10}  module:function:line"
    for title, index, count in (
        ("by cumulative time", 3, TOP_CUMULATIVE),
        ("by own time", 2, TOP_OWN),
    ):
        out(f"top {count} {title}:")
        out(header)
        ranked = sorted(rows.items(), key=lambda item: item[1][index], reverse=True)
        for key, (cc, nc, tt, ct, _callers) in ranked[:count]:
            out(f"{_calls(cc, nc):>12}  {tt * 1000:10.1f}  {ct * 1000:10.1f}  {_where(key)}")
    watched = [(key, row) for key, row in rows.items() if _watched(key)]
    out("net worth, valuation, prices, holdings and household month: calls")
    if not watched:
        out("  none of them ran")
        return
    watched.sort(key=lambda item: (-item[1][1], _where(item[0])))
    out(f"{'ncalls':>12}  {'cumtime_ms':>10}  module:function:line")
    for key, (cc, nc, _tt, ct, _callers) in watched[:WATCHED_SHOWN]:
        out(f"{_calls(cc, nc):>12}  {ct * 1000:10.1f}  {_where(key)}")
    if len(watched) > WATCHED_SHOWN:
        out(f"  … {len(watched) - WATCHED_SHOWN} more")
