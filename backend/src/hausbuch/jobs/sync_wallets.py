"""The wallet sync jobs (docs/phases/phase-7.md D182 (1)).

`sync_wallets` runs daily at 06:45 UTC, after the bank fetch (06:30): every active address of
every provisioned workspace, the one synced longest ago first, each in its own run with its
own `wallet_sync` row (services.chain.sync). The request budget of each source kind is shared
by the whole run, so a long history continues the next day (`wallet.sync_deferred`).

`sync_wallet` is „Jetzt abrufen“ on a wallet's page: the addresses of one wallet, queued by
POST /wallets/{id}/sync in the caller's transaction.

Both take one lock, so two runs never read the same address at once (a second writer of the
same movement hash would fail its row). No retry: a failed address is read again the next
day from the same cursor. Logged per address by services.chain.sync (chain, outcome, counts,
exception class), never an address, cursor, hash, URL or key; one line per run here.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

import procrastinate
from sqlalchemy import Connection

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.schemas.enums import ActorType
from hausbuch.services.chain.sync import SyncOutcome, sync_wallets
from hausbuch.services.context import Actor

from .document_read import JobDeps

SYNC_WALLETS_TASK = "sync_wallets"
SYNC_WALLETS_CRON = "45 6 * * *"  # D182 (1): 06:45, the worker's clock (UTC in the container)
SYNC_WALLET_TASK = "sync_wallet"
SYNC_WALLETS_LOCK = "wallets:sync"
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, SYNC_WALLETS_TASK)

logger = logging.getLogger("hausbuch.jobs.sync_wallets")

# Never opened: defer() with `connection=` uses the given connection (jobs.enqueue's pattern).
_DEFER_APP = procrastinate.App(connector=procrastinate.SyncPsycopgConnector())


@dataclass
class WalletRunResult:
    addresses: int = 0
    failed_workspaces: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    movements_new: int = 0
    deferred: int = 0


def _count(result: WalletRunResult, outcomes: list[SyncOutcome]) -> None:
    for o in outcomes:
        result.addresses += 1
        key = o.error_code or (o.status.value if o.status else "none")
        result.statuses[key] = result.statuses.get(key, 0) + 1
        result.movements_new += o.movements_new
        result.deferred += int(o.deferred)


def _log(result: WalletRunResult, task: str) -> None:
    fields = {
        "event": task,
        "addresses": result.addresses,
        "failed_workspaces": result.failed_workspaces,
        "statuses": result.statuses,
        "movements_new": result.movements_new,
        "deferred": result.deferred,
    }
    level = logging.WARNING if result.failed_workspaces else logging.INFO
    logger.log(level, "wallet sync run", extra={"fields": fields})


def run_sync_wallets(deps: JobDeps, *, workspace_ids=None) -> WalletRunResult:
    """The daily run. Never raises for one workspace's failure (logged by class name)."""
    result = WalletRunResult()
    budgets: dict = {}  # one per source kind, for the whole run (D182 (1))
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    for ws in sorted(set(ids)):
        try:
            outcomes = sync_wallets(
                deps.engine,
                ws,
                settings=deps.settings,
                store=deps.blob_store,
                actor=SYSTEM_ACTOR,
                clock=deps.clock,
                budgets=budgets,
            )
        except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
            result.failed_workspaces += 1
            fields = {"event": "wallet_sync_workspace_failed", "exc_type": type(exc).__name__}
            logger.error("a workspace's wallets were not read", extra={"fields": fields})
            continue
        _count(result, outcomes)
    _log(result, SYNC_WALLETS_TASK)
    return result


def run_sync_wallet(deps: JobDeps, *, workspace_id: UUID, account_id: UUID) -> WalletRunResult:
    """„Jetzt abrufen“: one wallet's addresses."""
    result = WalletRunResult()
    _count(
        result,
        sync_wallets(
            deps.engine,
            workspace_id,
            settings=deps.settings,
            store=deps.blob_store,
            actor=SYSTEM_ACTOR,
            clock=deps.clock,
            account_id=account_id,
        ),
    )
    _log(result, SYNC_WALLET_TASK)
    return result


def enqueue_sync_wallet(conn: Connection, *, workspace_id: UUID, account_id: UUID) -> None:
    """Queue `sync_wallet` for one wallet, in the caller's transaction."""
    _DEFER_APP.configure_task(
        SYNC_WALLET_TASK,
        allow_unknown=True,
        lock=SYNC_WALLETS_LOCK,
        connection=conn.connection.driver_connection,
    ).defer(workspace_id=str(workspace_id), account_id=str(account_id))
