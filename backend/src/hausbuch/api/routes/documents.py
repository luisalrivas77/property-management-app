"""Documents: upload, typing, links, original, retention.

Endpoints: docs/phases/phase-1-step-3.md §4.7. Originals are immutable (principle 6): the
upload goes through upload_document, the download through read_original, which checks the
SHA-256 of what the store returns. There is deliberately no DELETE /documents/{id}: retention
deletion is not exposed in phase 1.
"""

import mimetypes
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
    ErrorKind,
    LinkBody,
    RelatedDocument,
)
from hausbuch.schemas.document import Document, DocumentLink, RetentionResult
from hausbuch.schemas.enums import DocumentSource, EntityType, LinkTarget
from hausbuch.services.context import CommandContext
from hausbuch.services.documents import (
    current_extraction,
    document_media_type,
    document_retention,
    get_document,
    link_document,
    list_documents,
    list_links,
    list_related_documents,
    read_original,
    type_document,
    unlink_document,
    upload_document,
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
    ctx: CommandContext, store: BlobStore, data: bytes, **kwargs: Any
) -> tuple[Document, bool]:
    """upload_document, and whether it registered a new document (dedup returns the
    workspace's existing one for the same bytes, D9)."""
    document = upload_document(ctx, store, data, **kwargs)
    return document, document.created_at == ctx.now


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
    responses={200: {"model": Document, "description": "The bytes were already in the vault"}},
)
async def document_upload(
    file: Annotated[UploadFile, File()],
    cmd: Cmd,
    store: StoreDep,
    settings: SettingsDep,
    response: Response,
    source: Annotated[DocumentSource, Form()] = DocumentSource.UPLOAD,
) -> Document:
    """201 with a new document; 200 with the existing one when these bytes are already in
    the vault. 413 past HAUSBUCH_MAX_UPLOAD_BYTES, 415 outside the allow-list."""
    media_type = upload_media_type(file.content_type, settings.upload_media_types)
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise too_large()
    # The upload route is async (it awaits the spooled file); the transaction and the blob
    # write block, so they run in the threadpool like every sync route.
    document, created = await run_in_threadpool(
        cmd.run,
        _upload,
        store,
        data,
        media_type=media_type,
        source=source,
        original_filename=file.filename or None,
    )
    if not created:
        response.status_code = 200
    return document


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


def _detail(conn: Connection, document_id: UUID) -> DocumentDetail:
    document = found(get_document(conn, document_id), EntityType.DOCUMENT, document_id)
    media_type = found(document_media_type(conn, document_id), EntityType.DOCUMENT, document_id)
    return DocumentDetail(
        document=document,
        links=list_links(conn, document_id),
        extraction=current_extraction(conn, document_id),
        retention=document_retention(conn, document_id),
        media_type=media_type,
        opens_inline=media_type in INLINE_MEDIA_TYPES,
    )


@router.get("/documents/{document_id}")
def document_get(document_id: UUID, cmd: Cmd) -> DocumentDetail:
    """The document, its links, its current extraction and its retention, read in one
    transaction."""
    return cmd.read(_detail, document_id)


@router.put("/documents/{document_id}/typing")
def document_type(document_id: UUID, body: DocumentTypingBody, cmd: Cmd) -> Document:
    return cmd.run(type_document, document_id, body.typing, body.fields)


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
    filename = document.original_filename or _fallback_filename(document, media_type)
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


def _fallback_filename(document: Document, media_type: str) -> str:
    extension = mimetypes.guess_extension(media_type) or ".bin"
    return f"{document.file_sha256[:12]}{extension}"


def content_disposition(kind: str, filename: str) -> str:
    """`kind; filename="<ascii>"; filename*=UTF-8''<percent-encoded>` (RFC 6266 / 5987): the
    ASCII fallback replaces anything outside a safe set, the encoded form keeps the name."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1] or "original"
    ascii_name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
