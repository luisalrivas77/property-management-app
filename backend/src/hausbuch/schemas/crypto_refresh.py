"""„Jetzt aktualisieren“ on Anlagen › Krypto and in the net worth's panel
(docs/phases/phase-10.md §7.7, D291; api/routes/crypto_refresh.py). A run is shown by its job
id, its state and two moments only."""

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
    null when none was read today."""

    job_id: int | None = None
    status: CryptoRefreshStatus
    queued: bool = False
    finished_at: datetime | None = None
    spot_read_at: datetime | None = None
