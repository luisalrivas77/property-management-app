"""Document vault commands: content-addressed originals, typing, versioned extractions, links.

Principle 6: the original bytes are immutable. document_file is never updated (the migration's
trigger and grants enforce it), the blob store never overwrites a key, and read_original checks
the SHA-256 of what comes back. Typing and extracted fields are layers on top: document holds
the current typing, document_extraction an append-only version per change (D9, phase-1.md).
"""

import hashlib
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, delete, exists, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import property_acquisition_cost as cost_table
from hausbuch.db.tables import property_price_split as split_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.rules import retention
from hausbuch.schemas.api import LinkTargetRef, RelatedDocument
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
from hausbuch.schemas.events import (
    DocumentField,
    DocumentLinked,
    DocumentTyped,
    DocumentUploaded,
    EventType,
)
from hausbuch.storage import BlobStore, blob_key

from .context import CommandContext
from .errors import NotFoundError
from .events import append_event
from .parties import tenancies_ever_of

# The document_file.media_type CHECK, so a bad value is refused before any SQL.
_MEDIA_TYPE = re.compile(r"[a-z]+/[a-z0-9.+-]+")
_FOREIGN_KEY_VIOLATION = "23503"
_UNIQUE_VIOLATION = "23505"
_DEFAULT_POLICY = RetentionPolicy()  # frozen, so safe to share as a default
# The order of a related document's `via`: the page's own (direct) link first, then children.
_VIA_ORDER = {LinkTarget.PROPERTY: 0, LinkTarget.UNIT: 1, LinkTarget.TENANCY: 2, LinkTarget.LOAN: 3}
# The landlord's calendar: received_on is the local date a document arrived on.
LOCAL_TIME_ZONE = ZoneInfo("Europe/Berlin")


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
    same bytes (one document per file per workspace, D9).

    A re-upload writes no rows and no event, but it does put the blob again: that restores an
    original lost from the store, and fails with BlobConflictError if different bytes sit
    under the key.
    """
    if not data:
        raise InvalidUploadError("an upload must not be empty")
    if not isinstance(media_type, str) or not _MEDIA_TYPE.fullmatch(media_type):
        raise InvalidUploadError(f"media type {media_type!r} is not a lowercase type/subtype")
    source = DocumentSource(source)

    digest = hashlib.sha256(data)
    sha_bytes, sha_hex = digest.digest(), digest.hexdigest()
    key = blob_key(ctx.workspace_id, sha_hex)
    # Blob first, rows second, and on every upload: put is idempotent, so a re-upload heals a
    # lost blob. If the transaction later rolls back, the blob stays behind unreferenced; that
    # is harmless: the key is content-addressed, so a retry reuses it.
    store.put(key, data, media_type)
    existing = _select_document_by_sha(ctx.conn, sha_bytes)
    if existing is not None:
        return existing

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
        # Only the same file may conflict; a storage_key collision must fail loudly.
        .on_conflict_do_nothing(index_elements=["workspace_id", "sha256"])
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
    # In the DocumentField order; an untyped document had no value for any field. The title
    # is named here but its value never enters the payload (D5).
    previous = current.typing
    changed = [
        field
        for field in DocumentField
        if (None if previous is None else getattr(previous, field.value))
        != getattr(typing, field.value)
    ]
    append_event(
        ctx,
        EventType.DOCUMENT_TYPED,
        document_id,
        DocumentTyped(
            type=typing.type,
            subtype=typing.subtype,
            document_date=typing.document_date,
            tax_year=typing.tax_year,
            changed_fields=changed,
            extraction_version=version,
        ),
    )
    return typed


def link_document(
    ctx: CommandContext, document_id: UUID, target_type: LinkTarget, target_id: UUID
) -> DocumentLink:
    """Link a document to the entity it proves."""
    target_type = _check_link_args(document_id, target_type, target_id)
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
    target_type = _check_link_args(document_id, target_type, target_id)
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


def list_related_documents(
    conn: Connection, *, target_type: LinkTarget, target_id: UUID
) -> list[RelatedDocument]:
    """The documents of a property or a unit together with those of its children, oldest
    first like list_documents, one entry per document.

    A property rolls up links to itself, its units, those units' tenancies and its loans; a
    unit, links to itself and its tenancies. Owner and tenant links are never rolled up: owners
    are household-wide, and tenants (personal data) reach a property only through
    tenancy_tenant. `via` is every matching link of the document, the direct one first, then
    unit, tenancy and loan links, each group by target id.
    """
    if not isinstance(target_id, UUID):  # as in _check_link_args: None would match IS NULL
        raise TypeError(f"target_id must be a UUID, got {type(target_id).__name__}")
    links = link_table.c
    target_type = LinkTarget(target_type)
    if target_type is LinkTarget.PROPERTY:
        units = select(unit_table.c.id).where(unit_table.c.property_id == target_id)
        matches = or_(
            links.property_id == target_id,
            links.unit_id.in_(units),
            links.tenancy_id.in_(
                select(tenancy_table.c.id).where(tenancy_table.c.unit_id.in_(units))
            ),
            links.loan_id.in_(select(loan_table.c.id).where(loan_table.c.property_id == target_id)),
        )
    elif target_type is LinkTarget.UNIT:
        matches = or_(
            links.unit_id == target_id,
            links.tenancy_id.in_(
                select(tenancy_table.c.id).where(tenancy_table.c.unit_id == target_id)
            ),
        )
    else:
        raise ValueError(f"documents roll up to a property or a unit only, not {target_type}")

    linked = select(links.id).where(links.document_id == document_table.c.id).where(matches)
    documents = [
        _to_document(r)
        for r in conn.execute(
            select(document_table)
            .where(exists(linked))
            .order_by(document_table.c.received_at, document_table.c.id)
        )
    ]
    if not documents:
        return []
    via: dict[UUID, list[DocumentLink]] = {d.id: [] for d in documents}
    query = select(link_table).where(links.document_id.in_(via)).where(matches)
    for row in conn.execute(query):
        link = _to_link(row)
        via[link.document_id].append(link)
    return [
        RelatedDocument(
            document=d,
            via=[
                LinkTargetRef(target_type=lk.target_type, target_id=lk.target_id)
                for lk in sorted(
                    via[d.id], key=lambda lk: (_VIA_ORDER[lk.target_type], lk.target_id)
                )
            ],
        )
        for d in documents
    ]


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


def document_media_type(conn: Connection, document_id: UUID) -> str | None:
    """The media type of the document's file (document_file.media_type), None if no such
    document is visible."""
    return conn.execute(
        select(file_table.c.media_type)
        .select_from(_document_with_file())
        .where(document_table.c.id == document_id)
    ).scalar_one_or_none()


def read_original(conn: Connection, store: BlobStore, document_id: UUID) -> bytes:
    """The original bytes, verified against the recorded SHA-256."""
    row = conn.execute(
        select(file_table.c.sha256, file_table.c.size_bytes, file_table.c.storage_key)
        .select_from(_document_with_file())
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

    # A linked tenant reaches every tenancy they were ever a party of (parties.py).
    tenancy_ids = ids[LinkTarget.TENANCY] | tenancies_ever_of(conn, ids[LinkTarget.TENANT])
    unit_ids = ids[LinkTarget.UNIT] | _scalars(
        conn,
        select(tenancy_table.c.unit_id).where(tenancy_table.c.id.in_(ids[LinkTarget.TENANCY])),
    )
    # Recorded as the evidence of an acquisition cost or a price split: the AfA basis and the
    # §23 gain are built from it, so it is an ownership record of that property (amendment 1).
    cost_basis_properties = _scalars(
        conn, select(cost_table.c.property_id).where(cost_table.c.document_id == document_id)
    ) | _scalars(
        conn,
        select(split_table.c.property_id).where(split_table.c.evidence_document_id == document_id),
    )
    property_ids = (
        ids[LinkTarget.PROPERTY]
        | _scalars(conn, select(unit_table.c.property_id).where(unit_table.c.id.in_(unit_ids)))
        | _scalars(
            conn,
            select(loan_table.c.property_id).where(loan_table.c.id.in_(ids[LinkTarget.LOAN])),
        )
        | cost_basis_properties
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
        # The local (Berlin) calendar date, whatever the session's TimeZone. Per D11 retention
        # must never end early, and §147 AO counts the local calendar year: a document received
        # at 00:30 on 1 Jan in Berlin belongs to the new year, though it is still 31 Dec in UTC.
        received_on=doc.received_at.astimezone(LOCAL_TIME_ZONE).date(),
        ownership_end_dates=ownership_end_dates,
        tenancy_end_dates=tenancy_end_dates,
        cost_basis_reference=bool(cost_basis_properties),
    )


# --- Helpers -----------------------------------------------------------------


def _document_with_file():
    return document_table.join(
        file_table,
        (file_table.c.workspace_id == document_table.c.workspace_id)
        & (file_table.c.sha256 == document_table.c.file_sha256),
    )


def _check_link_args(document_id: UUID, target_type: LinkTarget, target_id: UUID) -> LinkTarget:
    """Refuse anything but UUIDs before any SQL: a None target_id would render as
    `<target>_id IS NULL` and match every other link of the document."""
    for name, value in (("document_id", document_id), ("target_id", target_id)):
        if not isinstance(value, UUID):
            raise TypeError(f"{name} must be a UUID, got {type(value).__name__}")
    return LinkTarget(target_type)


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
