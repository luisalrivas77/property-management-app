"""Document vault commands: content-addressed originals, typing, versioned extractions, links.

Principle 6: the original bytes are immutable. document_file is never updated (the migration's
trigger and grants enforce it), the blob store never overwrites a key, and read_original checks
the SHA-256 of what comes back. Typing and extracted fields are layers on top: document holds
the current typing, document_extraction an append-only version per change (D9, phase-1.md).
"""

import hashlib
import re
from datetime import date
from decimal import Decimal
from typing import Any, get_args
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
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
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.ai import ExtractedValue
from hausbuch.schemas.api import LinkTargetRef, RelatedDocument
from hausbuch.schemas.document import (
    Document,
    DocumentExtraction,
    DocumentLink,
    DocumentRetentionInput,
    DocumentTyping,
    HandField,
    HandFields,
    ManualDocumentFields,
    RetentionPolicy,
    RetentionResult,
)
from hausbuch.schemas.enums import (
    DocumentSource,
    EntityType,
    ExtractionKind,
    ExtractionSource,
    LinkTarget,
)
from hausbuch.schemas.events import (
    DocumentField,
    DocumentLinked,
    DocumentTyped,
    DocumentUploaded,
    EventType,
)
from hausbuch.schemas.extraction import EXTRACTION_MODELS, AnyExtraction, value_kind
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

# Hand-entry fields of the `manual` kind sent over an AI reading whose kind has no place for
# one of them (phase 2 D37, §12).
EXTRACTION_KIND_MISMATCH = "document.extraction_kind_mismatch"
_HAND_FIELDS: tuple[HandField, ...] = get_args(HandField)
# D37: where each AI kind keeps the six hand-entry fields (ManualDocumentFields). A kind or a
# hand field not listed has no place: it is not shown, and a non-null value for it is refused.
HAND_FIELD_MAP: dict[ExtractionKind, dict[HandField, str]] = {
    ExtractionKind.INVOICE: {
        "amount_total": "amount_total",
        "counterparty": "sender",
        "reference": "reference",
        "notes": "subject",
    },
    ExtractionKind.BESCHEID: {
        "amount_total": "amount_total",
        "counterparty": "sender",
        "reference": "reference",
    },
    ExtractionKind.DEPOSIT_RECORD: {"amount_total": "amount"},
    ExtractionKind.ZINSBESCHEINIGUNG: {
        "amount_total": "interest_paid",
        "counterparty": "lender",
        "reference": "contract_number",
    },
    ExtractionKind.LOAN_CONTRACT: {"counterparty": "lender", "reference": "contract_number"},
    ExtractionKind.GRUNDSTEUERBESCHEID: {"reference": "reference"},
    ExtractionKind.STATEMENT: {"period_start": "period_start", "period_end": "period_end"},
}


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
    fields: ManualDocumentFields | AnyExtraction | None = None,
    *,
    source: ExtractionSource = ExtractionSource.MANUAL,
    ai_call_id: UUID | None = None,
) -> Document:
    """Set what the document is and, optionally, its typed fields.

    Fields that differ from the current extraction (or the first ones) become the next
    extraction version, recorded with `source` (phase 2: `ai` with the ai_call it came from,
    when an applied proposal types the document with the fields already read; the default
    `manual` is a person's typing). Hand-entry fields (ManualDocumentFields) over an AI
    reading are translated into the reading's own kind (D37, hand_fields_into); a non-null
    one that kind has no place for raises RuleError(EXTRACTION_KIND_MISMATCH) and nothing is
    written. Writes nothing and appends no event if neither typing nor fields change.
    """
    current = _select_document(ctx.conn, document_id, for_update=True)
    if current is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)

    version = None
    if fields is not None:
        # The document row is locked (FOR UPDATE above), so concurrent typings of the same
        # document serialize here and cannot both compute the same next version.
        if isinstance(fields, ManualDocumentFields):
            latest = current_extraction(ctx.conn, document_id)
            if latest is not None and latest.kind is not ExtractionKind.MANUAL:
                # D37: hand fields over an AI reading become a version of the reading's kind,
                # never a `manual` one that would hide it. Equal to the reading: no version.
                fields = hand_fields_into(latest.kind, latest.fields, fields)
        version = _append_extraction(ctx, document_id, fields, source=source, ai_call_id=ai_call_id)
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


def record_ai_extraction(
    ctx: CommandContext, document_id: UUID, fields: AnyExtraction, *, ai_call_id: UUID
) -> DocumentExtraction:
    """Record what the model read as the document's next extraction version (source `ai`,
    phase 2 D14, D15), without typing the document: the typing stays a proposal until the
    user applies it. Always a new version, even when equal to the last one, so the version
    names the ai_call it came from. Appends no event (the job's document.extracted does)."""
    if _select_document(ctx.conn, document_id, for_update=True) is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
    _append_extraction(
        ctx, document_id, fields, source=ExtractionSource.AI, ai_call_id=ai_call_id, force=True
    )
    extraction = current_extraction(ctx.conn, document_id)
    assert extraction is not None
    return extraction


def _append_extraction(
    ctx: CommandContext,
    document_id: UUID,
    fields: ManualDocumentFields | AnyExtraction,
    *,
    source: ExtractionSource,
    ai_call_id: UUID | None,
    force: bool = False,
) -> int | None:
    """The next extraction version, or None when the fields equal the current version's
    (unless `force`). The caller holds the document row's lock. The kind is the fields'
    model; `ai` needs the ai_call_id (the table's provenance CHECK)."""
    source = ExtractionSource(source)
    if (source is ExtractionSource.AI) != (ai_call_id is not None):
        raise ValueError("an ai extraction carries its ai_call_id, a manual one none")
    kind = _extraction_kind(fields)
    latest = current_extraction(ctx.conn, document_id)
    if not force and latest is not None and latest.fields == fields:
        return None
    version = (latest.version if latest else 0) + 1
    ctx.conn.execute(
        insert(extraction_table).values(
            workspace_id=ctx.workspace_id,
            document_id=document_id,
            version=version,
            source=source.value,
            fields=fields.model_dump(mode="json"),
            kind=kind.value,
            ai_call_id=ai_call_id,
            created_at=ctx.now,
        )
    )
    return version


def _extraction_kind(fields: ManualDocumentFields | AnyExtraction) -> ExtractionKind:
    if isinstance(fields, ManualDocumentFields):
        return ExtractionKind.MANUAL
    for kind, model in EXTRACTION_MODELS.items():
        if type(fields) is model:
            return kind
    raise TypeError(f"not an extraction field set: {type(fields).__name__}")


# --- Hand-entry fields over an AI reading (D37) -----------------------------------------

_HAND_TYPES = {
    name: TypeAdapter(ManualDocumentFields.model_fields[name].annotation) for name in _HAND_FIELDS
}


def hand_fields(extraction: DocumentExtraction | None) -> HandFields:
    """The hand-entry section as the typing form shows it: a `manual` extraction's own fields
    (all six available), or what an AI reading holds in the fields its kind has (HAND_FIELD_MAP).
    A stored value the hand types cannot hold is shown as null (a low-confidence reading keeps
    the raw text, e.g. "ca. 400 Euro" in a money field); it is then left as it was on a save
    that does not type into it."""
    if extraction is None:
        return HandFields(values=ManualDocumentFields(), available=list(_HAND_FIELDS))
    if extraction.kind is ExtractionKind.MANUAL:
        assert isinstance(extraction.fields, ManualDocumentFields)
        return HandFields(values=extraction.fields, available=list(_HAND_FIELDS))
    mapping = HAND_FIELD_MAP.get(extraction.kind, {})
    return HandFields(
        values=_hand_values(extraction.fields, mapping),
        available=[name for name in _HAND_FIELDS if name in mapping],
    )


def hand_fields_into(
    kind: ExtractionKind, reading: AnyExtraction, typed: ManualDocumentFields
) -> AnyExtraction:
    """The reading with the typed hand fields written into it, of the same kind (D37).

    A mapped field whose typed value differs from what the form showed for it (hand_fields)
    becomes the typed value in its stored form, with no source, checks [validates], confidence
    high; cleared, it becomes missing. Every other field is the reading's, as it was, so a save
    that changes nothing returns an equal reading. A non-null hand field the kind has no place
    for raises RuleError(EXTRACTION_KIND_MISMATCH)."""
    mapping = HAND_FIELD_MAP.get(kind, {})
    unplaced = [
        name for name in _HAND_FIELDS if name not in mapping and getattr(typed, name) is not None
    ]
    if unplaced:
        raise RuleError(
            EXTRACTION_KIND_MISMATCH,
            f"a {kind} reading has no place for the hand field(s) {', '.join(unplaced)}",
        )
    shown = _hand_values(reading, mapping)
    model = type(reading)
    update: dict[str, ExtractedValue] = {}
    for name, target in mapping.items():
        kind_of = value_kind(model, target)
        value = _stored(getattr(typed, name), kind_of)
        if value == _stored(getattr(shown, name), kind_of):
            continue
        update[target] = (
            ExtractedValue.missing()
            if value is None
            else ExtractedValue(value=value, checks=["validates"], confidence="high")
        )
    return reading.model_copy(update=update) if update else reading


def _hand_values(reading: Any, mapping: dict[HandField, str]) -> ManualDocumentFields:
    values: dict[str, Any] = {}
    for name, target in mapping.items():
        stored = getattr(reading, target).value
        if stored is None:
            continue
        try:
            values[name] = _HAND_TYPES[name].validate_python(stored)
        except ValidationError:
            continue  # not a value the hand field can hold: shown empty, kept as it was
    try:
        return ManualDocumentFields.model_validate(values)
    except ValidationError:
        # Only the period can fail as a whole (an end before its start): show neither.
        values.pop("period_start", None)
        values.pop("period_end", None)
        return ManualDocumentFields.model_validate(values)


def _stored(value: Any, kind: str) -> Any:
    """A hand value in the form rules.extraction_parse stores for the value kind; None for
    none (and for a text of whitespace only)."""
    if value is None:
        return None
    if kind == "money":
        assert isinstance(value, Decimal)
        return str(value.quantize(Decimal("0.01")))
    if kind == "date":
        assert isinstance(value, date)
        return value.isoformat()
    if kind == "text":
        return " ".join(str(value).split()) or None
    raise AssertionError(f"no hand field maps to a {kind} value")


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
