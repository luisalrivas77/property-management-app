"""The wallet sync jobs (docs/phases/phase-7.md D182 (1)).

`sync_wallets` runs daily at 06:30 UTC, beside the bank schedule (neither depends on the
other; 06:45 until 23 Nov 2026): every active address of every provisioned workspace, the one
synced longest ago first, each in its own run with its own `wallet_sync` row
(services.chain.sync). The request budget of each source kind is shared by the whole run, so
a long history continues the next day (`wallet.sync_deferred`). It starts the morning chain
(jobs.morning): when it ends, the price fetch is queued, or first the one retry below.

The one retry (23 Nov 2026): an address whose morning run ended `failed` with
`wallet.source_failed` (a provider that did not answer, often only for a moment) is read
once more about ten minutes later by `sync_wallets_retry` (run_retry_wallets), with a fresh
request budget; that job queues the price fetch and never another retry. „Jetzt abrufen“
(`sync_wallet`) retries nothing.

`sync_wallet` is „Jetzt abrufen“ on a wallet's page: the addresses of one wallet, queued by
POST /wallets/{id}/sync in the caller's transaction.

All three take one lock, so two runs never read the same address at once (a second writer of
the same movement hash would fail its row). The retry job waits at a lower priority, so while
it waits its ten minutes it never holds back a press of „Jetzt abrufen“ (Procrastinate holds
a job back behind a waiting job of the same lock only at an equal or higher priority).
Otherwise no retry: a failed address is read again the next day from the same cursor.
Logged per address by services.chain.sync (chain, outcome, counts, exception class), never an
address, cursor, hash, URL or key; one line per run here.

After a workspace's addresses ran, its coin transfers are relinked once
(services.chain.transfers.relink_after_sync, phase 7 step 5, D184): in its own transaction,
so the linker never fails a sync, a failure logged by its class name only.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

import procrastinate
from sqlalchemy import Connection, text

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.rules.errors import WALLET_SOURCE_FAILED
from hausbuch.schemas.chain import WalletSyncStatus
from hausbuch.schemas.enums import ActorType
from hausbuch.services.chain.sync import SyncOutcome, sync_wallets
from hausbuch.services.chain.transfers import relink_after_sync
from hausbuch.services.context import Actor

from .document_read import JobDeps

SYNC_WALLETS_TASK = "sync_wallets"
SYNC_WALLETS_CRON = "30 6 * * *"  # D182 (1): 06:30, the worker's clock (UTC in the container)
SYNC_WALLET_TASK = "sync_wallet"
SYNC_WALLETS_RETRY_TASK = "sync_wallets_retry"
RETRY_DELAY_S = 600  # the one retry, about ten minutes after the morning run
RETRY_PRIORITY = -1  # below „Jetzt abrufen“ (0): a waiting retry never holds it back
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
    # (workspace, address) whose run failed with wallet.source_failed: the one retry's list
    retry: list[tuple[UUID, UUID]] = field(default_factory=list)


def _retry_targets(workspace_id: UUID, outcomes: list[SyncOutcome]) -> list[tuple[UUID, UUID]]:
    return [
        (workspace_id, o.address_id)
        for o in outcomes
        if o.status is WalletSyncStatus.FAILED and o.error_code == WALLET_SOURCE_FAILED
    ]


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
        result.retry.extend(_retry_targets(ws, outcomes))
        relink_after_sync(deps.engine, ws, actor=SYSTEM_ACTOR, clock=deps.clock)
    _log(result, SYNC_WALLETS_TASK)
    return result


def run_retry_wallets(deps: JobDeps, targets: list[tuple[UUID, UUID]]) -> WalletRunResult:
    """The morning run's one retry: each listed address that is still active is read once
    more (services.chain.sync.sync_wallets with address_ids, so the addresses of one account
    and chain still run one after another), with a fresh request budget per source kind for
    this run. Never raises for one workspace's failure; `retry` stays empty, since nothing
    is ever retried twice."""
    result = WalletRunResult()
    budgets: dict = {}
    by_workspace: dict[UUID, list[UUID]] = {}
    for ws, address_id in targets:
        by_workspace.setdefault(ws, []).append(address_id)
    for ws in sorted(by_workspace):
        try:
            outcomes = sync_wallets(
                deps.engine,
                ws,
                settings=deps.settings,
                store=deps.blob_store,
                actor=SYSTEM_ACTOR,
                clock=deps.clock,
                budgets=budgets,
                address_ids=by_workspace[ws],
            )
        except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
            result.failed_workspaces += 1
            fields = {"event": "wallet_sync_workspace_failed", "exc_type": type(exc).__name__}
            logger.error("a workspace's wallets were not read", extra={"fields": fields})
            continue
        _count(result, outcomes)
        relink_after_sync(deps.engine, ws, actor=SYSTEM_ACTOR, clock=deps.clock)
    _log(result, SYNC_WALLETS_RETRY_TASK)
    return result


def encode_targets(targets: list[tuple[UUID, UUID]]) -> list[str]:
    """The retry job's argument: "<workspace>:<address>", ids only."""
    return [f"{ws}:{address_id}" for ws, address_id in targets]


def decode_targets(values: list[str]) -> list[tuple[UUID, UUID]]:
    pairs = [value.split(":", 1) for value in values]
    return [(UUID(ws), UUID(address_id)) for ws, address_id in pairs]


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
    relink_after_sync(deps.engine, workspace_id, actor=SYSTEM_ACTOR, clock=deps.clock)
    _log(result, SYNC_WALLET_TASK)
    return result


def enqueue_sync_wallet(
    conn: Connection, *, workspace_id: UUID, account_id: UUID, once: bool = False
) -> bool:
    """Queue `sync_wallet` for one wallet, in the caller's transaction. Whether it queued.

    once (the deposit lookups of D181 (c) amended 16 Nov 2026, queued by every exchange
    import, answer and relink): not when a `sync_wallet` of the same wallet already waits to
    run, since that run reads the chain when it starts and covers this cause too. The lookup
    is jobs.enqueue's (enqueue_rematch_waiting): a plain SELECT holding the waiting job FOR
    KEY SHARE until the caller commits, so the worker cannot start it in between, and never
    Procrastinate's queueing_lock, whose unique index would raise inside the caller's
    transaction. Two commands racing past the lookup queue two runs; that is harmless (one
    lock, the second finds nothing new). „Jetzt abrufen“ and the daily run do not pass it."""
    if once:
        waiting = conn.execute(
            text(
                "SELECT 1 FROM procrastinate_jobs WHERE task_name = :task AND status = 'todo'"
                " AND args->>'workspace_id' = :ws AND args->>'account_id' = :account"
                " LIMIT 1 FOR KEY SHARE"
            ),
            {"task": SYNC_WALLET_TASK, "ws": str(workspace_id), "account": str(account_id)},
        ).first()
        if waiting is not None:
            return False
    _DEFER_APP.configure_task(
        SYNC_WALLET_TASK,
        allow_unknown=True,
        lock=SYNC_WALLETS_LOCK,
        connection=conn.connection.driver_connection,
    ).defer(workspace_id=str(workspace_id), account_id=str(account_id))
    return True
