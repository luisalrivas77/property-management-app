"""The document_read job: privacy gate, classify, extract, hand the result over (§5.1-§5.5).

Plain synchronous code over the engine, independent of Procrastinate (jobs/tasks.py wraps it),
so it is tested with a fake model client and no queue.

Transactions. No transaction is held open across a model call (a call can take minutes):

1. read: the proposal's state, the workspace setting, the original (verified against its
   SHA-256); gates 1 and 2 end the job here;
2. after each model call, its ai_call row is written and committed at once (failed calls
   included), so provenance exists before the result is used and survives a later failure.
   That transaction first reads the proposal row FOR SHARE: a proposal superseded or
   scrubbed meanwhile (a tenant erasure holds the rows FOR UPDATE while it supersedes and
   scrubs them) gets a row without output or raw text, so a read still in flight cannot
   write content back after the erasure (D42 (3)); see _send;
3. the outcome: services.proposals.record_read_result or record_read_failure, in a
   transaction of its own (D20: every one is a workspace_transaction as the app role).

Retries (§5.1). run_document_read never lets a model or database error escape: it raises
RetryLater (the Procrastinate retry strategy turns it into a scheduled retry, 10 s, 60 s,
5 min) while attempts remain, and records the failure on the last attempt. A 4xx from the API
(other than 429/529) is not retried. Inside one attempt, a refusal or max_tokens stop and an
unparsable answer get one immediate retry each (§5.3).

Nothing here writes a domain table (D14), and no log line carries a document value, a model
answer, an exception message or the API key: only ids, codes and exception class names.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select

from hausbuch.ai import documents
from hausbuch.ai.client import ModelCallError, ModelClient, ModelRequest, ModelResponse
from hausbuch.ai.provenance import record_ai_call
from hausbuch.ai.requests import (
    CLASSIFY_PAGES,
    classify_request,
    extract_request,
    parse_classification,
)
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.extraction_parse import TENANT_SUBTYPES, extraction_kind, parse_lines
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue
from hausbuch.schemas.enums import ActorType, ExtractionKind, ProposalStatus
from hausbuch.schemas.extraction import EXTRACTION_MODELS
from hausbuch.services import documents as document_service
from hausbuch.services import proposals
from hausbuch.services.context import Actor, new_command
from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

from .enqueue import TASK_NAME

# Proposal error codes this job produces (phase-2.md §12 names the first six; the last
# three are new here and reported to the spec).
AI_DISABLED = "ai.disabled"
BLOCKED_TENANT_DATA = "ai.blocked_tenant_data"
UNPARSABLE = "ai.unparsable"
REFUSED = "ai.refused"
TOO_MANY_PAGES = documents.TOO_MANY_PAGES
UNSUPPORTED_MEDIA_TYPE = documents.UNSUPPORTED_MEDIA_TYPE
UNREADABLE = documents.UNREADABLE
ADOBE_FORM = documents.ADOBE_FORM  # D43, not retried
INTERNAL_ERROR = "ai.internal_error"  # not a model error: a bug or the database, retried out
# ai.api_error and ai.unavailable come from hausbuch.ai.client.

RETRY_WAITS_S = (10, 60, 300)  # §5.1: three retries
MAX_RETRIES = len(RETRY_WAITS_S)
_RETRY_STOPS = frozenset({"refusal", "max_tokens"})
_PENDING = (ProposalStatus.QUEUED.value, ProposalStatus.RUNNING.value)
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, TASK_NAME)

logger = logging.getLogger("hausbuch.jobs.document_read")


class RetryLater(Exception):
    """Raised to Procrastinate: run this job again in `wait_s` seconds."""

    def __init__(self, wait_s: int) -> None:
        super().__init__(f"retry in {wait_s} s")
        self.wait_s = wait_s


@dataclass(frozen=True)
class JobDeps:
    """What a job needs from the process; built once by jobs.worker.build_worker."""

    engine: Engine
    settings: Settings
    blob_store: BlobStore
    model_client: ModelClient | None  # None when the AI layer is off (gate 1 needs no client)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


class _Stop(Exception):
    """The job ends with record_read_failure(code, ai_call_id)."""

    def __init__(self, code: str, ai_call_id: UUID | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.ai_call_id = ai_call_id


class _CallFailed(Exception):
    """A model call raised; its ai_call row is written."""

    def __init__(self, error: ModelCallError, ai_call_id: UUID) -> None:
        super().__init__(error.error_code)
        self.error = error
        self.ai_call_id = ai_call_id


@dataclass(frozen=True)
class _Original:
    proposal_id: UUID
    media_type: str
    data: bytes
    tenant_documents_allowed: bool
    contains_tenant_data: bool  # gate 2: the row's flag (uploader or an earlier classify)


def run_document_read(
    deps: JobDeps,
    *,
    workspace_id: UUID,
    document_id: UUID,
    proposal_id: UUID,
    attempt: int,
) -> None:
    """One attempt (0 is the first run; Procrastinate's job.attempts). Returns when the
    proposal has its outcome (or there was nothing to do); raises RetryLater otherwise.

    The retry and the failure are raised and recorded outside the except blocks, so no
    exception (whose text could quote SQL parameters or a response) is chained to them."""
    log = {"event": "document_read", "document_id": str(document_id), "attempt": attempt}
    target = proposal_id
    failure: tuple[str, UUID | None] | None = None
    retryable = False
    try:
        original = _load(deps, workspace_id, document_id, proposal_id)
        if original is None:
            logger.info("nothing to read", extra={"fields": log | {"outcome": "skipped"}})
            return
        target = original.proposal_id
        log["proposal_id"] = str(target)
        _read(deps, workspace_id, document_id, original)
    except _Stop as stop:
        failure = (stop.code, stop.ai_call_id)
    except _CallFailed as failed:
        error = failed.error
        log |= {"status": error.status, "request_id": error.request_id}
        failure, retryable = (error.error_code, failed.ai_call_id), error.retryable
    except Exception as exc:  # a bug, the database, the blob store: class name only
        log["exc_type"] = type(exc).__name__
        failure, retryable = (INTERNAL_ERROR, None), True

    if failure is None:
        logger.info("document read", extra={"fields": log | {"outcome": "ready"}})
        return
    code, ai_call_id = failure
    log["error_code"] = code
    if retryable and attempt < MAX_RETRIES:
        wait_s = RETRY_WAITS_S[attempt]
        logger.warning("document read retried", extra={"fields": log | {"retry_in_s": wait_s}})
        raise RetryLater(wait_s)
    _fail(deps, workspace_id, target, code, ai_call_id, attempt + 1, log)


def _fail(
    deps: JobDeps,
    workspace_id: UUID,
    proposal_id: UUID,
    code: str,
    ai_call_id: UUID | None,
    attempts: int,
    fields: dict,
) -> None:
    """record_read_failure; `attempts` counts the runs so far (the first one included)."""
    try:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=deps.clock())
            proposals.record_read_failure(
                ctx, proposal_id, error_code=code, ai_call_id=ai_call_id, attempts=attempts
            )
    except Exception as exc:
        fields = fields | {"record_exc_type": type(exc).__name__}
        logger.error("document read failure not recorded", extra={"fields": fields})
        failed = True
    else:
        failed = False
    if failed:  # outside the except block: nothing chained
        raise JobFailed()
    logger.warning("document not read", extra={"fields": fields})


class JobFailed(Exception):
    """The failure could not be recorded either; Procrastinate marks the job failed and the
    proposal stays pending (logged above with class names only)."""

    def __init__(self) -> None:
        super().__init__("document_read could not record its outcome")


# --- 1. read ------------------------------------------------------------------------


def _load(
    deps: JobDeps, workspace_id: UUID, document_id: UUID, proposal_id: UUID
) -> _Original | None:
    with workspace_transaction(deps.engine, workspace_id) as conn:
        found = _target_proposal(conn, document_id, proposal_id)
        if found is None:
            return None
        target, tenant = found
        allowed = conn.execute(
            select(workspace_table.c.ai_tenant_documents).where(
                workspace_table.c.id == workspace_id
            )
        ).scalar_one()
        if not deps.settings.ai_enabled or deps.model_client is None:
            # Gate 1 needs neither the original nor the model; _read raises _Stop for it.
            return _Original(target, "", b"", allowed, tenant)
        media_type = document_service.document_media_type(conn, document_id)
        data = document_service.read_original(conn, deps.blob_store, document_id)
    return _Original(target, media_type or "", data, allowed, tenant)


def _tenant_documents_allowed(deps: JobDeps, workspace_id: UUID) -> bool:
    """The workspace's D16 setting as it is now (gate 3 re-reads it before extraction)."""
    with workspace_transaction(deps.engine, workspace_id) as conn:
        return conn.execute(
            select(workspace_table.c.ai_tenant_documents).where(
                workspace_table.c.id == workspace_id
            )
        ).scalar_one()


def _target_proposal(conn, document_id: UUID, proposal_id: UUID) -> tuple[UUID, bool] | None:
    """The proposal this run serves, with its tenant-data flag (D16 gate 2, read from the
    row and never from the job's arguments): the given one while it is pending; otherwise the
    document's newest pending one, so a job whose proposal a re-run superseded reads for the
    newer proposal, and the re-run's own job (or any duplicate) then finds nothing pending."""
    columns = (proposal_table.c.id, proposal_table.c.contains_tenant_data)
    row = conn.execute(
        select(*columns).where(
            proposal_table.c.id == proposal_id,
            proposal_table.c.document_id == document_id,
            proposal_table.c.status.in_(_PENDING),
        )
    ).first()
    if row is None:
        row = conn.execute(
            select(*columns)
            .where(
                proposal_table.c.document_id == document_id,
                proposal_table.c.status.in_(_PENDING),
            )
            .order_by(proposal_table.c.created_at.desc(), proposal_table.c.id.desc())
            .limit(1)
        ).first()
    return None if row is None else (row.id, row.contains_tenant_data)


# --- 2. classify and extract ---------------------------------------------------------


def _read(
    deps: JobDeps,
    workspace_id: UUID,
    document_id: UUID,
    original: _Original,
) -> None:
    settings = deps.settings
    if not settings.ai_enabled or deps.model_client is None:
        raise _Stop(AI_DISABLED)  # gate 1
    if original.contains_tenant_data and not original.tenant_documents_allowed:
        raise _Stop(BLOCKED_TENANT_DATA)  # gate 2
    try:
        documents.check_media_type(original.media_type)
        pages = documents.page_count(original.data) if original.media_type == documents.PDF else 1
        if original.media_type == documents.PDF and documents.is_adobe_only_form(original.data):
            raise _Stop(ADOBE_FORM)  # D43: the model would see only Adobe's placeholder page
        first = (
            documents.pdf_pages(original.data, 0, min(CLASSIFY_PAGES, pages))
            if original.media_type == documents.PDF
            else original.data
        )
    except documents.DocumentInputError as exc:
        raise _Stop(exc.code) from None

    block = documents.document_block(first, original.media_type, citations=False)
    classification, classify_id = _classify(
        deps, workspace_id, document_id, original.proposal_id, classify_request(settings, block)
    )

    tenant = classification.contains_tenant_data or classification.subtype in TENANT_SUBTYPES
    if tenant and not original.tenant_documents_allowed:  # gate 3
        _finish(deps, workspace_id, original.proposal_id, classification, classify_id)
        return
    # The setting was read before the classify call, which can take minutes: read it again,
    # so a workspace that has just turned tenant documents off sends none to extraction.
    if (tenant or original.contains_tenant_data) and not _tenant_documents_allowed(
        deps, workspace_id
    ):
        _finish(deps, workspace_id, original.proposal_id, classification, classify_id)
        return
    kind = extraction_kind(classification.type, classification.subtype)
    if kind is None:  # typing-only: no per-type extraction for this type
        _finish(deps, workspace_id, original.proposal_id, classification, classify_id, ok=True)
        return

    values, extract_id = _extract(deps, workspace_id, document_id, original, pages, kind)
    _finish(
        deps,
        workspace_id,
        original.proposal_id,
        classification,
        classify_id,
        ok=True,
        values=values,
        extract_id=extract_id,
        model=settings.ai_model_extract,
    )


def _classify(
    deps: JobDeps,
    workspace_id: UUID,
    document_id: UUID,
    proposal_id: UUID,
    request: ModelRequest,
) -> tuple[ClassifyOutput, UUID]:
    def parsed(response: ModelResponse) -> _Parsed:
        try:
            classification = parse_classification(response.text)
        except ValueError:
            return _Parsed(None, None, UNPARSABLE)
        return _Parsed(classification, classification.model_dump(mode="json"), None)

    return _with_one_retry(deps, workspace_id, document_id, proposal_id, request, parsed)


def _extract(
    deps: JobDeps,
    workspace_id: UUID,
    document_id: UUID,
    original: _Original,
    pages: int,
    kind: ExtractionKind,
) -> tuple[dict[str, ExtractedValue], UUID]:
    """All fields of `kind`, merged over the chunks of a long PDF. The first chunk's
    ai_call id is the one handed over (record_read_result takes one; the others are in
    ai_call under the same document)."""
    is_pdf = original.media_type == documents.PDF
    ranges = documents.chunks(pages, deps.settings.ai_max_pages) if is_pdf else [(0, 1)]
    merged: dict[str, ExtractedValue] = {}
    first_id: UUID | None = None
    for start, stop in ranges:
        try:
            data = documents.pdf_pages(original.data, start, stop) if is_pdf else original.data
        except documents.DocumentInputError as exc:
            raise _Stop(exc.code, first_id) from None
        block = documents.document_block(data, original.media_type, citations=True)
        request = extract_request(deps.settings, kind, block)
        values, call_id = _extract_chunk(
            deps, workspace_id, document_id, original.proposal_id, request, kind, start
        )
        first_id = first_id or call_id
        merge_values(merged, values)
    assert first_id is not None
    fields = EXTRACTION_MODELS[kind].model_fields
    return {name: merged.get(name, ExtractedValue.missing()) for name in fields}, first_id


def _extract_chunk(
    deps: JobDeps,
    workspace_id: UUID,
    document_id: UUID,
    proposal_id: UUID,
    request: ModelRequest,
    kind: ExtractionKind,
    page_offset: int,
) -> tuple[dict[str, ExtractedValue], UUID]:
    def parsed(response: ModelResponse) -> _Parsed:
        values = parse_lines(response.blocks, kind, page_offset=page_offset)
        if not values:  # no line keyed by a field of this kind
            return _Parsed(None, None, UNPARSABLE)
        output = {key: value.model_dump(mode="json") for key, value in values.items()}
        return _Parsed(values, output, None)

    return _with_one_retry(deps, workspace_id, document_id, proposal_id, request, parsed)


_RANK = {"low": 0, "medium": 1, "high": 2}


def merge_values(merged: dict[str, ExtractedValue], values: dict[str, ExtractedValue]) -> None:
    """A stated value replaces a missing one; a later chunk's value replaces an earlier
    stated one only with strictly higher confidence (the first statement wins ties)."""
    for key, value in values.items():
        current = merged.get(key)
        first_statement = current is None or current.value is None
        better = (
            current is not None
            and value.value is not None
            and _RANK[value.confidence] > _RANK[current.confidence]
        )
        if first_statement or better:
            merged[key] = value


@dataclass(frozen=True)
class _Parsed:
    result: Any  # what the job uses, None when the answer is unusable
    output: Any  # ai_call.output (JSON)
    error_code: str | None


def _with_one_retry(
    deps: JobDeps,
    workspace_id: UUID,
    document_id: UUID,
    proposal_id: UUID,
    request: ModelRequest,
    parse: Callable[[ModelResponse], _Parsed],
) -> tuple[Any, UUID]:
    """Send, record, parse; a refusal / max_tokens stop or an unparsable answer is sent once
    more (§5.3), and a second one ends the job with its code."""
    parsed = _Parsed(None, None, UNPARSABLE)
    call_id: UUID | None = None
    for _ in range(2):
        parsed, call_id = _send(deps, workspace_id, document_id, proposal_id, request, parse)
        if parsed.error_code is None:
            return parsed.result, call_id
    assert parsed.error_code is not None
    raise _Stop(parsed.error_code, call_id)


def _send(
    deps: JobDeps,
    workspace_id: UUID,
    document_id: UUID,
    proposal_id: UUID,
    request: ModelRequest,
    parse: Callable[[ModelResponse], _Parsed],
) -> tuple[_Parsed, UUID]:
    """One model call and its ai_call row, committed before the response is used.

    The row's transaction reads the proposal FOR SHARE first. A tenant erasure locks the
    document's proposals FOR UPDATE, supersedes and scrubs them and nulls the content of
    their documents' ai_call rows in one transaction, so the two serialise: either this row
    commits first and the erasure's UPDATE (a later statement, read committed) nulls it, or
    the erasure commits first and this row is stored without output and raw text (model,
    tokens and hashes stay: provenance without content). A proposal superseded by a re-run
    is treated the same way; its result is not used (record_read_result is a no-op)."""
    assert deps.model_client is not None
    try:
        response = deps.model_client.send(request)
    except ModelCallError as error:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            call_id = record_ai_call(
                conn,
                workspace_id=workspace_id,
                request=request,
                document_id=document_id,
                failure=error,
            )
        raise _CallFailed(error, call_id) from None
    if response.stop_reason in _RETRY_STOPS:
        parsed = _Parsed(None, None, REFUSED)
    else:
        parsed = parse(response)
    with workspace_transaction(deps.engine, workspace_id) as conn:
        keep_content = _still_served(conn, proposal_id)
        call_id = record_ai_call(
            conn,
            workspace_id=workspace_id,
            request=request,
            document_id=document_id,
            response=response,
            output=parsed.output if keep_content else None,
            error_code=parsed.error_code,
            keep_raw_text=keep_content,
        )
    return parsed, call_id


def _still_served(conn, proposal_id: UUID) -> bool:
    """False when the proposal is superseded or scrubbed (or gone); locks it FOR SHARE."""
    p = proposal_table
    row = conn.execute(
        select(p.c.status, p.c.scrubbed_at).where(p.c.id == proposal_id).with_for_update(read=True)
    ).first()
    return (
        row is not None
        and row.status != ProposalStatus.SUPERSEDED.value
        and row.scrubbed_at is None
    )


# --- 3. the outcome --------------------------------------------------------------------


def _finish(
    deps: JobDeps,
    workspace_id: UUID,
    proposal_id: UUID,
    classification: ClassifyOutput,
    classify_id: UUID,
    *,
    ok: bool = False,
    values: dict[str, ExtractedValue] | None = None,
    extract_id: UUID | None = None,
    model: str | None = None,
) -> None:
    """record_read_result: `ok` False is the gate 3 stop (blocked_tenant_data)."""
    actor = Actor(ActorType.AI, model or deps.settings.ai_model_classify)
    with workspace_transaction(deps.engine, workspace_id) as conn:
        ctx = new_command(conn, workspace_id, actor, now=deps.clock())
        proposals.record_read_result(
            ctx,
            proposal_id,
            classification=classification,
            classify_call_id=classify_id,
            values=values,
            extract_call_id=extract_id,
            blocked_tenant_data=not ok,
        )
