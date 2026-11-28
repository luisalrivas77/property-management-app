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
morning jobs, through `internal_get`, which runs the whole ASGI app as a request would. Since
§7.13 (e) 1 it asks every kept route of Übersicht, Haushalt, Anlagen and Konten with the
frontend's own query strings (WARMED); every other kept route is in NOT_WARMED with its
reason, and a test fails when a kept route is in neither. §7.13 (e) 2: a person's miss within
a day of an answer stored for the same path logs once which part of the key differed
(`explain_miss`), by part name, with values only for a version, a day or a language.

§7.5 (c): the store also records, per member, the paths (with their query) that member's own
requests found kept or stored (never an internal request's, never an answer the store did not
keep), at most MAX_RECORDED_PATHS per member and for RECORDED_FOR, and counts the requests in
flight. api/kept_refresh.py asks those paths again once a write has been followed by a quiet
period, pausing while a request runs. The
checks (`check`, `compare_all`) recompute a kept answer with the store bypassed and compare
the bytes; a difference empties the store and is logged as an error.
"""

import json
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
# §7.13 (e) 2: a person's miss within this long of an answer stored for the same path is
# explained once in the log (which part of the key differed); at most this many stored keys
# are remembered for it, the oldest dropped first.
EXPLAIN_MISS_WITHIN = timedelta(days=1)
MAX_STORED_KEYS = 4000

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
    # Phase 10 §7.9 (e) items 1, 2: the property cards, the goal's reads plus each default
    # property scenario's draft facts and loans (rows) and the committed house price index
    "/api/household-goal/properties",
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


@dataclass
class StoredKey:
    """The last key an answer was stored under for one member and path (§7.13 (e) 2); kept
    beside the store, so it outlives the answer `put` drops under an older version."""

    key: Key
    stored_at: datetime
    explained: bool = False


def key_difference(stored: Key, asked: Key) -> dict[str, Any]:
    """The parts of `asked` that differ from `stored`, for the log: each part by name, with
    its two values only where they are a version, a day or a language. Never an id, a path or
    a query value: a differing query is named by its parameter names alone."""
    parts: dict[str, Any] = {}
    if stored.path != asked.path:
        parts["path"] = True
    if stored.query != asked.query:
        was, now = dict(stored.query), dict(asked.query)
        parts["query"] = sorted(n for n in was.keys() | now.keys() if was.get(n) != now.get(n))
    for name in ("version", "berlin_day", "utc_day"):
        was_value, now_value = getattr(stored.stamp, name), getattr(asked.stamp, name)
        if was_value != now_value:
            parts[name] = [str(was_value), str(now_value)]
    for name in ("locale", "app_version", "rule_version"):
        if getattr(stored, name) != getattr(asked, name):
            parts[name] = [getattr(stored, name), getattr(asked, name)]
    return parts


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
        # §7.13 (e) 2: per member, path and query (and per member and path), the last key an
        # answer was stored under, to explain a person's miss.
        self._stored_keys: OrderedDict[tuple[Any, ...], StoredKey] = OrderedDict()
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
            member = (key.workspace_id, key.user_id, key.path)
            for side in ((*member, key.query), member):
                self._stored_keys.pop(side, None)
                self._stored_keys[side] = StoredKey(key, self.clock())
            while len(self._stored_keys) > MAX_STORED_KEYS:
                self._stored_keys.popitem(last=False)
        return True

    def explain_miss(self, key: Key, template: str) -> dict[str, Any] | None:
        """§7.13 (e) 2: a person's request missed. When an answer was stored for the same
        member and path within EXPLAIN_MISS_WITHIN (the same query's first, else the path's
        latest), log once which part of the key differed; a stored key is explained at most
        once. Returns the logged parts (None when nothing was logged)."""
        member = (key.workspace_id, key.user_id, key.path)
        with self._lock:
            stored = self._stored_keys.get((*member, key.query)) or self._stored_keys.get(member)
            if stored is None or stored.explained:
                return None  # the clock is read only below: nothing else may move it
            now = self.clock()
            if now - stored.stored_at > EXPLAIN_MISS_WITHIN:
                return None
            if stored.key == key:  # dropped as least recently used, or expired: no key part
                parts: dict[str, Any] = {"dropped": True}
            else:
                parts = key_difference(stored.key, key)
            for side in ((*member, stored.key.query), member):
                if (found := self._stored_keys.get(side)) is not None and found.key == stored.key:
                    found.explained = True
        fields = {
            "event": "kept_answer_miss_explained",
            "route": template,
            "parts": sorted(parts),
            "differences": parts,
            "stored_minutes_ago": int((now - stored.stored_at).total_seconds() // 60),
        }
        logger.info("a kept answer missed", extra={"fields": fields})
        return parts

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
            if not internal:
                kept.explain_miss(key, template)

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
    # Answered with a refusal the page expects (EXPECTED_REFUSALS): not kept, not a failure.
    expected: int = 0
    paths: list[str] = field(default_factory=list)
    templates: set[str] = field(default_factory=set)  # every template asked


def last_full_month(today: date) -> str:
    """The month before today's, as the page asks for it (components/cashflow.tsx)."""
    return shift_month(f"{today.year:04d}-{today.month:02d}", -1)


def shift_month(month: str, by: int) -> str:
    """ "2026-01" shifted by -1 → "2025-12" (components/cashflow.tsx shiftMonth)."""
    year, mon = int(month[:4]), int(month[5:7])
    index = year * 12 + (mon - 1) + by
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months_up_to(month: str, count: int) -> list[str]:
    """The `count` months ending at `month`, oldest first (components/cashflow.tsx)."""
    return [shift_month(month, i - count + 1) for i in range(count)]


# Haushalt › Monat shows the twelve months ending at the chosen one (household/page.tsx
# MONTHS_SHOWN); the page opens on the last full month.
HOUSEHOLD_MONTHS_SHOWN = 12

# A refusal a page expects and shows as a to-do of its own, per template: the warm counts it
# as answered, not as a failure. GET /owners/{id}/anlage-n answers 422 anlage_n.no_pay for an
# owner with no pay source in the year, which Übersicht's to-dos read as „Arbeitslohn fehlt“
# (components/overviewTodos.tsx); the query is the page's own (§7.13 (e) 7).
EXPECTED_REFUSALS: dict[str, frozenset[str]] = {
    "/api/owners/{owner_id}/anlage-n": frozenset({"anlage_n.no_pay"}),
}

# The kept routes the warm step does not ask, each with its reason (§7.13 (e) 1). Every
# other KEPT_ROUTES template is asked by `warm_member` as the pages ask it (WARMED);
# tests/api/test_kept_answers.py fails when a kept route is in neither list, or when the warm
# asks a template listed here or misses one it should ask. A route left out is still kept
# once a person asks it, and refreshed after a change (§7.5 (c)).
_NO_SCREEN = "no screen reads it with GET"
_DETAIL = (
    "a detail page opened from one record (Immobilien, Mieter, Darlehen, Dokumente, Steuern), "
    "not one of the pages the warm prepares (Übersicht, Haushalt, Anlagen, Konten); kept on "
    "its first open and refreshed after a change"
)
_PER_LINE = "one per bank line or document, opened by a click; too many to ask ahead"
NOT_WARMED: dict[str, str] = {
    "/api/health/net-worth-series": _NO_SCREEN + " (GET /health carries its series)",
    "/api/accounts/auto-rate": _NO_SCREEN,
    "/api/accounts/{account_id}/ownership": "read by the account's edit form only",
    "/api/accounts/{account_id}/bank-statements": (
        "read by the Bankabruf card after its connection, which is not kept"
    ),
    "/api/imports/{import_id}": _NO_SCREEN,
    "/api/transactions/{transaction_id}/allocations": _PER_LINE,
    "/api/transfers/open": _NO_SCREEN,
    "/api/net-worth": _NO_SCREEN,
    "/api/net-worth/snapshots": _NO_SCREEN + " (the screens POST it)",
    "/api/net-worth/snapshots/{run_id}": _NO_SCREEN,
    "/api/wallets/holdings": _NO_SCREEN,
    "/api/wallets/{account_id}/lots": _NO_SCREEN,
    "/api/owners/{owner_id}": _DETAIL,
    "/api/owners/{owner_id}/anlage-n/exports": _DETAIL,
    "/api/income-inputs": _DETAIL,
    "/api/tax-rate": _DETAIL,
    "/api/return-inputs": _DETAIL,
    "/api/return-inputs/{return_input_id}/evidence": _PER_LINE,
    "/api/tax/anlage-n/years": _DETAIL,
    "/api/tax/questionnaire": _DETAIL,
    "/api/tax/{tax_year}/guide": _DETAIL,
    "/api/properties/{property_id}/ownership": _DETAIL,
    "/api/properties/{property_id}/acquisition-costs": _DETAIL,
    "/api/properties/{property_id}/price-splits": _DETAIL,
    "/api/properties/{property_id}/purchase-summary": _DETAIL,
    "/api/properties/{property_id}/documents": _DETAIL,
    "/api/properties/{property_id}/tax-inputs": _DETAIL,
    "/api/properties/{property_id}/anlage-v/sources": _DETAIL,
    "/api/properties/{property_id}/anlage-v/exports": _DETAIL,
    "/api/properties/{property_id}/performance/actuals": _DETAIL,
    "/api/properties/{property_id}/performance/projection": _DETAIL,
    "/api/properties/{property_id}/scenarios": _DETAIL,
    "/api/units/{unit_id}": _DETAIL,
    "/api/units/{unit_id}/documents": _DETAIL,
    "/api/tenants/{tenant_id}/documents": _DETAIL,
    "/api/tenants/{tenant_id}/tenancies": _DETAIL,
    "/api/tenancies/{tenancy_id}": _DETAIL,
    "/api/tenancies/{tenancy_id}/deposits": _DETAIL,
    "/api/tenancies/{tenancy_id}/rent-schedule": _DETAIL,
    "/api/tenancies/{tenancy_id}/rent": _DETAIL,
    "/api/tenancies/{tenancy_id}/documents": _DETAIL,
    "/api/loans/{loan_id}": _DETAIL,
    "/api/loans/{loan_id}/summary": _DETAIL,
    "/api/loans/{loan_id}/documents": _DETAIL,
    "/api/documents/{document_id}/links": _PER_LINE,
    "/api/documents/{document_id}/retention": _PER_LINE,
    "/api/documents/{document_id}/proposals": _PER_LINE,
    "/api/proposals/{proposal_id}": _PER_LINE,
}
WARMED: tuple[str, ...] = tuple(t for t in KEPT_ROUTES if t not in NOT_WARMED)


def _refusal_codes(body: bytes) -> set[str]:
    try:
        parsed = json.loads(body)
    except ValueError:
        return set()
    if not isinstance(parsed, dict):
        return set()
    codes = [*(parsed.get("codes") or ()), parsed.get("code")]
    return {code for code in codes if isinstance(code, str)}


async def warm_member(
    kept: KeptAnswers,
    auth: AuthContext,
    today: date,
    *,
    before: Callable[[], Awaitable[None]] | None = None,
) -> WarmResult:
    """Ask the kept routes of Übersicht, Haushalt, Anlagen and Konten once as `auth`, in the
    order the pages are likeliest opened and with the parameters the pages send (§7.13 (e) 1:
    the frontend's own query strings), so their answers are kept. A path asked twice is asked
    once. A failed route is counted and logged by its template only; the others still run.
    `before`, when given, is awaited right before each route, with nothing awaited between it
    and the request."""
    result = WarmResult(members=1)
    month = last_full_month(today)  # every page opens on the last full month
    running = f"{today.year:04d}-{today.month:02d}"  # Konten › Umsätze's month card
    year = today.year
    asked: dict[tuple[str, tuple[tuple[str, str], ...]], Any] = {}

    async def get(path: str, query: dict[str, Any] | None = None) -> Any:
        query = query or {}
        template = kept_route(path)
        once = (path, canonical_query(urlencode(query).encode()))
        if once in asked:
            return asked[once]
        if before is not None:
            await before()
        result.requests += 1
        if template is not None:
            result.templates.add(template)
        try:
            response = await kept.internal_get(auth, path, query)
        except Exception as exc:  # noqa: BLE001 - class name only
            response = None
            exc_type = type(exc).__name__
        else:
            exc_type = None
        parsed: Any = None
        expected = EXPECTED_REFUSALS.get(template or "", frozenset())
        if (
            response is not None
            and response.status == 422
            and _refusal_codes(response.body) & expected
        ):
            result.expected += 1
            asked[once] = None
            return None
        if response is None or response.status != 200:
            result.failed += 1
            fields = {
                "event": "kept_answer_warm_failed",
                "route": template,
                "status": None if response is None else response.status,
                "exc_type": exc_type,
            }
            logger.warning("a kept answer was not warmed", extra={"fields": fields})
        else:
            result.paths.append(path)
            try:
                parsed = json.loads(response.body)
            except ValueError:
                parsed = None
        asked[once] = parsed
        return parsed

    # Übersicht (page.tsx): the tiles first, then the to-dos (overviewTodos.tsx).
    await get("/api/net-worth/overview")
    health = await get("/api/health") or {}
    await get("/api/household-goal")
    await get("/api/household/cashflow", {"month": month})
    await get("/api/health/months", {"from": month, "to": month})
    await get("/api/inbox")  # every page's menu count; Zu prüfen's list (§7.5 (b))
    await get("/api/properties/equity")
    await get("/api/properties/payback")
    await get("/api/reminders")
    properties = await get("/api/properties") or []
    await get("/api/household-plan")
    await get("/api/household-goal/tracking")
    owners = await get("/api/owners") or []
    await get("/api/tax/return-estimate", {"tax_year": year})
    for prop in properties:
        base = f"/api/properties/{prop['id']}"
        await get(f"{base}/flat-rate-check")
        checklist = await get(f"{base}/checklist")
        if checklist is not None:
            await get(f"{base}/anlage-v", {"tax_year": checklist["tax_year"]})
    for owner in owners:
        await get(f"/api/owners/{owner['id']}/anlage-n", {"tax_year": year})
    # Zu prüfen (components/inbox.tsx)
    await get("/api/inbox/lines")
    await get("/api/possible-duplicates")
    await get("/api/documents")
    await get("/api/tenants")
    # Haushalt › Monat (household/page.tsx loadYear, healthFigures.tsx, householdRecurring.tsx)
    shown = months_up_to(month, HOUSEHOLD_MONTHS_SHOWN)
    for each in shown:
        await get("/api/household/cashflow", {"month": each})
    await get("/api/transfers", {"month": month})
    await get("/api/health/months", {"from": shown[0], "to": month})
    await get("/api/household-plan/comparison", {"month": month})
    window = [m for m in (health.get("window") or {}).get("months") or [] if isinstance(m, str)]
    for each in window:  # the „Ø“ columns' months, when outside the twelve shown
        await get("/api/household/cashflow", {"month": each})
    await get("/api/household-budget")
    await get("/api/household-scenarios", {"include_history": "true"})
    accounts = await get("/api/accounts") or []
    await get("/api/household/recurring")
    # Haushalt › Budget, Reserve (householdPlan.tsx) and Ziel; „Euer Plan“ (householdPlanPage)
    if window:
        await get("/api/health/months", {"from": window[0], "to": window[-1]})
    await get("/api/household-plan/explanation")
    await get("/api/household-goal/plan", {"view": "own"})
    # Anlagen (assets/page.tsx, Overview, Performance, Crypto, PropertyGrowth)
    await get("/api/assets/overview")
    await get("/api/assets")
    await get("/api/assets/etf")
    await get("/api/assets/performance")
    await get("/api/holdings/crypto")
    await get("/api/household-goal/properties")
    # Konten (accounts/page.tsx): each account's row, the loans; then each account's page
    for prop in properties:
        base = f"/api/properties/{prop['id']}"
        units = await get(f"{base}/units") or []
        loans = await get(f"{base}/loans") or []
        for loan in loans:
            await get(f"/api/loans/{loan['id']}/schedule")
        for unit in units:
            await get(f"/api/units/{unit['id']}/tenancies")
    for account in accounts:
        base = f"/api/accounts/{account['id']}"
        if account.get("kind") == "wallet":  # wallet.tsx, wallet-cards.tsx
            wallet = f"/api/wallets/{account['id']}"
            await get(wallet)
            await get(f"{wallet}/groups")
            await get(f"{wallet}/answered")
            await get(f"{wallet}/hidden-tokens")
            continue
        await get(f"{base}/imports")
        await get(base)
        await get(f"{base}/transactions")
        if account.get("kind") == "exchange":  # exchange.tsx
            await get(f"{base}/exchange")
            await get(f"{base}/movements")
        else:  # Konten › Umsätze (accountLines.tsx MonthCard)
            await get(f"{base}/month", {"month": running})
    return result
