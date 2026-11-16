"""Lots, the Krypto tab and the year-end holdings statement (docs/phases/phase-7.md D188,
D190, §13, §14; step 6). services/holdings.py computes everything on read.

GET /holdings/crypto is the Anlagen › Krypto tab; GET /wallets/holdings is D190's statement
per wallet and exchange account; GET /wallets/{id}/lots an account's open lots. `on` is a day
on the Berlin calendar, today by default. Every answer carries masked addresses only.

Registered before `wallets`, so /wallets/holdings is not read as /wallets/{account_id}.
"""

from datetime import date, datetime
from uuid import UUID

from hausbuch.schemas.chain import HoldingsStatement
from hausbuch.schemas.holdings import CryptoHoldings, CryptoLot
from hausbuch.services import holdings
from hausbuch.services.documents import LOCAL_TIME_ZONE

from ..deps import Cmd, domain_router

router = domain_router()


def _today() -> date:
    return datetime.now(LOCAL_TIME_ZONE).date()


@router.get("/holdings/crypto")
def crypto_holdings(cmd: Cmd, on: date | None = None) -> CryptoHoldings:
    """Every wallet and exchange account with its coins and their open lots (FIFO per account
    and coin), each lot with its day, units, cost and „steuerfrei ab“, or the prices whose
    absence leaves it without a cost („Kurs fehlt“); the four cards of the tab."""
    return cmd.read(holdings.crypto_holdings, on or _today())


@router.get("/wallets/holdings")
def wallets_holdings(cmd: Cmd, on: date | None = None) -> HoldingsStatement:
    """D190's statement at the end of the Berlin day `on`: per account its addresses' units,
    its positions with their open lots and value, the spam tokens (never valued) and the
    addresses whose last sync on or before the day did not reconcile."""
    return cmd.read(holdings.statement, on or _today())


@router.get("/wallets/{account_id}/lots")
def wallet_lots(
    account_id: UUID, cmd: Cmd, asset: UUID | None = None, on: date | None = None
) -> list[CryptoLot]:
    """The account's open lots on `on`, of one coin with `asset`, in FIFO order. An account
    with nothing held (or of another workspace) answers an empty list."""
    return cmd.read(holdings.account_lots, account_id, on or _today(), asset)
