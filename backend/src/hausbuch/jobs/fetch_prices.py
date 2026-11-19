"""The fetch_prices periodic task (docs/phases/phase-6.md D167 (3), D174).

At 07:00 UTC, after the ECB published the previous day's rates (around 16:00 CET), the
worker runs services.prices.fetch for every provisioned workspace: the ECB rates first, then
each held coin (CoinGecko with the Demo key HAUSBUCH_COINGECKO_KEY, Kraken as the fallback and
alone without the key), then each held ETF's NAV from its issuer's file. One run asks each
source once per coin day, pair or fund for all workspaces (services.prices.fetch.Sources).

The CoinGecko key is the workspace's (phase 6 D260): one typed under Einstellungen ›
Zugangsschlüssel wins over the container's. Workspaces with the same key share one CoinGecko
client and its answers (the per-coin-day dedup holds per key); the ECB, Kraken and the funds'
files stay shared by every workspace. A typed key that no longer decrypts prices that
workspace's coins from Kraken alone, never with the container's key, and counts
`coingecko:key_unreadable` in the log line.

No retry: a failed source leaves the price stale and the next day's run asks again; D174's
card appears after seven failed days. lock= keeps one run at a time.

Logged: one line per run with counts, and the failed sources by name, exception class and
HTTP status only (never a URL with a fund or coin, a price, or the key); a workspace that
fails is logged by its exception class.
"""

import dataclasses
import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.app_secrets import AppSecretName
from hausbuch.schemas.enums import ActorType
from hausbuch.services import app_secrets
from hausbuch.services.context import Actor
from hausbuch.services.prices.coingecko import CoinGeckoDaily
from hausbuch.services.prices.fetch import Sources, run_workspace
from hausbuch.services.prices.http import Fetch, urllib_fetch

from .document_read import JobDeps

FETCH_PRICES_TASK = "fetch_prices"
FETCH_PRICES_CRON = "0 7 * * *"  # D167 (3): 07:00, the worker's clock (UTC in the container)
FETCH_PRICES_LOCK = "prices:fetch"
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, FETCH_PRICES_TASK)

logger = logging.getLogger("hausbuch.jobs.fetch_prices")


@dataclass
class FetchResult:
    workspaces: int = 0  # with something held
    failed_workspaces: int = 0
    assets: int = 0
    rows_new: int = 0
    assets_failed: int = 0
    coingecko: bool = False  # a key is set (the container's, or one a workspace typed)
    errors: dict[str, int] = field(default_factory=dict)
    # D174 amended (d), (k): lookups that found no such fund; no failure, no warning
    lookup_misses: dict[str, int] = field(default_factory=dict)


KEY_UNREADABLE = ("coingecko", "key_unreadable", None)  # an errors entry, never the key


def _gecko(key: SecretStr | None, fetch: Fetch, sleep: Any) -> CoinGeckoDaily | None:
    if key is None:
        return None
    coingecko = CoinGeckoDaily(key=key, fetch=fetch)
    if sleep is not None:
        coingecko.sleep = sleep
    return coingecko


def sources_for(deps: JobDeps, fetch: Fetch = urllib_fetch, sleep: Any = None) -> Sources:
    """The run's shared sources with the container's key (no workspace read)."""
    return Sources(fetch=fetch, coingecko=_gecko(deps.settings.coingecko_key, fetch, sleep))


class _PerKey:
    """One Sources per CoinGecko key of the run, all sharing `base`'s ECB, Kraken and fund
    answers and its error counts (a shallow copy keeps the same dicts); only the CoinGecko
    client and its answers are per key. Keys are told apart by a SHA-256 digest, so no key is
    kept as a dict key or compared in clear."""

    def __init__(self, base: Sources, fetch: Fetch, sleep: Any) -> None:
        self.base, self.fetch, self.sleep = base, fetch, sleep
        self.by_digest: dict[str | None, Sources] = {}
        self.with_key = False

    def for_key(self, key: SecretStr | None) -> Sources:
        digest = (
            None if key is None else hashlib.sha256(key.get_secret_value().encode()).hexdigest()
        )
        if digest not in self.by_digest:
            self.by_digest[digest] = dataclasses.replace(
                self.base,
                coingecko=_gecko(key, self.fetch, self.sleep),
                _coingecko={},
                _coingecko_error=None,
            )
        self.with_key = self.with_key or key is not None
        return self.by_digest[digest]

    def for_workspace(self, deps: JobDeps, ws: UUID) -> Sources:
        """The workspace's key: typed in the app, else the container's; unreadable → none."""
        try:
            with workspace_transaction(deps.engine, ws) as conn:
                settings = app_secrets.workspace_settings(
                    conn, deps.settings, [AppSecretName.COINGECKO_KEY]
                )
        except app_secrets.SecretUnreadable:
            self.base.errors[KEY_UNREADABLE] += 1
            return self.for_key(None)
        return self.for_key(settings.coingecko_key)


def run_fetch_prices(
    deps: JobDeps,
    *,
    fetch: Fetch = urllib_fetch,
    sleep: Any = None,
    workspace_ids: Iterable[UUID] | None = None,
) -> FetchResult:
    """One daily run. `fetch`, `sleep` and `workspace_ids` are for tests. Never raises for
    one workspace's failure (logged by class name; the next run asks again)."""
    now = deps.clock()
    sources = Sources(fetch=fetch, coingecko=None)  # the shared answers and error counts
    per_key = _PerKey(sources, fetch, sleep)
    result = FetchResult()
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    for ws in sorted(set(ids)):
        try:
            own = per_key.for_workspace(deps, ws)
            new, out = run_workspace(deps.engine, ws, SYSTEM_ACTOR, own, now)
        except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
            result.failed_workspaces += 1
            fields = {"event": "prices_workspace_failed", "exc_type": type(exc).__name__}
            logger.error("a workspace's prices were not fetched", extra={"fields": fields})
            continue
        if out.sources:
            result.workspaces += 1
        result.assets += out.assets
        result.rows_new += new
        result.assets_failed += len(out.failed)
    result.coingecko = per_key.with_key
    result.errors = {
        f"{source}:{kind}" + ("" if status is None else f":{status}"): n
        for (source, kind, status), n in sorted(sources.errors.items(), key=str)
    }
    result.lookup_misses = {
        f"{source}:{kind}" + ("" if status is None else f":{status}"): n
        for (source, kind, status), n in sorted(sources.misses.items(), key=str)
    }
    fields = {
        "event": "prices_fetched",
        "workspaces": result.workspaces,
        "failed_workspaces": result.failed_workspaces,
        "assets": result.assets,
        "rows_new": result.rows_new,
        "assets_failed": result.assets_failed,
        "coingecko": result.coingecko,
        "errors": result.errors,
        "lookup_misses": result.lookup_misses,
    }
    level = logging.WARNING if result.errors or result.failed_workspaces else logging.INFO
    logger.log(level, "price fetch run", extra={"fields": fields})
    return result
