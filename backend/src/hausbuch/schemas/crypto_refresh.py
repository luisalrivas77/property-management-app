"""„Jetzt aktualisieren“ on Anlagen › Krypto and in the net worth's panel
(docs/phases/phase-10.md §7.7, D291; api/routes/crypto_refresh.py). A run is shown by its job
id, its state, two moments and, once it succeeded, its counts."""

from datetime import datetime
from typing import Literal

from hausbuch.schemas.common import Schema

CryptoRefreshStatus = Literal["idle", "running", "succeeded", "failed"]


class CryptoRefreshState(Schema):
    """GET /crypto/refresh, and POST's answer.

    status: `idle` (never pressed), `running` (queued or reading), `succeeded`, `failed`.
    queued (POST only): whether this press queued a new run; false while one runs or one
    finished less than two minutes ago, whose state is then returned (one at a time per
    household). spot_read_at: the moment of the newest price read on demand today (Berlin),
    null when none was read today. prices_done: while a run is `running`, whether it has
    already stored its current prices (a price read on demand at or after the run started),
    so the card says the wallets are still being checked; false otherwise, and false through
    a run that stored no price at all.

    The counts of the newest run when it succeeded (its workspace.crypto_refreshed event),
    else null: `stored`, the held coins that got a current price; `unpriced`, those with a
    price source (a Kraken pair or a CoinGecko id) that kept their daily close;
    `wallets_failed`, the wallet addresses that could not be read; `wallets_aborted`, the
    wallet read stopped as a whole, so no address count is known."""

    job_id: int | None = None
    status: CryptoRefreshStatus
    queued: bool = False
    finished_at: datetime | None = None
    spot_read_at: datetime | None = None
    prices_done: bool = False
    stored: int | None = None
    unpriced: int | None = None
    wallets_failed: int | None = None
    wallets_aborted: bool | None = None
