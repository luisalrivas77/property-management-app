"""Crypto values refreshed on demand (docs/phases/phase-10.md §7.7, D291).

- POST /crypto/refresh: „Jetzt aktualisieren“. 202 with the run's state; `queued` false when
  one runs or finished less than two minutes ago (that run is returned, nothing is queued).
- GET /crypto/refresh: the newest run's state and the moment of today's newest price read on
  demand. Never kept (§7.4): a job's state is no workspace data, so the data version does not
  follow it.
"""

from datetime import UTC, datetime

from hausbuch.schemas.crypto_refresh import CryptoRefreshState
from hausbuch.services import crypto_refresh

from ..deps import Cmd, domain_router

router = domain_router()


@router.post("/crypto/refresh", status_code=202)
def crypto_refresh_start(cmd: Cmd) -> CryptoRefreshState:
    return cmd.run(crypto_refresh.queue_refresh)


@router.get("/crypto/refresh")
def crypto_refresh_state(cmd: Cmd) -> CryptoRefreshState:
    return cmd.read(crypto_refresh.refresh_state, cmd.auth.workspace_id, datetime.now(UTC))
