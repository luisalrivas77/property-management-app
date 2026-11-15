"""The wallet sync routes' bodies (docs/phases/phase-7.md D182 (1); api/routes/wallet_sync.py).

A run is shown by ids, counts and its outcome only: never the address, the cursor, a
transaction hash or the provider's URL (the host name is the provider's, D181).
"""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from hausbuch.schemas.chain import WalletSyncStatus
from hausbuch.schemas.common import Schema


class WalletSyncQueued(Schema):
    """POST /wallets/{id}/sync: „Jetzt abrufen“ queued for the wallet's active addresses."""

    account_id: UUID
    addresses: int = Field(ge=0)


class WalletSyncRun(Schema):
    """One address's run (a wallet_sync row). `balances` and `difference` are base units per
    asset (the contract, or `native`), as strings. `reconciled` null with `balances` set: a
    group waits for an answer („wartet auf Antwort“); `balances` null on a succeeded run: the
    budget ended first (`wallet.sync_deferred`)."""

    id: UUID
    address_id: UUID
    address_masked: str
    started_at: datetime
    finished_at: datetime | None = None
    status: WalletSyncStatus
    source_host: str
    block: int | None = None
    txs_seen: int = Field(ge=0)
    movements_new: int = Field(ge=0)
    balances: dict[str, str] | None = None
    reconciled: bool | None = None
    difference: dict[str, str] | None = None
    error_code: str | None = None
    document_id: UUID | None = None
    deferred: bool = False
