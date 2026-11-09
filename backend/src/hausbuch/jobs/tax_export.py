"""The tax_export job: renders and stores one queued export (phase-3.md §10, §12, D65).

Plain synchronous code over the engine like the matching jobs (jobs/tasks.py wraps it), so it
is tested by calling run_tax_export directly. One transaction does everything
(services.tax_export.write_export): the draft, the file, its blob, document_file, the
tax_export row and the event.

Outcomes, read back by GET /tax-exports/{id} (services.tax_export.export_state):
- written: the row exists, so the export is `ready`;
- exists: a second run for the same id; nothing written;
- too_large: the archive outgrew 100 MiB after the request checked it; the job succeeds without
  a row, which the state reads as `failed` with `tax_export.too_large`;
- a RuleError or NotFoundError (the property was deleted, the year no longer has a rule set):
  re-raised at once, so the job fails (`tax_export.failed`); a retry would not change it;
- any other error (database, blob store): RetryLater (10 s, 60 s, 5 min) while attempts
  remain, then re-raised, so the job fails.

No log line carries an amount, a name or an exception message: only ids, the outcome and the
exception's type.
"""

import logging
from uuid import UUID

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.tax import TaxExportKind
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import NotFoundError
from hausbuch.services.tax_export import WriteOutcome, write_export

from .document_read import MAX_RETRIES, RETRY_WAITS_S, JobDeps, RetryLater
from .enqueue import TAX_EXPORT_TASK

logger = logging.getLogger("hausbuch.jobs.tax_export")

SYSTEM_ACTOR = Actor(ActorType.SYSTEM, TAX_EXPORT_TASK)


def _actor(created_by: UUID | None) -> Actor:
    """The person who asked for the export, as the request's own actor would have been."""
    if created_by is None:
        return SYSTEM_ACTOR
    return Actor(ActorType.USER, f"user:{created_by}")


def run_tax_export(
    deps: JobDeps,
    *,
    workspace_id: UUID,
    export_id: UUID,
    property_id: UUID,
    tax_year: int,
    kind: TaxExportKind,
    created_by: UUID | None,
    attempt: int,
) -> WriteOutcome:
    log = {
        "event": TAX_EXPORT_TASK,
        "export_id": str(export_id),
        "kind": kind.value,
        "attempt": attempt,
    }
    try:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            ctx = new_command(conn, workspace_id, _actor(created_by), now=deps.clock())
            outcome = write_export(
                ctx,
                deps.blob_store,
                export_id=export_id,
                property_id=property_id,
                tax_year=tax_year,
                kind=kind,
                created_by=created_by,
            )
    except (RuleError, NotFoundError) as exc:
        logger.error("export refused", extra={"fields": log | {"exc_type": type(exc).__name__}})
        raise
    except Exception as exc:
        kind_name = type(exc).__name__
        if attempt < MAX_RETRIES:
            wait_s = RETRY_WAITS_S[attempt]
            logger.warning("export retried", extra={"fields": log | {"retry_in_s": wait_s}})
            raise RetryLater(wait_s) from None
        logger.error("export gave up", extra={"fields": log | {"exc_type": kind_name}})
        raise
    level = logging.WARNING if outcome == "too_large" else logging.INFO
    logger.log(level, "export done", extra={"fields": log | {"outcome": outcome}})
    return outcome
