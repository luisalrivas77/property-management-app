"""Enqueue functions the request path calls (phase 2 spec §5.1).

Seam between the step 1 chunks, written by the orchestrator: the worker chunk owns this file
and replaces the body; the proposals chunk only calls it. Keep the signature.
"""

from uuid import UUID

from sqlalchemy import Connection


def enqueue_document_read(
    conn: Connection, *, workspace_id: UUID, document_id: UUID, proposal_id: UUID
) -> None:
    """Queue `document_read` in the caller's transaction, so the job exists if and only if
    the proposal row commits. Locked by `doc:{document_id}` (a double enqueue collapses)."""
    raise NotImplementedError("step 1 worker chunk")
