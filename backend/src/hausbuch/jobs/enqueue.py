"""Enqueue functions the request path calls (phase 2 spec §5.1).

Seam between the step 1 chunks: the worker chunk owns this file; the proposals chunk only
calls it. Keep the signature.

The job row is inserted on the caller's own connection, inside its transaction, so the job
exists if and only if the proposal row commits. Procrastinate's sync connector takes an
explicit psycopg connection for that (`connection=`); the app object used here is never
opened, since it only builds the insert.

Jobs carry `lock = doc:{document_id}`, so two jobs for one document never run at once. They
carry no queueing lock: Procrastinate enforces one with a unique index over waiting (todo)
jobs, and a retry turns a running job back into a waiting one, so a retry while a re-run's
job waits would violate that index, leaving the retried job stuck in `doing` and, through its
lock, blocking every later job of the document. A duplicate job is harmless instead: it
serves the document's newest pending proposal (jobs.document_read), finds none once the first
job is done, and sends nothing.
"""

from uuid import UUID

import procrastinate
from sqlalchemy import Connection

# Defined here, not in document_read: document_read imports services.proposals, which imports
# this module, so importing the name from document_read is a cycle that breaks whenever
# hausbuch.jobs.document_read is the first of the three to be imported.
TASK_NAME = "document_read"

# Never opened: defer() with `connection=` uses the given connection, not a pool.
_DEFER_APP = procrastinate.App(connector=procrastinate.SyncPsycopgConnector())


def document_lock(document_id: UUID) -> str:
    return f"doc:{document_id}"


def enqueue_document_read(
    conn: Connection, *, workspace_id: UUID, document_id: UUID, proposal_id: UUID
) -> None:
    """Queue `document_read` in the caller's transaction, so the job exists if and only if
    the proposal row commits. Locked by `doc:{document_id}`: never two running at once."""
    deferrer = _DEFER_APP.configure_task(
        TASK_NAME,
        allow_unknown=True,
        lock=document_lock(document_id),
        connection=conn.connection.driver_connection,
    )
    deferrer.defer(
        workspace_id=str(workspace_id),
        document_id=str(document_id),
        proposal_id=str(proposal_id),
    )


# --- Step 4: matching (§5.1) -------------------------------------------------------------
#
# Same pattern: inserted in the caller's transaction, a lock and no queueing lock. A
# duplicate job is harmless: a line that already has rows is not matched again
# (services.matching.Line.open_for).

MATCH_IMPORT_TASK = "match_import"
MATCH_TRANSACTION_TASK = "match_transaction"
REMATCH_TASK = "rematch_on_evidence"
REMATCH_RULE_TASK = "rematch_on_rule"


def import_lock(import_id: UUID) -> str:
    return f"import:{import_id}"


def transaction_lock(transaction_id: UUID) -> str:
    return f"tx:{transaction_id}"


def _defer(conn: Connection, task: str, lock: str, **kwargs: str) -> None:
    _DEFER_APP.configure_task(
        task, allow_unknown=True, lock=lock, connection=conn.connection.driver_connection
    ).defer(**kwargs)


def enqueue_match_import(conn: Connection, *, workspace_id: UUID, import_id: UUID) -> None:
    """Queue `match_import` for a reconciled import (it fans out one match_transaction per
    line). Locked by `import:{id}`, like statement_parse, so it never runs beside a parse."""
    _defer(
        conn,
        MATCH_IMPORT_TASK,
        import_lock(import_id),
        workspace_id=str(workspace_id),
        import_id=str(import_id),
    )


def enqueue_match_transaction(
    conn: Connection, *, workspace_id: UUID, transaction_id: UUID
) -> None:
    """Queue `match_transaction` for one line. Locked by `tx:{id}`."""
    _defer(
        conn,
        MATCH_TRANSACTION_TASK,
        transaction_lock(transaction_id),
        workspace_id=str(workspace_id),
        transaction_id=str(transaction_id),
    )


def enqueue_rematch_on_evidence(conn: Connection, *, workspace_id: UUID, document_id: UUID) -> None:
    """Queue `rematch_on_evidence` for a document whose new reading may change lines.
    Locked by `doc:{id}`, so it waits for a running read of the same document."""
    _defer(
        conn,
        REMATCH_TASK,
        document_lock(document_id),
        workspace_id=str(workspace_id),
        document_id=str(document_id),
    )


def rule_lock(rule_id: UUID) -> str:
    return f"rule:{rule_id}"


def enqueue_rematch_on_rule(conn: Connection, *, workspace_id: UUID, rule_id: UUID) -> None:
    """Queue `rematch_on_rule` for a newly learned rule (in the rule insert's transaction).
    Locked by `rule:{id}`: the job's input is one rule, and each line it touches is
    serialised by match_line's row lock (and _guarded's advisory locks), so a wider lock
    would only make one stuck job hold up other rules' jobs."""
    _defer(
        conn,
        REMATCH_RULE_TASK,
        rule_lock(rule_id),
        workspace_id=str(workspace_id),
        rule_id=str(rule_id),
    )
