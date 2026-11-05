"""Document vault commands: content-addressed originals, typing, versioned extractions, links.

Principle 6: the original bytes are immutable. document_file is never updated (the migration's
trigger and grants enforce it), the blob store never overwrites a key, and read_original checks
the SHA-256 of what comes back. Typing and extracted fields are layers on top: document holds
the current typing, document_extraction an append-only version per change (D9, phase-1.md).
"""

import hashlib
import re
from datetime import UTC
from uuid import UUID

from sqlalchemy import Connection, delete, exists, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import tenancy_tenant as tenancy_tenant_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.rules import retention
from hausbuch.schemas.document import (
    Document,
    DocumentExtraction,
    DocumentLink,
    DocumentRetentionInput,
    DocumentTyping,
    ManualDocumentFields,
    RetentionPolicy,
    RetentionResult,
)
from hausbuch.schemas.enums import DocumentSource, EntityType, ExtractionSource, LinkTarget
from hausbuch.schemas.events import DocumentLinked, DocumentTyped, DocumentUploaded, EventType
from hausbuch.storage import BlobStore, blob_key

from .context import CommandContext
from .errors import NotFoundError
from .events import append_event

# The document_file.media_type CHECK, so a bad value is refused before any SQL.
_MEDIA_TYPE = re.compile(r"[a-z]+/[a-z0-9.+-]+")
_FOREIGN_KEY_VIOLATION = "23503"
_UNIQUE_VIOLATION = "23505"
_DEFAULT_POLICY = RetentionPolicy()  # frozen, so safe to share as a default


class InvalidUploadError(ValueError):
    """The upload is empty or its media type is not storable; nothing was written."""


class LinkTargetNotFoundError(NotFoundError):
    """The link target does not exist in this workspace (missing, or another workspace's)."""


class DuplicateLinkError(ValueError):
    """The document is already linked to this target."""

    def __init__(self, document_id: UUID, target_type: LinkTarget, target_id: UUID) -> None:
        super().__init__(f"document {document_id} is already linked to {target_type} {target_id}")


class LinkNotFoundError(LookupError):
    """There is no link between the document and this target."""

    def __init__(self, document_id: UUID, target_type: LinkTarget, target_id: UUID) -> None:
        super().__init__(f"document {document_id} is not linked to {target_type} {target_id}")


class OriginalIntegrityError(RuntimeError):
    """The blob store returned bytes whose SHA-256 differs from the recorded one."""


# --- Commands ----------------------------------------------------------------


def upload_document(
    ctx: CommandContext,
    store: BlobStore,
    data: bytes,
    *,
    media_type: str,
    source: DocumentSource,
    original_filename: str | None = None,
) -> Document:
    """Store an original and register it, or return the workspace's existing document for the
    same bytes (one document per file per workspace, D9); a re-upload writes nothing."""
    if not data:
        raise InvalidUploadError("an upload must not be empty")
    if not isinstance(media_type, str) or not _MEDIA_TYPE.fullmatch(media_type):
        raise InvalidUploadError(f"media type {media_type!r} is not a lowercase type/subtype")
    source = DocumentSource(source)

    digest = hashlib.sha256(data)
    sha_bytes, sha_hex = digest.digest(), digest.hexdigest()
    existing = _select_document_by_sha(ctx.conn, sha_bytes)
    if existing is not None:
        return existing

    key = blob_key(ctx.workspace_id, sha_hex)
    # Blob first, rows second. If the transaction later rolls back, the blob stays behind
    # unreferenced; that is harmless: the key is content-addressed, so a retry reuses it.
    store.put(key, data, media_type)

    # ON CONFLICT DO NOTHING: a concurrent upload of the same bytes waits for the other
    # transaction, then finds its rows instead of failing on the unique keys.
    ctx.conn.execute(
        pg_insert(file_table)
        .values(
            workspace_id=ctx.workspace_id,
            sha256=sha_bytes,
            size_bytes=len(data),
            media_type=media_type,
            storage_key=key,
            created_at=ctx.now,
        )
        .on_conflict_do_nothing()
    )
    row = ctx.conn.execute(
        pg_insert(document_table)
        .values(
            workspace_id=ctx.workspace_id,
            file_sha256=sha_bytes,
            source=source.value,
            original_filename=original_filename,
            received_at=ctx.now,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .on_conflict_do_nothing()
        .returning(*document_table.c)
    ).one_or_none()
    if row is None:  # lost the race: the other upload registered it and wrote the event
        return _select_document_by_sha(ctx.conn, sha_bytes)

    uploaded = _to_document(row)
    append_event(
        ctx,
        EventType.DOCUMENT_UPLOADED,
        uploaded.id,
        DocumentUploaded(
            sha256=sha_hex, media_type=media_type, size_bytes=len(data), source=source
        ),
    )
    return uploaded


def type_document(
    ctx: CommandContext,
    document_id: UUID,
    typing: DocumentTyping,
    fields: ManualDocumentFields | None = None,
) -> Document:
    """Set what the document is and, optionally, its typed fields.

    Fields that differ from the current extraction (or the first ones) become the next
    extraction version. Writes nothing and appends no event if neither typing nor fields change.
    """
    current = _select_document(ctx.conn, document_id, for_update=True)
    if current is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)

    version = None
    if fields is not None:
        # The document row is locked (FOR UPDATE above), so concurrent typings of the same
        # document serialize here and cannot both compute the same next version.
        latest = current_extraction(ctx.conn, document_id)
        if latest is None or latest.fields != fields:
            version = (latest.version if latest else 0) + 1
            ctx.conn.execute(
                insert(extraction_table).values(
                    workspace_id=ctx.workspace_id,
                    document_id=document_id,
                    version=version,
                    source=ExtractionSource.MANUAL.value,
                    fields=fields.model_dump(mode="json"),
                    created_at=ctx.now,
                )
            )
    if current.typing == typing and version is None:
        return current

    row = ctx.conn.execute(
        update(document_table)
        .where(document_table.c.id == document_id)
        .values(
            type=typing.type.value,
            subtype=None if typing.subtype is None else typing.subtype.value,
            title=typing.title,
            document_date=typing.document_date,
            tax_year=typing.tax_year,
            updated_at=ctx.now,
        )
        .returning(*document_table.c)
    ).one()
    typed = _to_document(row)
    append_event(
        ctx,
        EventType.DOCUMENT_TYPED,
        document_id,
        DocumentTyped(
            type=typing.type,
            subtype=typing.subtype,
            tax_year=typing.tax_year,
            extraction_version=version,
        ),
    )
    return typed


def link_document(
    ctx: CommandContext, document_id: UUID, target_type: LinkTarget, target_id: UUID
) -> DocumentLink:
    """Link a document to the entity it proves."""
    target_type = LinkTarget(target_type)
    if _select_document(ctx.conn, document_id, for_update=False) is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
    try:
        # A savepoint, so a refused link leaves the caller's transaction usable.
        with ctx.conn.begin_nested():
            row = ctx.conn.execute(
                insert(link_table)
                .values(
                    workspace_id=ctx.workspace_id,
                    document_id=document_id,
                    created_at=ctx.now,
                    **{_link_column(target_type).name: target_id},
                )
                .returning(*link_table.c)
            ).one()
    except IntegrityError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""
        if sqlstate == _UNIQUE_VIOLATION:
            raise DuplicateLinkError(document_id, target_type, target_id) from None
        if sqlstate == _FOREIGN_KEY_VIOLATION and not constraint.endswith("document_id_fkey"):
            # The composite FK (workspace_id, target) rejects a missing id and another
            # workspace's id alike.
            raise LinkTargetNotFoundError(EntityType(target_type.value), target_id) from None
        raise
    link = _to_link(row)
    append_event(
        ctx,
        EventType.DOCUMENT_LINKED,
        document_id,
        DocumentLinked(target_type=target_type, target_id=target_id),
    )
    return link


def unlink_document(
    ctx: CommandContext, document_id: UUID, target_type: LinkTarget, target_id: UUID
) -> None:
    """Remove a link. The document.linked event stays; document.unlinked records the removal."""
    target_type = LinkTarget(target_type)
    removed = ctx.conn.execute(
        delete(link_table)
        .where(link_table.c.document_id == document_id)
        .where(_link_column(target_type) == target_id)
        .returning(link_table.c.id)
    ).one_or_none()
    if removed is None:
        raise LinkNotFoundError(document_id, target_type, target_id)
    append_event(
        ctx,
        EventType.DOCUMENT_UNLINKED,
        document_id,
        DocumentLinked(target_type=target_type, target_id=target_id),
    )


# --- Queries -----------------------------------------------------------------


def get_document(conn: Connection, document_id: UUID) -> Document | None:
    return _select_document(conn, document_id, for_update=False)


def list_documents(
    conn: Connection,
    *,
    target_type: LinkTarget | None = None,
    target_id: UUID | None = None,
) -> list[Document]:
    """Documents in the current workspace, oldest first; optionally only those linked to a
    target type, or to one target (target_id needs target_type)."""
    query = select(document_table).order_by(document_table.c.received_at, document_table.c.id)
    if target_id is not None and target_type is None:
        raise ValueError("target_id needs target_type")
    if target_type is not None:
        column = _link_column(LinkTarget(target_type))
        linked = select(link_table.c.id).where(link_table.c.document_id == document_table.c.id)
        linked = linked.where(column.is_not(None) if target_id is None else column == target_id)
        query = query.where(exists(linked))
    return [_to_document(r) for r in conn.execute(query)]


def list_links(conn: Connection, document_id: UUID) -> list[DocumentLink]:
    query = (
        select(link_table)
        .where(link_table.c.document_id == document_id)
        .order_by(link_table.c.created_at, link_table.c.id)
    )
    return [_to_link(r) for r in conn.execute(query)]


def current_extraction(conn: Connection, document_id: UUID) -> DocumentExtraction | None:
    """The highest extraction version, or None if the document has no fields yet."""
    row = conn.execute(
        select(extraction_table)
        .where(extraction_table.c.document_id == document_id)
        .order_by(extraction_table.c.version.desc())
        .limit(1)
    ).one_or_none()
    return None if row is None else DocumentExtraction.model_validate(dict(row._mapping))


def read_original(conn: Connection, store: BlobStore, document_id: UUID) -> bytes:
    """The original bytes, verified against the recorded SHA-256."""
    row = conn.execute(
        select(file_table.c.sha256, file_table.c.size_bytes, file_table.c.storage_key)
        .select_from(
            document_table.join(
                file_table,
                (file_table.c.workspace_id == document_table.c.workspace_id)
                & (file_table.c.sha256 == document_table.c.file_sha256),
            )
        )
        .where(document_table.c.id == document_id)
    ).one_or_none()
    if row is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
    data = store.get(row.storage_key)
    if len(data) != row.size_bytes or hashlib.sha256(data).digest() != row.sha256:
        raise OriginalIntegrityError(
            f"original of document {document_id} does not match its SHA-256 "
            f"({row.storage_key}); the stored blob was altered or replaced"
        )
    return data


def document_retention(
    conn: Connection, document_id: UUID, policy: RetentionPolicy = _DEFAULT_POLICY
) -> RetentionResult:
    """How long the document is kept, from its typing and what it is linked to."""
    return retention.document_retention(document_retention_input(conn, document_id), policy)


def document_retention_input(conn: Connection, document_id: UUID) -> DocumentRetentionInput:
    """The facts rules.retention needs, gathered from the document and its links.

    ownership_end_dates: one per property reached through any link (directly, or via a unit,
    a tenancy's unit, or a loan). tenancy_end_dates: one per tenancy reached directly or via a
    linked tenant's tenancies. Owner links reach neither; tenant links reach no property.
    """
    doc = get_document(conn, document_id)
    if doc is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
    links = list_links(conn, document_id)
    ids: dict[LinkTarget, set[UUID]] = {t: set() for t in LinkTarget}
    for link in links:
        ids[link.target_type].add(link.target_id)

    tenancy_ids = ids[LinkTarget.TENANCY] | _scalars(
        conn,
        select(tenancy_tenant_table.c.tenancy_id).where(
            tenancy_tenant_table.c.tenant_id.in_(ids[LinkTarget.TENANT])
        ),
    )
    unit_ids = ids[LinkTarget.UNIT] | _scalars(
        conn,
        select(tenancy_table.c.unit_id).where(tenancy_table.c.id.in_(ids[LinkTarget.TENANCY])),
    )
    property_ids = (
        ids[LinkTarget.PROPERTY]
        | _scalars(conn, select(unit_table.c.property_id).where(unit_table.c.id.in_(unit_ids)))
        | _scalars(
            conn,
            select(loan_table.c.property_id).where(loan_table.c.id.in_(ids[LinkTarget.LOAN])),
        )
    )
    ownership_end_dates = list(
        conn.execute(
            select(property_table.c.ownership_ended_on).where(property_table.c.id.in_(property_ids))
        ).scalars()
    )
    tenancy_end_dates = list(
        conn.execute(
            select(tenancy_table.c.end_date).where(tenancy_table.c.id.in_(tenancy_ids))
        ).scalars()
    )

    typing = doc.typing
    return DocumentRetentionInput(
        type=None if typing is None else typing.type,
        subtype=None if typing is None else typing.subtype,
        tax_year=None if typing is None else typing.tax_year,
        document_date=None if typing is None else typing.document_date,
        # The UTC calendar date, so the result does not depend on the session's TimeZone.
        received_on=doc.received_at.astimezone(UTC).date(),
        ownership_end_dates=ownership_end_dates,
        tenancy_end_dates=tenancy_end_dates,
    )


# --- Helpers -----------------------------------------------------------------


def _link_column(target_type: LinkTarget):
    return link_table.c[f"{target_type.value}_id"]


def _scalars(conn: Connection, query) -> set[UUID]:
    return set(conn.execute(query).scalars())


def _select_document(conn: Connection, document_id: UUID, *, for_update: bool) -> Document | None:
    query = select(document_table).where(document_table.c.id == document_id)
    if for_update:
        query = query.with_for_update()
    row = conn.execute(query).one_or_none()
    return None if row is None else _to_document(row)


def _select_document_by_sha(conn: Connection, sha256: bytes) -> Document | None:
    row = conn.execute(
        select(document_table).where(document_table.c.file_sha256 == sha256)
    ).one_or_none()
    return None if row is None else _to_document(row)


def _to_document(row) -> Document:
    m = row._mapping
    typing = None
    if m["type"] is not None:
        typing = DocumentTyping(
            type=m["type"],
            subtype=m["subtype"],
            title=m["title"],
            document_date=m["document_date"],
            tax_year=m["tax_year"],
        )
    return Document(
        id=m["id"],
        workspace_id=m["workspace_id"],
        created_at=m["created_at"],
        file_sha256=bytes(m["file_sha256"]).hex(),
        source=m["source"],
        original_filename=m["original_filename"],
        received_at=m["received_at"],
        updated_at=m["updated_at"],
        typing=typing,
    )


def _to_link(row) -> DocumentLink:
    m = row._mapping
    for target_type in LinkTarget:
        target_id = m[f"{target_type.value}_id"]
        if target_id is not None:
            return DocumentLink(
                id=m["id"],
                workspace_id=m["workspace_id"],
                document_id=m["document_id"],
                target_type=target_type,
                target_id=target_id,
                created_at=m["created_at"],
            )
    raise AssertionError("document_link row without a target")  # the CHECK forbids this
