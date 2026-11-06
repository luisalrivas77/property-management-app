"""Enqueue functions the request path calls (phase 2 spec §5.1).

Seam between the step 1 chunks: the worker chunk owns this file; the proposals chunk only
calls it. Keep the signature.

The job row is inserted on the caller's own connection, inside its transaction, so the job
exists if and only if the proposal row commits. Procrastinate's sync connector takes an
explicit psycopg connection for that (`connection=`); the app object used here is never
opened, since it only builds the insert. The insert runs in a savepoint: when a job with the
same queueing lock is already waiting (status todo), Postgres refuses it and Procrastinate
raises AlreadyEnqueued, and rolling back to the savepoint keeps the caller's transaction
usable. The waiting job then serves the newest pending proposal (jobs.document_read).
"""

import logging
from uuid import UUID

import procrastinate
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import Connection

logger = logging.getLogger("hausbuch.jobs.enqueue")

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
    the proposal row commits. Locked by `doc:{document_id}` (a double enqueue collapses)."""
    lock = document_lock(document_id)
    deferrer = _DEFER_APP.configure_task(
        TASK_NAME,
        allow_unknown=True,
        queueing_lock=lock,  # one waiting job per document
        lock=lock,  # and never two running at once
        connection=conn.connection.driver_connection,
    )
    try:
        with conn.begin_nested():
            deferrer.defer(
                workspace_id=str(workspace_id),
                document_id=str(document_id),
                proposal_id=str(proposal_id),
            )
    except AlreadyEnqueued:
        fields = {"event": "job_collapsed", "task": TASK_NAME, "document_id": str(document_id)}
        logger.info("document_read already queued", extra={"fields": fields})
