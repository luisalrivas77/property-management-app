"""A wallet's chain sync (docs/phases/phase-7.md D182 (1)).

- POST /wallets/{id}/sync: „Jetzt abrufen“. 202 with the number of active addresses queued;
  the job (jobs.sync_wallets `sync_wallet`) reads each address in its own run. 404 for an
  account that is not a wallet of this workspace.
- GET /wallets/{id}/syncs: the latest runs of its addresses, newest first (at most 50), by
  the masked address only.
"""

from uuid import UUID

from sqlalchemy import Connection, func, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.jobs.sync_wallets import enqueue_sync_wallet
from hausbuch.schemas.chain import WalletSyncStatus
from hausbuch.schemas.enums import AccountKind, EntityType
from hausbuch.schemas.wallet_sync import WalletSyncQueued, WalletSyncRun
from hausbuch.services.context import CommandContext
from hausbuch.services.errors import NotFoundError

from ..deps import Cmd, domain_router

router = domain_router()

_LIMIT = 50


def _require_wallet(conn: Connection, account_id: UUID) -> None:
    kind = conn.execute(
        select(account_table.c.kind).where(account_table.c.id == account_id)
    ).scalar_one_or_none()
    if kind != AccountKind.WALLET.value:
        raise NotFoundError(EntityType.ACCOUNT, account_id)


def queue_wallet_sync(ctx: CommandContext, account_id: UUID) -> WalletSyncQueued:
    _require_wallet(ctx.conn, account_id)
    count = ctx.conn.execute(
        select(func.count())
        .select_from(address_table)
        .where(address_table.c.account_id == account_id, address_table.c.active.is_(True))
    ).scalar_one()
    if count:
        enqueue_sync_wallet(ctx.conn, workspace_id=ctx.workspace_id, account_id=account_id)
    return WalletSyncQueued(account_id=account_id, addresses=count)


def list_wallet_syncs(conn: Connection, account_id: UUID) -> list[WalletSyncRun]:
    _require_wallet(conn, account_id)
    rows = conn.execute(
        select(sync_table, address_table.c.address_masked)
        .select_from(sync_table.join(address_table, address_table.c.id == sync_table.c.address_id))
        .where(sync_table.c.account_id == account_id)
        .order_by(sync_table.c.started_at.desc(), sync_table.c.created_at.desc())
        .limit(_LIMIT)
    ).all()
    return [
        WalletSyncRun(
            id=r.id,
            address_id=r.address_id,
            address_masked=r.address_masked,
            started_at=r.started_at,
            finished_at=r.finished_at,
            status=WalletSyncStatus(r.status),
            source_host=r.source_host,
            block=r.block,
            txs_seen=r.txs_seen,
            movements_new=r.movements_new,
            balances=r.balances,
            reconciled=r.reconciled,
            difference=r.difference,
            error_code=r.error_code,
            document_id=r.document_id,
            deferred=r.status == WalletSyncStatus.SUCCEEDED.value and r.balances is None,
        )
        for r in rows
    ]


@router.post("/wallets/{account_id}/sync", status_code=202)
def wallet_sync_start(account_id: UUID, cmd: Cmd) -> WalletSyncQueued:
    return cmd.run(queue_wallet_sync, account_id)


@router.get("/wallets/{account_id}/syncs")
def wallet_sync_list(account_id: UUID, cmd: Cmd) -> list[WalletSyncRun]:
    return cmd.read(list_wallet_syncs, account_id)
