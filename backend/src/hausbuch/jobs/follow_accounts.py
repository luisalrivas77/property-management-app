"""`follow_accounts`: once after the deploy of 14 Nov 2026 (phase 6 D165 (a), D176 (3)
amended), queued by `hausbuch follow-accounts`. Every account that already has its IBAN or
number takes the statements waiting for it, and every ready depot suggestion is rebuilt
(services.statements.follow_all_accounts). It runs in the worker because reading a stored
statement or a depot document's printed number needs the object store, which the operator's
computer does not hold. One transaction; no retry (the command can be run again: what it did
is no longer waiting). Never the model."""

import logging
from uuid import UUID

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.enums import ActorType
from hausbuch.services.context import Actor, new_command
from hausbuch.services.statements import AccountsFollowed, follow_all_accounts

from .document_read import JobDeps
from .enqueue import FOLLOW_ACCOUNTS_TASK

logger = logging.getLogger("hausbuch.jobs.follow_accounts")

SYSTEM_ACTOR = Actor(ActorType.SYSTEM, FOLLOW_ACCOUNTS_TASK)


def run_follow_accounts(deps: JobDeps, *, workspace_id: UUID) -> AccountsFollowed:
    with workspace_transaction(deps.engine, workspace_id) as conn:
        ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=deps.clock())
        result = follow_all_accounts(ctx, deps.blob_store, iban_key=deps.settings.iban_hmac_key)
    logger.info(
        "accounts followed",
        extra={
            "fields": {
                "event": FOLLOW_ACCOUNTS_TASK,
                "imported": result.imported,
                "refused": result.refused,
                "rebuilt": result.rebuilt,
            }
        },
    )
    return result
