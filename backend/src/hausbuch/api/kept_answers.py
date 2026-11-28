"""Kept answers (phase 10 §7.4): serve a computed read again while nothing has changed.

Übersicht and Zu prüfen recompute every figure on each load. This middleware keeps the
answer of a few GET routes (KEPT_ROUTES) in the API's memory and sends the same bytes again
while the key it was computed under still holds. The API runs as one instance (min = max
scale 1), so one in-process store is the whole cache; a restart (every deploy) empties it.

The key is everything an answer is computed from that is not the request path:
- the path and the query (its parameters sorted, so their order never matters);
- the workspace and the member (user id): every member has their own answers;
- the workspace's data version (migration 0050), which every committed write moves;
- the day in Berlin and the day in UTC (a route that reads "today" reads one of them; the
  clock audit in the phase 10 spec §7.4 found no kept route reading the time of day);
- the language of the request (phase 10 D284: server texts are translated);
- the app's version and the tax and plan rules' version (`rule_version()`); both are fixed
  for a process, so they only guard against a cache outliving a change in place.

What the store keeps, and when:
- only a 200 answer, never an error, never one that sets a cookie;
- only when the data version and both days read after the answer equal those read before it
  (a commit, or midnight, during the computation discards the answer: it may hold data of
  either side);
- the exact bytes and headers the route sent; a hit sends them again unchanged;
- at most `max_bytes` (32 MB), the least recently used dropped first; an answer bigger than a
  quarter of that is not kept. Storing an answer drops the answers of its workspace kept
  under an older version, which can never be served again;
- until its expiry, when the route named one (`keep_until`, §7.5 (b)): a route whose answer
  reads the clock finer than the day names the first moment that answer may change with no
  write in between, and a kept answer is served only before it (by the store's clock). Only
  GET /inbox and /inbox/lines name one; every other kept answer has none.

The session is still resolved on every request, a hit included (the resolver's lookup and its
idle slide): a revoked or expired session gets its 401 from the route as before, never a kept
answer. The middleware resolves it once and hands it to the route's `current_auth` through
the ASGI scope (AUTH_SCOPE_KEY), so a miss does not resolve it twice. Only server code can put
a key into the scope; a client cannot.

Not kept, on purpose (the clock and input audit, phase 10 §7.4): `/api/auth/me` and
`/api/auth/members` (they read the session and the passkeys, which the data version does not
cover: `auth_session`, `auth_passkey`), and every route not listed. Adding a route to
KEPT_ROUTES needs the same audit: every input it reads must be in the key or covered by the
data version, or, for a clock read finer than the day, its expiry.

§7.5 (b) keeps `/api/inbox` and `/api/inbox/lines` until their expiry: the email-in cards
(a 24-hour card, a 30-day list) and the price cards (a 31-day streak window) name the moment
their oldest row leaves its window (services/timed_items.py), the wallet cards are dated by
stored times only, every other section reads at most the Berlin date, and the settings they
read are the process's (fixed while the store lives) or typed values in `app_secret`, which
the data version covers.

§7.5 (a) applied that audit to every other GET route and kept those that read only the
workspace's rows, the day and the code. Left out, besides the above: `/api/events` and the
property History routes (they show the sign-in and passkey events, which since migration 0051
move no version, and the sign-out events, which since migration 0055 move none either); every
route that reads
the clock finer than the day (`/api/inbound-mail`, signed URLs: `/api/exports/holdings`,
`/api/tax-exports/{id}`, which also reads the job queue), settings or typed secrets
(`/api/app-secrets`, `/api/bank-credential-slots`, `/api/inbound-mail/address`,
`/api/workspace/settings`, `/api/accounts/{id}/bank-connection`, `/api/wallets/{id}/state`) or
the blob store (`/api/documents/{id}`, `/api/groups/{id}/change`); every download (`/file`,
`/original`, `/flat-rate-letter`); a route with a side effect (`.../plain`, which writes an
event); and the sync progress routes (`/api/syncs/{id}`, `.../syncs`). `/api/properties/{id}`
and `/api/tenants/{id}` passed the audit but are not listed (tests/api/test_kept_answers.py
and tests/api/test_no_echo.py pin them as unkept).
tests/api/test_kept_answers.py also requests every kept route and fails if one reads the
session or passkey tables.

The warm step (jobs/kept_answers.py) asks the page's routes once per enrolled member after the
morning jobs, through `internal_get`, which runs the whole ASGI app as a request would.

§7.5 (c): the store also records, per member, the paths (with their query) that member's own
requests found kept or stored (never an internal request's, never an answer the store did not
keep), at most MAX_RECORDED_PATHS per member and for RECORDED_FOR, and counts the requests in
flight. api/kept_refresh.py asks those paths again once a write has been followed by a quiet
period, pausing while a request runs. The
checks (`check`, `compare_all`) recompute a kept answer with the store bypassed and compare
the bytes; a difference empties the store and is logged as an error.
"""

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import parse_qsl, urlencode
from uuid import UUID

from sqlalchemy import Engine, text
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hausbuch import i18n
from hausbuch.db.session import workspace_transaction
from hausbuch.services import plan_inputs

from .auth_context import AuthContext, NotAuthenticatedError, current_auth

logger = logging.getLogger("hausbuch.api.kept_answers")

# Server-only scope keys: the session the middleware resolved, and "recompute, do not use or
# keep a stored answer" for the checks.
AUTH_SCOPE_KEY = "hausbuch.auth"
BYPASS_SCOPE_KEY = "hausbuch.kept_bypass"
ROUTE_SCOPE_KEY = "hausbuch.kept_route"
# Set by internal_get: the warm step, the checks and the refresh, never a person's request.
INTERNAL_SCOPE_KEY = "hausbuch.kept_internal"
# Set by a route (`keep_until`) in the request state: when its answer may change (§7.5 (b)).
EXPIRES_STATE_KEY = "kept_expires_at"

MAX_BYTES = 32 * 1024 * 1024
# §7.5 (c): what a member's own requests found kept, asked again after a change.
MAX_RECORDED_PATHS = 200  # per member, the least recently asked dropped first
MAX_RECORDED_MEMBERS = 50
RECORDED_FOR = timedelta(days=3)

# The GET routes whose answers are kept: what Übersicht and Zu prüfen load (perf_probe.PageRun,
# components/inbox.tsx loadInbox), less the routes the audit excluded, and since §7.5 (a) every
# other section's reads that the same audit passed (module docstring). A literal path comes
# before a template its segment would also match (`auto-rate` before `{account_id}`), so it
# is named by its own template.
KEPT_ROUTES: tuple[str, ...] = (
    # Übersicht and Zu prüfen (§7.4)
    "/api/inbox",  # until its expiry (§7.5 (b))
    "/api/inbox/lines",  # until its expiry (§7.5 (b))
    "/api/properties",
    "/api/properties/equity",
    "/api/properties/payback",
    "/api/properties/{property_id}/units",
    "/api/properties/{property_id}/loans",
    "/api/properties/{property_id}/checklist",
    "/api/properties/{property_id}/flat-rate-check",
    "/api/properties/{property_id}/anlage-v",
    "/api/units/{unit_id}/tenancies",
    "/api/tenants",
    "/api/owners",
    "/api/owners/{owner_id}/anlage-n",
    "/api/tax/return-estimate",
    "/api/reminders",
    "/api/net-worth/overview",
    "/api/health",
    "/api/health/months",
    "/api/health/net-worth-series",
    "/api/household/cashflow",
    "/api/household-scenarios",
    "/api/accounts",
    "/api/accounts/{account_id}/imports",
    "/api/accounts/{account_id}/transactions",
    "/api/accounts/{account_id}/movements",
    "/api/possible-duplicates",
    "/api/documents",
    # Every other section (§7.5 (a))
    "/api/accounts/auto-rate",
    "/api/accounts/{account_id}",
    "/api/accounts/{account_id}/ownership",
    "/api/accounts/{account_id}/month",
    "/api/accounts/{account_id}/bank-statements",
    "/api/accounts/{account_id}/exchange",
    "/api/imports/{import_id}",
    "/api/transactions/{transaction_id}/allocations",
    "/api/transfers",
    "/api/transfers/open",
    "/api/net-worth",
    "/api/net-worth/snapshots",
    "/api/net-worth/snapshots/{run_id}",
    "/api/assets",
    "/api/assets/overview",
    "/api/assets/performance",
    "/api/assets/etf",
    "/api/holdings/crypto",
    "/api/wallets/holdings",
    "/api/wallets/{account_id}",
    "/api/wallets/{account_id}/lots",
    "/api/wallets/{account_id}/groups",
    "/api/wallets/{account_id}/answered",
    "/api/wallets/{account_id}/hidden-tokens",
    "/api/household-plan",
    "/api/household-plan/comparison",
    "/api/household-plan/explanation",
    "/api/household-budget",
    # Phase 10 §7.9 (d): the default scenario's goal over the plan's own reads (the rows and
    # the Berlin day only)
    "/api/household-goal",
    # Phase 10 §7.11 (h): „Euer Plan“, the goal's reads plus the accounts' and properties'
    # rows and the running month's lines; the switches are its query
    "/api/household-goal/plan",
    # Phase 10 §7.9 (e): „Für euer Ziel“, the goal's reads plus /assets/overview (on the day
    # chosen and today) and /assets/performance, all kept above; the rows and the Berlin day
    "/api/household-goal/tracking",
    # Phase 10 §7.9 (b), (c): the confirmed private lines and the spending marks (migration
    # 0056, its data-version triggers) and the Berlin day only
    "/api/household/recurring",
    "/api/owners/{owner_id}",
    "/api/owners/{owner_id}/anlage-n/exports",
    "/api/income-inputs",
    "/api/tax-rate",
    "/api/return-inputs",
    "/api/return-inputs/{return_input_id}/evidence",
    "/api/tax/anlage-n/years",
    "/api/tax/questionnaire",
    # Phase 10 D290: composes kept reads only (checklists, drafts, estimate, questionnaire)
    "/api/tax/{tax_year}/guide",
    "/api/properties/{property_id}/ownership",
    "/api/properties/{property_id}/acquisition-costs",
    "/api/properties/{property_id}/price-splits",
    "/api/properties/{property_id}/purchase-summary",
    "/api/properties/{property_id}/documents",
    "/api/properties/{property_id}/tax-inputs",
    "/api/properties/{property_id}/anlage-v/sources",
    "/api/properties/{property_id}/anlage-v/exports",
    "/api/properties/{property_id}/performance/actuals",
    "/api/properties/{property_id}/performance/projection",
    "/api/properties/{property_id}/scenarios",
    "/api/units/{unit_id}",
    "/api/units/{unit_id}/documents",
    "/api/tenants/{tenant_id}/documents",
    "/api/tenants/{tenant_id}/tenancies",
    "/api/tenancies/{tenancy_id}",
    "/api/tenancies/{tenancy_id}/deposits",
    "/api/tenancies/{tenancy_id}/rent-schedule",
    "/api/tenancies/{tenancy_id}/rent",
    "/api/tenancies/{tenancy_id}/documents",
    "/api/loans/{loan_id}",
    "/api/loans/{loan_id}/schedule",
    "/api/loans/{loan_id}/summary",
    "/api/loans/{loan_id}/documents",
    "/api/documents/{document_id}/links",
    "/api/documents/{document_id}/retention",
    "/api/documents/{document_id}/proposals",
    "/api/proposals/{proposal_id}",
)

_PARAM = re.compile(r"\{[a-z_]+\}")


def _pattern(template: str) -> re.Pattern[str]:
    parts = _PARAM.split(template)
    return re.compile("^" + "[^/]+".join(re.escape(part) for part in parts) + "$")


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (template, _pattern(template)) for template in KEPT_ROUTES
)


def keep_until(request: Request, moment: datetime | None) -> None:
    """A kept route whose answer reads the clock finer than the day names the first moment
    its answer may change with no write in between (§7.5 (b)); None: time never changes it."""
    if moment is not None:
        setattr(request.state, EXPIRES_STATE_KEY, moment)


def kept_route(path: str) -> str | None:
    """The KEPT_ROUTES template `path` matches, or None."""
    for template, pattern in _PATTERNS:
        if pattern.match(path):
            return template
    return None


def _app_version() -> str:
    try:
        return version("hausbuch")
    except PackageNotFoundError:  # pragma: no cover - the package is always installed
        return "unknown"


def _rule_version() -> str:
    from hausbuch.services.health import rule_version

    return rule_version()


def read_data_version(engine: Engine, workspace_id: UUID) -> int | None:
    """The workspace's data version (migration 0050), or None when it has no row yet."""
    with workspace_transaction(engine, workspace_id) as conn:
        return conn.execute(
            text("SELECT version FROM workspace_data_version WHERE workspace_id = :ws"),
            {"ws": workspace_id},
        ).scalar_one_or_none()


@dataclass(frozen=True)
class Stamp:
    """What a kept answer depends on beyond its request: read before and after computing."""

    version: int
    berlin_day: date
    utc_day: date


@dataclass(frozen=True)
class Key:
    path: str
    query: tuple[tuple[str, str], ...]
    workspace_id: UUID
    user_id: UUID
    stamp: Stamp
    locale: str
    app_version: str
    rule_version: str


@dataclass
class Entry:
    template: str
    auth: AuthContext
    status: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes
    # The first moment the answer may change with no write in between (keep_until, §7.5 (b));
    # None for an answer that reads the clock at most to the day, which the key holds.
    expires_at: datetime | None = None

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    @property
    def size(self) -> int:
        return len(self.body) + sum(len(n) + len(v) for n, v in self.headers)


@dataclass
class Recorded:
    """One path a member's own request found kept or stored (§7.5 (c))."""

    template: str
    locale: str
    seen_at: datetime


@dataclass
class RecordedMember:
    auth: AuthContext
    # (path, canonical query) → Recorded, the least recently asked first.
    paths: OrderedDict[tuple[str, tuple[tuple[str, str], ...]], Recorded]


@dataclass
class Counters:
    hits: int = 0
    misses: int = 0
    stored: int = 0
    discarded: int = 0  # computed, but the version or the day moved meanwhile
    mismatches: int = 0


@dataclass
class Response:
    status: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes
    kept: str | None = None  # the middleware's outcome: hit, stored, discarded, miss, bypass
    # What the route itself sent, as the middleware saw it (inside the request log, which
    # adds its own headers): what a kept answer is compared with.
    inner: tuple[int, tuple[tuple[bytes, bytes], ...], bytes] | None = None


@dataclass
class CheckOutcome:
    template: str
    path: str
    result: str  # same, different, moved (version, day or expiry passed), failed


def canonical_query(query_string: bytes) -> tuple[tuple[str, str], ...]:
    # By name only, and stable: FastAPI takes the last of a repeated parameter, so the
    # order of one name's values is part of the answer while the order of names is not.
    pairs = parse_qsl(query_string.decode("latin-1"), keep_blank_values=True)
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


class KeptAnswers:
    """The store and what it needs: the engine for the data version, the clock, and (once
    create_app attached it) the ASGI app for the warm step and the checks."""

    def __init__(
        self,
        *,
        max_bytes: int = MAX_BYTES,
        clock: Callable[[], datetime] | None = None,
        enabled: bool = True,
    ) -> None:
        self.max_bytes = max_bytes
        self.clock = clock or (lambda: datetime.now(UTC))
        self.enabled = enabled
        self.counters = Counters()
        self.app_version = _app_version()
        self.rule_version = _rule_version()
        self._entries: OrderedDict[Key, Entry] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._locales: dict[UUID, str] = {}
        self._recorded: OrderedDict[tuple[UUID, UUID], RecordedMember] = OrderedDict()
        self.in_flight = 0  # a person's requests running now (§7.5 (c))
        self.internal_in_flight = 0  # internal_get requests running now
        # time.monotonic() when a person's request last ended, so the refresh waits for a
        # continuous idle time, not only for no request running (§7.5 (c) review fix C2).
        self.person_ended_at: float | None = None
        self.app: ASGIApp | None = None
        self.engine: Engine | None = None

    def attach(self, app: Any) -> None:
        """Called by create_app: the whole app (for internal requests) and its engine."""
        self.app = app
        self.engine = app.state.engine

    # --- The key ----------------------------------------------------------------------

    def stamp(self, workspace_id: UUID) -> Stamp | None:
        """The data version and both days now (blocking: one short transaction)."""
        assert self.engine is not None
        current = read_data_version(self.engine, workspace_id)
        if current is None:
            return None
        from hausbuch.services.documents import LOCAL_TIME_ZONE

        now = self.clock()
        return Stamp(current, now.astimezone(LOCAL_TIME_ZONE).date(), now.astimezone(UTC).date())

    def key(self, scope: Scope, auth: AuthContext, stamp: Stamp) -> Key:
        return Key(
            path=scope["path"],
            query=canonical_query(scope.get("query_string", b"")),
            workspace_id=auth.workspace_id,
            user_id=auth.user_id,
            stamp=stamp,
            locale=i18n.current(),
            app_version=self.app_version,
            rule_version=self.rule_version,
        )

    # --- The store --------------------------------------------------------------------

    def get(self, key: Key) -> Entry | None:
        """The kept answer under `key`; an expired one is dropped and never returned."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expired(self.clock()):
                self._bytes -= self._entries.pop(key).size
                return None
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def is_kept(self, key: Key) -> bool:
        """Whether an unexpired answer is kept under `key`; touches nothing."""
        with self._lock:
            entry = self._entries.get(key)
            return entry is not None and not entry.expired(self.clock())

    def put(self, key: Key, entry: Entry) -> bool:
        size = entry.size
        if size > self.max_bytes // 4:
            return False
        with self._lock:
            stale = [
                k
                for k in self._entries
                if k.workspace_id == key.workspace_id and k.stamp.version < key.stamp.version
            ]
            for k in stale:
                self._bytes -= self._entries.pop(k).size
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous.size
            self._entries[key] = entry
            self._bytes += size
            while self._bytes > self.max_bytes:
                _, dropped = self._entries.popitem(last=False)
                self._bytes -= dropped.size
        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)

    def items(self) -> list[tuple[Key, Entry]]:
        with self._lock:
            return list(self._entries.items())

    # --- What each member asked (§7.5 (c)) ---------------------------------------------

    def record(self, key: Key, template: str, auth: AuthContext) -> None:
        """A member's own request found `key` kept or stored it."""
        member = (key.workspace_id, key.user_id)
        with self._lock:
            recorded = self._recorded.pop(member, None) or RecordedMember(auth, OrderedDict())
            recorded.auth = auth
            self._recorded[member] = recorded
            path = (key.path, key.query)
            recorded.paths.pop(path, None)
            recorded.paths[path] = Recorded(template, key.locale, self.clock())
            while len(recorded.paths) > MAX_RECORDED_PATHS:
                recorded.paths.popitem(last=False)
            while len(self._recorded) > MAX_RECORDED_MEMBERS:
                self._recorded.popitem(last=False)

    def _prune(self) -> None:
        cutoff = self.clock() - RECORDED_FOR
        for member, recorded in list(self._recorded.items()):
            for path, seen in list(recorded.paths.items()):
                if seen.seen_at < cutoff:
                    del recorded.paths[path]
            if not recorded.paths:
                del self._recorded[member]

    def recorded_workspaces(self) -> set[UUID]:
        with self._lock:
            self._prune()
            return {workspace_id for workspace_id, _ in self._recorded}

    def recorded(
        self, workspace_id: UUID
    ) -> list[tuple[AuthContext, list[tuple[str, tuple[tuple[str, str], ...], Recorded]]]]:
        """Each member of `workspace_id` with their recorded paths, the most recently asked
        first (the page a member is likeliest to open next)."""
        with self._lock:
            self._prune()
            return [
                (
                    recorded.auth,
                    [
                        (path, query, seen)
                        for (path, query), seen in reversed(recorded.paths.items())
                    ],
                )
                for (ws, _), recorded in reversed(self._recorded.items())
                if ws == workspace_id
            ]

    def forget(self, auth: AuthContext, path: str, query: tuple[tuple[str, str], ...]) -> None:
        """A recorded path that no longer answers 200 (its record was deleted)."""
        with self._lock:
            recorded = self._recorded.get((auth.workspace_id, auth.user_id))
            if recorded is not None:
                recorded.paths.pop((path, query), None)

    # --- Requests in flight (§7.5 (c)) -------------------------------------------------

    def _count(self, attribute: str, step: int) -> None:
        with self._lock:
            setattr(self, attribute, getattr(self, attribute) + step)

    def _person_ended(self) -> None:
        with self._lock:
            self.in_flight -= 1
            self.person_ended_at = time.monotonic()

    @property
    def busy(self) -> bool:
        """A person's request or an internal one is running."""
        return self.in_flight > 0 or self.internal_in_flight > 0

    def remember_locale(self, user_id: UUID, locale: str) -> None:
        self._locales[user_id] = locale

    def locale_of(self, user_id: UUID) -> str:
        """The language this member's last kept request asked for (German by default)."""
        return self._locales.get(user_id, i18n.DE)

    # --- Internal requests (the warm step, the checks) ---------------------------------

    async def internal_get(
        self,
        auth: AuthContext,
        path: str,
        query: dict[str, Any] | Sequence[tuple[str, str]] | None = None,
        *,
        locale: str | None = None,
        bypass: bool = False,
    ) -> Response:
        """GET `path` through the whole app as `auth`, as a browser request would run it
        (every middleware, the route, its serialisation), with no cookie: the session is
        handed in through the scope."""
        if self.app is None:
            raise RuntimeError("kept answers are not attached to an app")
        query_string = urlencode(query or {}).encode()
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": query_string,
            "headers": [(b"accept-language", (locale or self.locale_of(auth.user_id)).encode())],
            "client": None,
            "server": None,
            AUTH_SCOPE_KEY: auth,
            BYPASS_SCOPE_KEY: bypass,
            INTERNAL_SCOPE_KEY: True,
        }
        sent = False

        async def receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        status = 0
        headers: tuple[tuple[bytes, bytes], ...] = ()
        body = bytearray()

        async def send(message: Message) -> None:
            nonlocal status, headers
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = tuple(message.get("headers", ()))
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        self._count("internal_in_flight", 1)
        try:
            await self.app(scope, receive, send)
        finally:
            self._count("internal_in_flight", -1)
        state = scope.get("state", {})
        return Response(
            status, headers, bytes(body), state.get("kept_answer"), state.get("kept_inner")
        )

    async def recompute(self, key: Key, entry: Entry) -> CheckOutcome:
        """Compute `key`'s answer again with the store bypassed and compare it with the kept
        bytes; only when the version and both days are the key's before and after, and the
        answer has not expired by then."""
        query = dict(key.query)
        outcome = CheckOutcome(entry.template, key.path, "moved")
        before = await run_in_threadpool(self.stamp, key.workspace_id)
        if before != key.stamp or entry.expired(self.clock()):
            return outcome
        fresh = await self.internal_get(entry.auth, key.path, query, locale=key.locale, bypass=True)
        after = await run_in_threadpool(self.stamp, key.workspace_id)
        if after != key.stamp or entry.expired(self.clock()):
            return outcome
        if fresh.inner is None:
            outcome.result = "failed"
        else:
            status, headers, body = fresh.inner
            if status != entry.status:
                outcome.result = "failed" if status >= 500 else "different"
            else:
                same = body == entry.body and _comparable(headers) == _comparable(entry.headers)
                outcome.result = "same" if same else "different"
        if outcome.result == "different":
            self.counters.mismatches += 1
        return outcome

    async def compare_all(self) -> list[CheckOutcome]:
        """Every kept answer recomputed and compared (perf-overview --check-kept)."""
        return [await self.recompute(key, entry) for key, entry in self.items()]

    async def check(self, day_number: int) -> CheckOutcome | None:
        """The daily check: one kept answer still current, the route rotating by day. On a
        difference the store is emptied and an error logged (the route only). None when no
        kept answer is current."""
        current: list[tuple[Key, Entry]] = []
        stamps: dict[UUID, Stamp | None] = {}
        for key, entry in self.items():
            if entry.expired(self.clock()):
                continue
            if key.workspace_id not in stamps:
                stamps[key.workspace_id] = await run_in_threadpool(self.stamp, key.workspace_id)
            if stamps[key.workspace_id] == key.stamp:
                current.append((key, entry))
        if not current:
            return None
        templates = sorted({entry.template for _, entry in current}, key=KEPT_ROUTES.index)
        template = templates[day_number % len(templates)]
        key, entry = next((k, e) for k, e in current if e.template == template)
        outcome = await self.recompute(key, entry)
        if outcome.result == "different":
            self.clear()
            fields = {"event": "kept_answer_mismatch", "route": template}
            logger.error("a kept answer differed from its recomputation", extra={"fields": fields})
        return outcome


def _comparable(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    return sorted((n.lower(), v) for n, v in headers)


def _resolve(request: Request) -> AuthContext | None:
    try:
        return current_auth(request)
    except NotAuthenticatedError:
        return None


class KeptAnswersMiddleware:
    """The innermost middleware: inside RequestMiddleware (which sets the language and logs
    the request) and the origin check; the route runs inside it."""

    def __init__(self, app: ASGIApp, kept: KeptAnswers) -> None:
        self.app = app
        self.kept = kept

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Every request of a person, whatever its method or route, is counted while it runs,
        so the refresh (§7.5 (c)) waits for it."""
        if scope["type"] != "http" or scope.get(INTERNAL_SCOPE_KEY):
            await self._handle(scope, receive, send)
            return
        self.kept._count("in_flight", 1)
        try:
            await self._handle(scope, receive, send)
        finally:
            self.kept._person_ended()

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        kept = self.kept
        if scope["type"] != "http" or scope["method"] != "GET" or not kept.enabled:
            await self.app(scope, receive, send)
            return
        template = kept_route(scope["path"])
        if template is None:
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        scope[ROUTE_SCOPE_KEY] = template
        auth = scope.get(AUTH_SCOPE_KEY)
        if auth is None:
            auth = await run_in_threadpool(_resolve, Request(scope, receive))
            if auth is None:  # the route answers its 401 itself
                await self.app(scope, receive, send)
                return
            scope[AUTH_SCOPE_KEY] = auth
        state["auth"] = auth
        bypass = bool(scope.get(BYPASS_SCOPE_KEY))
        internal = bool(scope.get(INTERNAL_SCOPE_KEY))
        before = await run_in_threadpool(kept.stamp, auth.workspace_id)
        if before is None:  # no version row yet: nothing can be kept safely
            state["kept_answer"] = "miss"
            await self.app(scope, receive, send)
            return
        key = kept.key(scope, auth, before)
        if not bypass:
            if not internal:  # the refresh asks in a recorded path's language (C1)
                kept.remember_locale(auth.user_id, key.locale)
            entry = kept.get(key)
            if entry is not None:
                kept.counters.hits += 1
                state["kept_answer"] = "hit"
                if not internal:
                    kept.record(key, template, auth)
                await send(
                    {
                        "type": "http.response.start",
                        "status": entry.status,
                        "headers": list(entry.headers),
                    }
                )
                await send({"type": "http.response.body", "body": entry.body})
                return
            kept.counters.misses += 1

        status = 0
        headers: tuple[tuple[bytes, bytes], ...] = ()
        body = bytearray()
        complete = False

        async def capture(message: Message) -> None:
            nonlocal status, headers, complete
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = tuple(message.get("headers", ()))
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
                complete = not message.get("more_body", False)
            await send(message)

        if bypass:  # a check recomputes from the database: no kept plan reads either
            with plan_inputs.bypass():
                await self.app(scope, receive, capture)
        else:
            await self.app(scope, receive, capture)
        if bypass:
            state["kept_answer"] = "bypass"
            state["kept_inner"] = (status, headers, bytes(body))
            return
        sets_cookie = any(name.lower() == b"set-cookie" for name, _ in headers)
        if status != 200 or not complete or sets_cookie:
            state["kept_answer"] = "miss"
            return
        after = await run_in_threadpool(kept.stamp, auth.workspace_id)
        expires_at = state.get(EXPIRES_STATE_KEY)
        if after != before or (expires_at is not None and kept.clock() >= expires_at):
            kept.counters.discarded += 1
            state["kept_answer"] = "discarded"
            return
        entry = Entry(template, auth, status, headers, bytes(body), expires_at)
        stored = kept.put(key, entry)
        if stored:
            kept.counters.stored += 1
            if not internal:
                kept.record(key, template, auth)
        state["kept_answer"] = "stored" if stored else "miss"


@dataclass
class WarmResult:
    members: int = 0
    requests: int = 0
    failed: int = 0
    paths: list[str] = field(default_factory=list)


def last_full_month(today: date) -> str:
    """The month before today's, as the page asks for it (components/cashflow.tsx)."""
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{year:04d}-{month:02d}"


async def warm_member(
    kept: KeptAnswers,
    auth: AuthContext,
    today: date,
    *,
    before: Callable[[], Awaitable[None]] | None = None,
) -> WarmResult:
    """Ask the kept routes of Übersicht and Zu prüfen once as `auth`, in the order and with
    the parameters the pages use, so their answers are kept. A failed route is counted and
    logged by its template only; the others still run. `before`, when given, is awaited right
    before each route, with nothing awaited between it and the request."""
    result = WarmResult(members=1)
    month = last_full_month(today)
    year = today.year

    async def get(path: str, query: dict[str, Any] | None = None) -> Any:
        if before is not None:
            await before()
        result.requests += 1
        try:
            response = await kept.internal_get(auth, path, query)
        except Exception as exc:  # noqa: BLE001 - class name only
            response = None
            exc_type = type(exc).__name__
        else:
            exc_type = None
        if response is None or response.status != 200:
            result.failed += 1
            fields = {
                "event": "kept_answer_warm_failed",
                "route": kept_route(path),
                "status": None if response is None else response.status,
                "exc_type": exc_type,
            }
            logger.warning("a kept answer was not warmed", extra={"fields": fields})
            return None
        result.paths.append(path)
        import json

        try:
            return json.loads(response.body)
        except ValueError:
            return None

    properties = await get("/api/properties") or []
    await get("/api/tenants")
    await get("/api/reminders")
    await get("/api/net-worth/overview")
    await get("/api/health")
    await get("/api/household/cashflow", {"month": month})
    await get("/api/health/months", {"from": month, "to": month})
    await get("/api/properties/equity")
    await get("/api/properties/payback")
    owners = await get("/api/owners") or []
    await get("/api/tax/return-estimate", {"tax_year": year})
    await get("/api/health/net-worth-series")
    await get("/api/household-scenarios", {"include_history": "true"})
    accounts = await get("/api/accounts") or []
    await get("/api/inbox")  # every page's menu count; Zu prüfen's list (§7.5 (b))
    await get("/api/inbox/lines")
    await get("/api/possible-duplicates")
    await get("/api/documents")
    for prop in properties:
        base = f"/api/properties/{prop['id']}"
        units = await get(f"{base}/units") or []
        await get(f"{base}/loans")
        for unit in units:
            await get(f"/api/units/{unit['id']}/tenancies")
        await get(f"{base}/flat-rate-check")
        checklist = await get(f"{base}/checklist")
        if checklist is not None:
            await get(f"{base}/anlage-v", {"tax_year": checklist["tax_year"]})
    for owner in owners:
        await get(f"/api/owners/{owner['id']}/anlage-n", {"tax_year": year})
    for account in accounts:
        if account.get("kind") in ("exchange", "wallet"):
            continue
        await get(f"/api/accounts/{account['id']}/imports")
        await get(f"/api/accounts/{account['id']}/transactions")
    return result
