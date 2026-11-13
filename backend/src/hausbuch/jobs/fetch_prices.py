"""The fetch_prices periodic task (docs/phases/phase-6.md D167 (3), D174).

At 07:00 UTC, after the ECB published the previous day's rates (around 16:00 CET), the
worker runs services.prices.fetch for every provisioned workspace: the ECB rates first, then
each held coin (CoinGecko with the Demo key HAUSBUCH_COINGECKO_KEY, Kraken as the fallback and
alone without the key), then each held ETF's NAV from its issuer's file. One run asks each
source once per coin day, pair or fund for all workspaces (services.prices.fetch.Sources).

No retry: a failed source leaves the price stale and the next day's run asks again; D174's
card appears after seven failed days. lock= keeps one run at a time.

Logged: one line per run with counts, and the failed sources by name, exception class and
HTTP status only (never a URL with a fund or coin, a price, or the key); a workspace that
fails is logged by its exception class.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.schemas.enums import ActorType
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
    coingecko: bool = False  # the key is set
    errors: dict[str, int] = field(default_factory=dict)


def sources_for(deps: JobDeps, fetch: Fetch = urllib_fetch, sleep: Any = None) -> Sources:
    key = deps.settings.coingecko_key
    coingecko = None
    if key is not None:
        coingecko = CoinGeckoDaily(key=key, fetch=fetch)
        if sleep is not None:
            coingecko.sleep = sleep
    return Sources(fetch=fetch, coingecko=coingecko)


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
    sources = sources_for(deps, fetch, sleep)
    result = FetchResult(coingecko=sources.coingecko is not None)
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    for ws in sorted(set(ids)):
        try:
            new, out = run_workspace(deps.engine, ws, SYSTEM_ACTOR, sources, now)
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
    result.errors = {
        f"{source}:{kind}" + ("" if status is None else f":{status}"): n
        for (source, kind, status), n in sorted(sources.errors.items(), key=str)
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
    }
    level = logging.WARNING if result.errors or result.failed_workspaces else logging.INFO
    logger.log(level, "price fetch run", extra={"fields": fields})
    return result
