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
