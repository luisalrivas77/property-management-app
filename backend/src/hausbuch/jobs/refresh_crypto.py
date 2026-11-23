"""The refresh_crypto job: „Jetzt aktualisieren“ (docs/phases/phase-10.md §7.7, D291).

Queued by POST /crypto/refresh (services.crypto_refresh) for one workspace:

1. first, a current price for every coin held now (services.prices.spot): Kraken's ticker,
   else CoinGecko's simple price with the workspace's key (phase 6 D260), stored as `spot`,
   so the values change within seconds of the press (GET /crypto/refresh's `prices_done`;
   one press had taken 43 s live when the prices came last, 23 Nov 2026);
2. then every active wallet address of the household is read again through its existing sync
   (services.chain.sync.sync_wallets, phase 7 D182: the same cursor, so only new
   transactions are read; each address its own run and `wallet_sync` row, a failed address
   recorded there), one after another as every wallet sync reads them, then its coin
   transfers relinked once (relink_after_sync);
3. then a current price for the coins held only since step 2 (a coin first received, a token
   first priced through its coin), none asked when there is none.

Step 1 failing never stops step 2: it is logged by its exception class, the wallets are still
read, and then it is raised, so the job ends `failed` and GET /crypto/refresh says so. Step 2
failing as a whole (its first read, say) is logged the same way and stops nothing. Step 3
failing (a database error: the sources' failures are counts) is logged and the run is recorded
with step 1's counts. No retry: the household presses again. The lock is the wallet syncs' own
(`wallets:sync`), so the daily run, „Jetzt abrufen“ and this job never read one address at
once.

The wallets are read one after another, not side by side, so a press takes about as long as
before in total; only its prices come first. Every sync's start (services.chain.sync._begin)
creates the chain's coins it lacks, under no lock: the native coin, and through adopt_registry
the chainless coin it is priced as (Ether on Ethereum and Ether (Portal) on Solana are both
priced as ETH). Two chains read side by side could both create that coin, and the unique index
on a symbol (migration 0041) would then fail one of the two reads.

A run whose step 1 finished appends one `workspace.crypto_refreshed` event with its counts
(schemas.events.CryptoRefreshed, tied to its job id), so GET /crypto/refresh can name the
coins that got no current price and the addresses that were not read (principle 7). An
address counts as not read when its run did not end `succeeded` (failed, refused, throttled,
or no run at all, wallet.key_missing); a failure of the relink after the reads is logged and
is not a failed read. A failure to write the event is logged and never fails the run: the
prices are stored, and GET then shows no counts.

Logged: one line per run with counts and the failed price sources by name, error class and
HTTP status only; never an address, a pair's price, a URL or the key.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import SecretStr

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.app_secrets import AppSecretName
from hausbuch.schemas.chain import WalletSyncStatus
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.events import CryptoRefreshed, EventType
from hausbuch.services import app_secrets
from hausbuch.services.chain.sync import sync_wallets
from hausbuch.services.chain.transfers import relink_after_sync
from hausbuch.services.context import Actor, new_command
from hausbuch.services.events import append_event
from hausbuch.services.prices.http import Fetch, urllib_fetch
from hausbuch.services.prices.spot import SpotResult, refresh_prices

from .document_read import JobDeps
from .sync_wallets import SYNC_WALLETS_LOCK

REFRESH_CRYPTO_TASK = "refresh_crypto"
REFRESH_CRYPTO_LOCK = SYNC_WALLETS_LOCK
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, REFRESH_CRYPTO_TASK)

logger = logging.getLogger("hausbuch.jobs.refresh_crypto")


@dataclass
class RefreshResult:
    addresses: int = 0
    wallets_failed: int = 0  # addresses whose run did not succeed
    wallets_aborted: bool = False  # the wallet step stopped as a whole
    key_unreadable: bool = False
    prices: SpotResult = field(default_factory=SpotResult)


def _coingecko_key(deps: JobDeps, workspace_id: UUID, result: RefreshResult) -> SecretStr | None:
    """The workspace's CoinGecko key: typed in the app, else the container's; unreadable →
    none (never the container's then, phase 6 D260)."""
    try:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            settings = app_secrets.workspace_settings(
                conn, deps.settings, [AppSecretName.COINGECKO_KEY]
            )
    except app_secrets.SecretUnreadable:
        result.key_unreadable = True
        return None
    return settings.coingecko_key


def _log(result: RefreshResult) -> None:
    p = result.prices
    fields = {
        "event": "crypto_refreshed",
        "addresses": result.addresses,
        "wallets_failed": result.wallets_failed,
        "wallets_aborted": result.wallets_aborted,
        "coins": p.coins,
        "kraken": p.kraken,
        "coingecko": p.coingecko,
        "unpriced": p.unpriced,
        "stored": p.stored,
        "key_unreadable": result.key_unreadable,
        "errors": {f"{s}:{k}:{st}": n for (s, k, st), n in sorted(p.errors.items(), key=str)},
    }
    logger.info("crypto values refreshed", extra={"fields": fields})


def record_run(
    deps: JobDeps, workspace_id: UUID, result: RefreshResult, job_id: int | None
) -> None:
    """The run's counts as one workspace.crypto_refreshed event, in its own transaction."""
    p = result.prices
    with workspace_transaction(deps.engine, workspace_id) as conn:
        ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=deps.clock())
        append_event(
            ctx,
            EventType.WORKSPACE_CRYPTO_REFRESHED,
            workspace_id,
            CryptoRefreshed(
                job_id=job_id,
                coins=p.coins,
                stored=p.stored,
                unpriced=p.unpriced,
                addresses=result.addresses,
                wallets_failed=result.wallets_failed,
                wallets_aborted=result.wallets_aborted,
            ),
        )


def _read_wallets(deps: JobDeps, workspace_id: UUID, result: RefreshResult) -> None:
    """Step 2: every active address read again, then its coin transfers relinked; a failure is
    logged by its class name and never raised."""
    try:
        outcomes = sync_wallets(
            deps.engine,
            workspace_id,
            settings=deps.settings,
            store=deps.blob_store,
            actor=SYSTEM_ACTOR,
            clock=deps.clock,
        )
    except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
        result.wallets_aborted = True
        fields = {"event": "crypto_refresh_wallets_failed", "exc_type": type(exc).__name__}
        logger.error("the wallets were not read again", extra={"fields": fields})
        return
    result.addresses = len(outcomes)
    result.wallets_failed = sum(o.status is not WalletSyncStatus.SUCCEEDED for o in outcomes)
    try:
        relink_after_sync(deps.engine, workspace_id, actor=SYSTEM_ACTOR, clock=deps.clock)
    except Exception as exc:  # noqa: BLE001 - class name only; the reads stand
        fields = {"event": "crypto_refresh_relink_failed", "exc_type": type(exc).__name__}
        logger.error("the coin transfers were not relinked", extra={"fields": fields})


def run_refresh_crypto(
    deps: JobDeps,
    *,
    workspace_id: UUID,
    fetch: Fetch = urllib_fetch,
    job_id: int | None = None,
) -> RefreshResult:
    """One press of „Jetzt aktualisieren“. `fetch` is for tests (the price sources only; the
    chain sources come from the settings as in every wallet sync); `job_id` is the
    refresh_crypto job's, kept in the run's event."""
    result = RefreshResult()
    key: SecretStr | None = None
    failed: Exception | None = None
    try:  # 1. the coins held now, priced first (GET /crypto/refresh: prices_done)
        key = _coingecko_key(deps, workspace_id, result)
        result.prices = refresh_prices(
            deps.engine, workspace_id, clock=deps.clock, fetch=fetch, coingecko_key=key
        )
    except Exception as exc:  # noqa: BLE001 - raised once the wallets are read
        failed = exc
        fields = {"event": "crypto_refresh_prices_failed", "exc_type": type(exc).__name__}
        logger.error("no current prices read", extra={"fields": fields})
    try:
        _read_wallets(deps, workspace_id, result)  # 2. the wallets, then the relink
        if failed is not None:
            raise failed
        try:  # 3. the coins held only since step 2 (none: no source is asked)
            result.prices.add(
                refresh_prices(
                    deps.engine,
                    workspace_id,
                    clock=deps.clock,
                    fetch=fetch,
                    coingecko_key=key,
                    skip=result.prices.asset_ids,
                )
            )
        except Exception as exc:  # noqa: BLE001 - step 1's prices stand; the run is recorded
            fields = {"event": "crypto_refresh_new_prices_failed", "exc_type": type(exc).__name__}
            logger.error("no current prices read for new coins", extra={"fields": fields})
    finally:
        _log(result)
    try:
        record_run(deps, workspace_id, result, job_id)
    except Exception as exc:  # noqa: BLE001 - the prices stand; GET then shows no counts
        fields = {"event": "crypto_refresh_record_failed", "exc_type": type(exc).__name__}
        logger.error("the run's counts were not recorded", extra={"fields": fields})
    return result
