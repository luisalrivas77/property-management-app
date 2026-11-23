"""The refresh_crypto job: „Jetzt aktualisieren“ (docs/phases/phase-10.md §7.7, D291).

Queued by POST /crypto/refresh (services.crypto_refresh) for one workspace:

1. every active wallet address of the household is read again through its existing sync
   (services.chain.sync.sync_wallets, phase 7 D182: the same cursor, so only new
   transactions are read; each address its own run and `wallet_sync` row, a failed address
   recorded there), then its coin transfers relinked once (relink_after_sync);
2. then, whatever step 1 did, a current price for every held coin
   (services.prices.spot): Kraken's ticker, else CoinGecko's simple price with the
   workspace's key (phase 6 D260), stored as `spot`.

Step 1 failing as a whole (its first read, say) never stops step 2: it is logged by its
exception class and the prices are still read. A failure of step 2 is logged the same way and
raised, so the job ends `failed` and GET /crypto/refresh says so. No retry: the household
presses again. The lock is the wallet syncs' own (`wallets:sync`), so the daily run, „Jetzt
abrufen“ and this job never read one address at once.

Logged: one line per run with counts and the failed price sources by name, error class and
HTTP status only; never an address, a pair's price, a URL or the key.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import SecretStr

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.app_secrets import AppSecretName
from hausbuch.schemas.enums import ActorType
from hausbuch.services import app_secrets
from hausbuch.services.chain.sync import sync_wallets
from hausbuch.services.chain.transfers import relink_after_sync
from hausbuch.services.context import Actor
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
    wallets_failed: bool = False
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
        "coins": p.coins,
        "kraken": p.kraken,
        "coingecko": p.coingecko,
        "unpriced": p.unpriced,
        "stored": p.stored,
        "key_unreadable": result.key_unreadable,
        "errors": {f"{s}:{k}:{st}": n for (s, k, st), n in sorted(p.errors.items(), key=str)},
    }
    logger.info("crypto values refreshed", extra={"fields": fields})


def run_refresh_crypto(
    deps: JobDeps, *, workspace_id: UUID, fetch: Fetch = urllib_fetch
) -> RefreshResult:
    """One press of „Jetzt aktualisieren“. `fetch` is for tests (the price sources only; the
    chain sources come from the settings as in every wallet sync)."""
    result = RefreshResult()
    try:
        outcomes = sync_wallets(
            deps.engine,
            workspace_id,
            settings=deps.settings,
            store=deps.blob_store,
            actor=SYSTEM_ACTOR,
            clock=deps.clock,
        )
        result.addresses = len(outcomes)
        relink_after_sync(deps.engine, workspace_id, actor=SYSTEM_ACTOR, clock=deps.clock)
    except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
        result.wallets_failed = True
        fields = {"event": "crypto_refresh_wallets_failed", "exc_type": type(exc).__name__}
        logger.error("the wallets were not read again", extra={"fields": fields})
    try:
        key = _coingecko_key(deps, workspace_id, result)
        result.prices = refresh_prices(
            deps.engine, workspace_id, clock=deps.clock, fetch=fetch, coingecko_key=key
        )
    except Exception as exc:
        fields = {"event": "crypto_refresh_prices_failed", "exc_type": type(exc).__name__}
        logger.error("no current prices read", extra={"fields": fields})
        raise
    finally:
        _log(result)
    return result
