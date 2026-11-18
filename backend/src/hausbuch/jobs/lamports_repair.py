"""`repair_solana_lamports`: once after the deploy of 18 Nov 2026 (phase 7 D186 (2)
amended), queued by `hausbuch repair-solana-lamports`. Every active Solana address's stored
originals are read again with the own token accounts they name, and a transaction whose own
token account received lamports the earlier reading missed gets its group superseded
(services.chain.lamports_repair). It runs in the worker because the stored originals need the
object store, which the operator's computer does not hold. One transaction; no retry (the
command can be run again: a corrected group is left as it is). A dry run writes nothing and
logs the same counts. Never the model, never the provider; the log holds counts only."""

import logging
from uuid import UUID

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.enums import ActorType
from hausbuch.services.chain.lamports_repair import (
    LamportsRepaired,
    repair_token_account_lamports,
)
from hausbuch.services.context import Actor, new_command

from .document_read import JobDeps
from .enqueue import REPAIR_SOLANA_LAMPORTS_TASK

logger = logging.getLogger("hausbuch.jobs.lamports_repair")

SYSTEM_ACTOR = Actor(ActorType.SYSTEM, REPAIR_SOLANA_LAMPORTS_TASK)


class _DryRun(Exception):
    """Raised inside the transaction so a dry run rolls back whatever it touched."""


def run_repair_solana_lamports(
    deps: JobDeps, *, workspace_id: UUID, dry_run: bool = False
) -> LamportsRepaired:
    result = LamportsRepaired()
    try:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=deps.clock())
            result = repair_token_account_lamports(
                ctx, deps.blob_store, hmac_key=deps.settings.iban_hmac_key, dry_run=dry_run
            )
            if dry_run:
                raise _DryRun
    except _DryRun:
        pass
    logger.info(
        "solana lamports repaired",
        extra={
            "fields": {
                "event": REPAIR_SOLANA_LAMPORTS_TASK,
                "dry_run": dry_run,
                "addresses": result.addresses,
                "records": result.records,
                "unchanged": result.unchanged,
                "carried": result.carried,
                "proposed": result.proposed,
                "skipped": result.skipped,
            }
        },
    )
    return result
