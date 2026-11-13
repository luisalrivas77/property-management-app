"""Documents: upload, typing, links, original, retention.

Endpoints: docs/phases/phase-1-step-3.md §4.7. Originals are immutable (principle 6): the
upload goes through upload_document, the download through read_original, which checks the
SHA-256 of what the store returns. There is deliberately no DELETE /documents/{id}: retention
deletion is not exposed in phase 1.
"""

import re
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import File, Form, Query, Response, UploadFile
from sqlalchemy import Connection
from starlette.concurrency import run_in_threadpool

from hausbuch.schemas.api import (
    DocumentDetail,
    DocumentTypingBody,
    DocumentUploadResult,
    ErrorKind,
    LinkBody,
    PaymentProofBody,
    RelatedDocument,
)
from hausbuch.schemas.bank import (
    ImportStoredStatementBody,
    StatementAccountBody,
    StatementAccountCreated,
    StatementImport,
)
from hausbuch.schemas.document import Document, DocumentLink, RetentionResult
from hausbuch.schemas.enums import DocumentSource, EntityType, LinkTarget
from hausbuch.schemas.proposal import Proposal
from hausbuch.services.accounts import create_account_from_statement
from hausbuch.services.context import CommandContext
from hausbuch.services.depot_explain import document_explanation
from hausbuch.services.documents import (
    attach_payment_proof,
    current_extraction,
    document_media_type,
    document_retention,
    download_filename,
    get_document,
    hand_fields,
    link_document,
    list_documents,
    list_links,
    list_related_documents,
    read_original,
    type_document,
    unlink_document,
    upload_document,
)
from hausbuch.services.proposals import attach_stored_proof, proposal_for_upload
from hausbuch.services.statements import (
    document_statement_imports,
    import_stored_statement,
    statement_offer,
)
from hausbuch.storage import BlobStore

from ..deps import Cmd, SettingsDep, StoreDep, domain_router, found, require
from ..errors import ApiError
from ..limits import too_large

router = domain_router()

# The service's (and the document_file CHECK's) media type form.
_MEDIA_TYPE = re.compile(r"[a-z]+/[a-z0-9.+-]+")
# Rendered by the browser from the API origin only if they cannot carry script (§4.7).
INLINE_MEDIA_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png", "image/webp"})
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


def linked_documents(
    target_type: LinkTarget, getter: Callable[[Connection, UUID], Any]
) -> Callable[[Connection, UUID], list[Document]]:
    """A read: the documents linked to one target, 404 if the target does not exist."""

    def query(conn: Connection, target_id: UUID) -> list[Document]:
        require(conn, getter, EntityType(target_type.value), target_id)
        return list_documents(conn, target_type=target_type, target_id=target_id)

    return query


def related_documents(
    target_type: LinkTarget, getter: Callable[[Connection, UUID], Any]
) -> Callable[[Connection, UUID], list[RelatedDocument]]:
    """A read: the documents of a property or unit rolled up from its children (units,
    tenancies, loans), 404 if the target does not exist."""

    def query(conn: Connection, target_id: UUID) -> list[RelatedDocument]:
        require(conn, getter, EntityType(target_type.value), target_id)
        return list_related_documents(conn, target_type=target_type, target_id=target_id)

    return query


# --- Upload --------------------------------------------------------------------------


def _upload(
    ctx: CommandContext,
    store: BlobStore,
    data: bytes,
    *,
    ai: bool,
    contains_tenant_data: bool,
    proof_for: UUID | None = None,
    **kwargs: Any,
) -> tuple[Document, bool, Proposal | None]:
    """upload_document, whether it registered a new document (dedup returns the
    workspace's existing one for the same bytes, D9), and with `ai` the proposal to poll,
    queued (and its job enqueued) in the same transaction (§6.1). With `proof_for`, the
    document is that bill's payment proof (phase 3 D143), typed and linked before the read
    is queued."""
    document = upload_document(ctx, store, data, **kwargs)
    created = document.created_at == ctx.now
    if proof_for is not None:
        document = attach_payment_proof(ctx, document.id, proof_for)
    proposal = None
    if ai:
        proposal = proposal_for_upload(
            ctx, document.id, created=created, contains_tenant_data=contains_tenant_data
        )
    return document, created, proposal


def upload_media_type(declared: str | None, allowed: frozenset[str]) -> str:
    """The part's declared content type, lower-cased and without parameters; 415 unless it
    is on the allow-list. The declared type is trusted after that (sniffing is phase 2)."""
    media_type = (declared or "").split(";", 1)[0].strip().lower()
    if not _MEDIA_TYPE.fullmatch(media_type) or media_type not in allowed:
        raise ApiError(
            415,
            ErrorKind.UNSUPPORTED_MEDIA_TYPE,
            f"accepted media types: {', '.join(sorted(allowed))}",
        )
    return media_type


@router.post(
    "/documents",
    status_code=201,
    responses={200: {"description": "The bytes were already in the vault"}},
)
async def document_upload(
    file: Annotated[UploadFile, File()],
    cmd: Cmd,
    store: StoreDep,
    settings: SettingsDep,
    response: Response,
    source: Annotated[DocumentSource, Form()] = DocumentSource.UPLOAD,
    ai: Annotated[bool | None, Form()] = None,
    contains_tenant_data: Annotated[bool, Form()] = False,
    proof_for: Annotated[UUID | None, Form()] = None,
) -> Document | DocumentUploadResult:
    """201 with a new document; 200 with the existing one when these bytes are already in
    the vault. 413 past HAUSBUCH_MAX_UPLOAD_BYTES, 415 outside the allow-list.

    `ai` (default HAUSBUCH_AI_ENABLED) also queues a pre-fill proposal and answers with a
    DocumentUploadResult (the document plus the proposal to poll, §6.1); without it the
    answer is the Document, as in phase 1. For bytes already in the vault the document's
    latest proposal is returned instead of a new read. `contains_tenant_data` with the
    workspace's ai_tenant_documents off stores the proposal blocked, unread (D16).

    `proof_for` (phase 3 D143, the to-do „Zahlungsnachweis hochladen“): the id of the bill
    this file proves was paid. The document is typed other / payment_proof by code and
    linked to the bill and its property in the same transaction, and its reading skips the
    classifier. 404 for an unknown bill, 422 when the file is the bill itself or is stored
    as another kind of document."""
    media_type = upload_media_type(file.content_type, settings.upload_media_types)
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise too_large()
    use_ai = settings.ai_enabled if ai is None else ai
    # The upload route is async (it awaits the spooled file); the transaction and the blob
    # write block, so they run in the threadpool like every sync route.
    document, created, proposal = await run_in_threadpool(
        cmd.run,
        _upload,
        store,
        data,
        ai=use_ai,
        contains_tenant_data=contains_tenant_data,
        proof_for=proof_for,
        media_type=media_type,
        source=source,
        original_filename=file.filename or None,
    )
    if not created:
        response.status_code = 200
    if proposal is None:
        return document
    return DocumentUploadResult(
        document=document, proposal_id=proposal.id, proposal_status=proposal.status
    )


# --- Reads -----------------------------------------------------------------------------


@router.get("/documents")
def documents_list(
    cmd: Cmd,
    target_type: LinkTarget | None = None,
    target_id: UUID | None = None,
) -> list[Document]:
    """Oldest first; optionally only those linked to a target type, or to one target
    (target_id needs target_type, else 422)."""
    return cmd.read(list_documents, target_type=target_type, target_id=target_id)


def _detail(
    conn: Connection, document_id: UUID, store: BlobStore, iban_key: bytes
) -> DocumentDetail:
    document = found(get_document(conn, document_id), EntityType.DOCUMENT, document_id)
    media_type = found(document_media_type(conn, document_id), EntityType.DOCUMENT, document_id)
    extraction = current_extraction(conn, document_id)
    return DocumentDetail(
        document=document,
        links=list_links(conn, document_id),
        extraction=extraction,
        hand_fields=hand_fields(extraction),
        retention=document_retention(conn, document_id),
        media_type=media_type,
        opens_inline=media_type in INLINE_MEDIA_TYPES,
        explanation=document_explanation(conn, document_id),
        statement_imports=document_statement_imports(conn, document_id),
        statement_offer=statement_offer(conn, store, document_id, iban_key=iban_key),
    )


@router.get("/documents/{document_id}")
def document_get(
    document_id: UUID, cmd: Cmd, store: StoreDep, settings: SettingsDep
) -> DocumentDetail:
    """The document, its links, its current extraction and its retention, read in one
    transaction; the statement imports it is the original of, and for a card or depot
    statement whose read was refused and which no account holds yet, the accounts
    „Auf Konto importieren“ offers (phase 6 D176 (3), step 4)."""
    return cmd.read(_detail, document_id, store, settings.iban_hmac_key)


@router.put("/documents/{document_id}/typing")
def document_type(document_id: UUID, body: DocumentTypingBody, cmd: Cmd) -> Document:
    """Over an AI reading, `fields` are written into the reading's own kind (D37); 422
    document.extraction_kind_mismatch for a non-null field that kind has no place for, 422
    document.extraction_stale when `shown_version` is no longer the current version."""
    return cmd.run(
        type_document, document_id, body.typing, body.fields, shown_version=body.shown_version
    )


def _links(conn: Connection, document_id: UUID) -> list[DocumentLink]:
    require(conn, get_document, EntityType.DOCUMENT, document_id)
    return list_links(conn, document_id)


@router.get("/documents/{document_id}/links")
def document_links(document_id: UUID, cmd: Cmd) -> list[DocumentLink]:
    return cmd.read(_links, document_id)


@router.post("/documents/{document_id}/links", status_code=201)
def document_link(document_id: UUID, body: LinkBody, cmd: Cmd) -> DocumentLink:
    """409 when already linked to this target; 404 when the target does not exist."""
    return cmd.run(link_document, document_id, body.target_type, body.target_id)


@router.post("/documents/{document_id}/payment-proof")
def document_payment_proof(document_id: UUID, body: PaymentProofBody, cmd: Cmd) -> Document:
    """Phase 3 D149, the to-do „Vorhandenen Nachweis zuordnen“: this document, a payment proof
    already stored, pays the bill `bill_id`. Linked as the upload from the to-do links it
    (D143): to the bill and the bill's properties; a link that exists is kept. When the
    proof's reading shows the bill's privately paid rows were paid in another year, a
    suggestion to move them waits on the proof's page (review F1, nothing moved yet). 404 for
    an unknown document or bill, 422 when it is the bill itself, is not typed as a payment
    proof (an untyped document too)."""
    return cmd.run(attach_stored_proof, document_id, body.bill_id)


def _import_stored(
    ctx: CommandContext, store: BlobStore, document_id: UUID, account_id: UUID, iban_key: bytes
) -> tuple[StatementImport, bool]:
    """import_stored_statement, and whether it created the import."""
    imported = import_stored_statement(ctx, store, document_id, account_id, iban_key=iban_key)
    return imported, imported.created_at == ctx.now


@router.post(
    "/documents/{document_id}/import-statement",
    status_code=201,
    responses={200: {"model": StatementImport, "description": "Already imported on this account"}},
)
async def document_import_statement(
    document_id: UUID,
    body: ImportStoredStatementBody,
    cmd: Cmd,
    store: StoreDep,
    settings: SettingsDep,
    response: Response,
) -> StatementImport:
    """„Auf Konto importieren“ (phase 6 D176 (3), step 4): the stored document (a card or
    depot statement uploaded through Dokumente) imported as a statement of `account_id` from
    its original, exactly as POST /accounts/{id}/imports would import the same file. 201 with
    the new import; 200 with the existing one when this document is already imported on
    that account (idempotent); 404 for an unknown document or account; 422 rule_violation with
    a statement.* code when the file is not this account's (statement.account_mismatch) or
    cannot be imported (nothing is written then)."""
    imported, created = await run_in_threadpool(
        cmd.run, _import_stored, store, document_id, body.account_id, settings.iban_hmac_key
    )
    if not created:
        response.status_code = 200
    return imported


@router.post("/documents/{document_id}/statement-account", status_code=201)
async def document_statement_account(
    document_id: UUID,
    body: StatementAccountBody,
    cmd: Cmd,
    store: StoreDep,
    settings: SettingsDep,
) -> StatementAccountCreated:
    """„Konto anlegen und einlesen“ (phase 6, 13 Nov 2026, amends D176 (3) and D165 (b)): for
    a statement file stored through Dokumente with no account for it yet (its
    `statement_offer.new_account`), the account is created from the file and the file imported
    onto it, in one transaction. The kind, the IBAN or number and the opening balance are read
    from the stored original on the server; the body carries only what the person sets (label,
    bank, provider, purpose, owner shares). 201 with the account and the import; 404 for an
    unknown document; 409 when an account already holds the file's IBAN or number (import onto
    it with „Auf Konto importieren“); 422 rule_violation statement.no_account_offer for a
    document without such an offer, or another statement.* code when the import refuses the
    file (nothing is written then)."""
    return await run_in_threadpool(
        cmd.run,
        create_account_from_statement,
        store,
        document_id,
        body,
        iban_key=settings.iban_hmac_key,
    )


@router.delete("/documents/{document_id}/links/{target_type}/{target_id}", status_code=204)
def document_unlink(
    document_id: UUID, target_type: LinkTarget, target_id: UUID, cmd: Cmd
) -> Response:
    cmd.run(unlink_document, document_id, target_type, target_id)
    return Response(status_code=204)


@router.get("/documents/{document_id}/retention")
def document_retention_get(document_id: UUID, cmd: Cmd) -> RetentionResult:
    return cmd.read(_retention, document_id)


def _retention(conn: Connection, document_id: UUID) -> RetentionResult:
    require(conn, get_document, EntityType.DOCUMENT, document_id)
    return document_retention(conn, document_id)


# --- Original ----------------------------------------------------------------------


def _original(conn: Connection, store: BlobStore, document_id: UUID) -> tuple[bytes, str, Document]:
    """The verified bytes, their media type (document_file.media_type, the same source as
    DocumentDetail.media_type) and the document, in one read."""
    document = found(get_document(conn, document_id), EntityType.DOCUMENT, document_id)
    data = read_original(conn, store, document_id)
    media_type = found(document_media_type(conn, document_id), EntityType.DOCUMENT, document_id)
    return data, media_type, document


@router.get(
    "/documents/{document_id}/original",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The bytes"}},
)
def document_original(
    document_id: UUID,
    cmd: Cmd,
    store: StoreDep,
    disposition: Annotated[str, Query(pattern="^(inline|attachment)$")] = "attachment",
) -> Response:
    """The original bytes, SHA-256 verified. `inline` is honoured only for PDF and the
    raster image types; everything else is always an attachment, so nothing script-capable
    is ever rendered from the API's origin."""
    data, media_type, document = cmd.read(_original, store, document_id)
    inline = disposition == "inline" and media_type in INLINE_MEDIA_TYPES
    filename = download_filename(document, media_type)
    return Response(
        content=data,
        headers={
            "Content-Type": media_type,
            "Content-Disposition": content_disposition(
                "inline" if inline else "attachment", filename
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def content_disposition(kind: str, filename: str) -> str:
    """`kind; filename="<ascii>"; filename*=UTF-8''<percent-encoded>` (RFC 6266 / 5987): the
    ASCII fallback replaces anything outside a safe set, the encoded form keeps the name."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1] or "original"
    ascii_name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
