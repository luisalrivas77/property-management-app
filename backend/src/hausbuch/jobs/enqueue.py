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
from sqlalchemy import Connection, text

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
REMATCH_WAITING_TASK = "rematch_waiting"


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


# --- rematch_waiting (§8.1: a repeat answer is never asked twice) -------------------------
#
# Something the deterministic order reads has changed, so lines already waiting in the inbox
# (no rows, or a proposed set) may now be booked: a learned rule, a loan (its account, lender
# or schedule terms), a tenant's IBAN, a tenancy's parties or rent rows, an owner account.
# The payload names the trigger as "<kind>:<entity id>", never an IBAN or its hash; the job
# reads the entity and narrows the lines from it (services.matching.waiting_lines).

TRIGGER_RULE = "rule"
TRIGGER_LOAN = "loan"
TRIGGER_TENANT = "tenant"
TRIGGER_TENANCY = "tenancy"
TRIGGER_ACCOUNT = "account"
TRIGGER_KINDS = frozenset(
    {TRIGGER_RULE, TRIGGER_LOAN, TRIGGER_TENANT, TRIGGER_TENANCY, TRIGGER_ACCOUNT}
)


def waiting_trigger(kind: str, entity_id: UUID) -> str:
    if kind not in TRIGGER_KINDS:
        raise ValueError(f"unknown rematch trigger kind {kind!r}")
    return f"{kind}:{entity_id}"


def waiting_lock(trigger: str) -> str:
    return f"waiting:{trigger}"


def enqueue_rematch_waiting(
    conn: Connection, *, workspace_id: UUID, kind: str, entity_id: UUID
) -> None:
    """Queue `rematch_waiting` for this change, in the caller's transaction, unless a job for
    the same trigger is already waiting to run: that job reads the entity when it runs, so it
    covers this change too, and a burst of edits (a pre-fill applying a loan field by field,
    a tenancy with its rent rows) queues one job, not one per edit. A job already running
    may have read the old state, so a new one is queued behind it (same lock). The lookup
    holds the waiting job FOR KEY SHARE until the command commits: Procrastinate's fetch
    takes jobs FOR UPDATE SKIP LOCKED, so the worker cannot start that job between this
    lookup and the commit and read the entity as it was before this change (review F1,
    7 Nov 2026; the window is milliseconds and no test can reproduce it).

    The dedupe is a lookup, not Procrastinate's queueing_lock: that lock is a unique index
    over waiting jobs, so a duplicate defer raises inside the caller's transaction (aborting
    the command), and a retry of a running job while another waits violates it and leaves
    the retried job stuck (see this module's docstring). Two commands racing past the lookup
    queue two jobs; that is harmless, since a line booked by the first is no longer waiting.

    Locked by `waiting:<kind>:<id>`: two jobs for one trigger never run at once; jobs for
    different triggers may, each line serialised by match_line's row lock."""
    trigger = waiting_trigger(kind, entity_id)
    lock = waiting_lock(trigger)
    queued = conn.execute(
        text(
            "SELECT 1 FROM procrastinate_jobs WHERE lock = :lock AND status = 'todo'"
            " LIMIT 1 FOR KEY SHARE"
        ),
        {"lock": lock},
    ).first()
    if queued is not None:
        return
    _defer(conn, REMATCH_WAITING_TASK, lock, workspace_id=str(workspace_id), trigger=trigger)


# --- Phase 3 step 3: tax exports (§10, §12, D65) ------------------------------------------
#
# Same pattern: inserted in the request's transaction, a lock and no queueing lock. The export
# row does not exist until the job writes it (tax_export is immutable and needs the file), so
# the job's arguments carry what to build; GET /tax-exports/{id} reads the job's status by its
# lock while no row exists. A duplicate job finds the row and writes nothing.

TAX_EXPORT_TASK = "tax_export"


def export_lock(export_id: UUID) -> str:
    return f"export:{export_id}"


def enqueue_tax_export(
    conn: Connection,
    *,
    workspace_id: UUID,
    export_id: UUID,
    property_id: UUID,
    tax_year: int,
    kind: str,
    created_by: UUID | None,
) -> None:
    """Queue `tax_export` for one export, in the caller's transaction. Locked by
    `export:{id}`. created_by is "" for an export no person asked for."""
    _defer(
        conn,
        TAX_EXPORT_TASK,
        export_lock(export_id),
        workspace_id=str(workspace_id),
        export_id=str(export_id),
        property_id=str(property_id),
        tax_year=str(tax_year),
        kind=kind,
        created_by="" if created_by is None else str(created_by),
    )
