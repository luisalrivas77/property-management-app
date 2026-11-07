"""The document vault end to end: services under hausbuch_app and RLS, with a LocalBlobStore."""

import hashlib
import json
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
import pytest
from db.db_support import OWNER, TEST_IBAN_KEY, insert_property, insert_tenancy, insert_unit
from fixture_models import RetentionFixture, load
from sqlalchemy import event as sa_event
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.ai import ExtractedValue, SourceRef
from hausbuch.schemas.api import LinkTargetRef, RelatedDocument
from hausbuch.schemas.document import DocumentTyping, ManualDocumentFields
from hausbuch.schemas.enums import (
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    EntityType,
    LinkTarget,
    RentModel,
    RetentionClass,
    RetentionHold,
    TenancyRole,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.extraction import InvoiceExtraction
from hausbuch.schemas.tenancy import TenancyCreate, TenancyParty
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import (
    DuplicateLinkError,
    InvalidUploadError,
    LinkNotFoundError,
    LinkTargetNotFoundError,
    OriginalIntegrityError,
    current_extraction,
    document_retention,
    document_retention_input,
    get_document,
    hand_fields,
    link_document,
    list_documents,
    list_links,
    list_related_documents,
    read_original,
    record_ai_extraction,
    type_document,
    unlink_document,
    upload_document,
)
from hausbuch.services.errors import NotFoundError
from hausbuch.services.events import list_events
from hausbuch.services.tenancies import create_tenancy, set_parties
from hausbuch.storage import BlobConflictError, blob_key

NOW = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
LATER = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
PDF = "application/pdf"
INVOICE = DocumentTyping(type=DocumentType.INVOICE, title="Heizungswartung", tax_year=2026)
FIELDS_1 = ManualDocumentFields(amount_total=Decimal("412.30"), counterparty="Heizung Meier")
FIELDS_2 = ManualDocumentFields(amount_total=Decimal("421.30"), counterparty="Heizung Meier")
PURCHASE_CONTRACT = DocumentTyping(
    type=DocumentType.CONTRACT, subtype=DocumentSubtype.PURCHASE_CONTRACT
)
LEASE = DocumentTyping(type=DocumentType.CONTRACT, subtype=DocumentSubtype.LEASE)


def _bytes(label: str = "doc") -> bytes:
    """Unique content per call: the workspaces are shared across the test session."""
    return f"%PDF-1.7 {label} {uuid.uuid4()}".encode()


def _upload(engine, store, ws, data, *, now=NOW, **kw):
    kw = {"media_type": PDF, "source": DocumentSource.UPLOAD} | kw
    with workspace_transaction(engine, ws) as conn:
        return upload_document(new_command(conn, ws, ACTOR, now=now), store, data, **kw)


def _type(engine, ws, doc_id, typing, fields=None, *, now=LATER):
    with workspace_transaction(engine, ws) as conn:
        return type_document(new_command(conn, ws, ACTOR, now=now), doc_id, typing, fields)


def _link(engine, ws, doc_id, target_type, target_id):
    with workspace_transaction(engine, ws) as conn:
        return link_document(
            new_command(conn, ws, ACTOR, now=LATER), doc_id, target_type, target_id
        )


def _unlink(engine, ws, doc_id, target_type, target_id):
    with workspace_transaction(engine, ws) as conn:
        unlink_document(new_command(conn, ws, ACTOR, now=LATER), doc_id, target_type, target_id)


def _events(engine, ws, doc_id):
    with workspace_transaction(engine, ws) as conn:
        return list_events(conn, entity_type=EntityType.DOCUMENT, entity_id=doc_id)


@contextmanager
def _seed(engine, ws):
    """A raw psycopg connection inside a committed workspace transaction, for the SQL helpers."""
    with workspace_transaction(engine, ws) as conn:
        yield conn.connection.driver_connection


@contextmanager
def recorded_sql(conn):
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    sa_event.listen(conn, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sa_event.remove(conn, "before_cursor_execute", record)


# Small SQL seeds for link targets db_support has no helper for.


def _insert_owner(raw, ws) -> uuid.UUID:
    sql = "INSERT INTO owner (workspace_id, full_name) VALUES (%s, 'Erika Muster') RETURNING id"
    return raw.execute(sql, (ws,)).fetchone()[0]


def _insert_loan(raw, ws, property_id) -> uuid.UUID:
    return raw.execute(
        "INSERT INTO loan (workspace_id, property_id, lender, principal, nominal_rate,"
        " monthly_payment, disbursed_on, first_payment_on)"
        " VALUES (%s, %s, 'Sparkasse', 200000, 0.036, 900, '2024-05-01', '2024-06-30')"
        " RETURNING id",
        (ws, property_id),
    ).fetchone()[0]


def _insert_tenant(raw, ws) -> uuid.UUID:
    sql = "INSERT INTO tenant (workspace_id, full_name) VALUES (%s, 'Max Mieter') RETURNING id"
    return raw.execute(sql, (ws,)).fetchone()[0]


def _add_party(raw, ws, tenancy_id, tenant_id, role="primary") -> None:
    raw.execute(
        "INSERT INTO tenancy_tenant (workspace_id, tenancy_id, tenant_id, role)"
        " VALUES (%s, %s, %s, %s)",
        (ws, tenancy_id, tenant_id, role),
    )


def _tenancy(raw, ws, end=None, property_id=None) -> uuid.UUID:
    """A tenancy on a fresh unit (so tenancies never overlap), on a fresh property by default."""
    property_id = property_id or insert_property(raw, ws)
    return insert_tenancy(raw, ws, insert_unit(raw, ws, property_id), "2024-06-01", end)


# --- upload and dedup ----------------------------------------------------------


def test_upload_stores_the_original_and_writes_one_event(app_engine, workspaces, blob_store):
    a, _ = workspaces
    data = _bytes()
    sha = hashlib.sha256(data).hexdigest()

    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        doc = upload_document(
            ctx,
            blob_store,
            data,
            media_type=PDF,
            source=DocumentSource.PHOTO,
            original_filename="kaufvertrag.pdf",
        )

    assert doc.workspace_id == a
    assert doc.file_sha256 == sha
    assert (doc.source, doc.original_filename, doc.typing) == (
        DocumentSource.PHOTO,
        "kaufvertrag.pdf",
        None,
    )
    assert doc.received_at == doc.created_at == doc.updated_at == NOW
    key = blob_key(a, sha)
    assert blob_store.get(key) == data
    with workspace_transaction(app_engine, a) as conn:
        assert get_document(conn, doc.id) == doc
        file = conn.execute(select(file_table).where(file_table.c.storage_key == key)).one()
        assert (file.size_bytes, file.media_type, bytes(file.sha256).hex()) == (len(data), PDF, sha)
        assert read_original(conn, blob_store, doc.id) == data

    [uploaded] = _events(app_engine, a, doc.id)
    assert uploaded.type is EventType.DOCUMENT_UPLOADED
    assert uploaded.payload == {
        "sha256": sha,
        "media_type": PDF,
        "size_bytes": len(data),
        "source": "photo",
    }
    assert uploaded.correlation_id == ctx.correlation_id
    assert uploaded.property_id is None and uploaded.unit_id is None
    assert uploaded.effective_date is None


def test_same_bytes_twice_is_the_same_document_and_one_event(app_engine, workspaces, blob_store):
    a, _ = workspaces
    data = _bytes()
    first = _upload(app_engine, blob_store, a, data, original_filename="scan.pdf")

    with workspace_transaction(app_engine, a) as conn, recorded_sql(conn) as statements:
        again = upload_document(
            new_command(conn, a, ACTOR, now=LATER),
            blob_store,
            data,
            media_type="image/jpeg",
            source=DocumentSource.EMAIL,
            original_filename="other.jpg",
        )

    assert again == first
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements), statements
    assert len(_events(app_engine, a, first.id)) == 1
    with workspace_transaction(app_engine, a) as conn:
        sha = bytes.fromhex(first.file_sha256)
        assert len(conn.execute(select(file_table).where(file_table.c.sha256 == sha)).all()) == 1


def test_same_bytes_in_another_workspace_is_a_separate_document(app_engine, workspaces, blob_store):
    a, b = workspaces
    data = _bytes()
    in_a = _upload(app_engine, blob_store, a, data)
    in_b = _upload(app_engine, blob_store, b, data)

    assert in_a.id != in_b.id and in_a.file_sha256 == in_b.file_sha256
    assert (in_a.workspace_id, in_b.workspace_id) == (a, b)
    assert blob_store.exists(blob_key(a, in_a.file_sha256))
    assert blob_store.exists(blob_key(b, in_b.file_sha256))
    assert len(_events(app_engine, a, in_a.id)) == len(_events(app_engine, b, in_b.id)) == 1


def test_re_upload_restores_a_lost_blob(app_engine, workspaces, blob_store):
    a, _ = workspaces
    data = _bytes()
    first = _upload(app_engine, blob_store, a, data)
    key = blob_key(a, first.file_sha256)
    blob_store.delete(key)  # lost out of band

    again = _upload(app_engine, blob_store, a, data)

    assert again.id == first.id
    assert blob_store.exists(key)
    with workspace_transaction(app_engine, a) as conn:
        assert read_original(conn, blob_store, first.id) == data
    assert len(_events(app_engine, a, first.id)) == 1


def test_re_upload_fails_when_other_bytes_sit_under_the_key(app_engine, workspaces, blob_store):
    a, _ = workspaces
    data = _bytes()
    first = _upload(app_engine, blob_store, a, data)
    key = blob_key(a, first.file_sha256)
    blob_store.delete(key)
    blob_store.put(key, b"forged", PDF)

    with pytest.raises(BlobConflictError):
        _upload(app_engine, blob_store, a, data)
    assert blob_store.get(key) == b"forged"  # never overwritten; read_original will flag it
    with workspace_transaction(app_engine, a) as conn, pytest.raises(OriginalIntegrityError):
        read_original(conn, blob_store, first.id)


def test_concurrent_uploads_of_the_same_bytes_give_one_document(app_engine, workspaces, blob_store):
    a, _ = workspaces
    data = _bytes()
    sha = hashlib.sha256(data).digest()
    results: list = []

    def second_upload():
        try:
            with workspace_transaction(app_engine, a) as conn:
                conn.execute(text("SET LOCAL lock_timeout = '10s'"))
                ctx = new_command(conn, a, ACTOR, now=LATER)
                results.append(
                    upload_document(
                        ctx, blob_store, data, media_type=PDF, source=DocumentSource.EMAIL
                    )
                )
        except BaseException as exc:
            results.append(exc)

    with workspace_transaction(app_engine, a) as conn:
        first = upload_document(
            new_command(conn, a, ACTOR, now=NOW),
            blob_store,
            data,
            media_type=PDF,
            source=DocumentSource.UPLOAD,
        )
        other = threading.Thread(target=second_upload)
        other.start()
        other.join(timeout=0.5)
        assert other.is_alive()  # waiting on this transaction's unique key
    other.join(timeout=15)

    assert not other.is_alive()
    [second] = results
    assert not isinstance(second, BaseException), second
    assert second.id == first.id and second.source is DocumentSource.UPLOAD
    assert [e.type for e in _events(app_engine, a, first.id)] == [EventType.DOCUMENT_UPLOADED]
    with workspace_transaction(app_engine, a) as conn:
        assert len(conn.execute(select(file_table).where(file_table.c.sha256 == sha)).all()) == 1


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (b"", PDF),
        (b"x", ""),
        (b"x", "application/PDF"),
        (b"x", "application/pdf; charset=binary"),
        (b"x", "pdf"),
        (b"x", "application/pdf\n"),
    ],
    ids=["empty", "no-media-type", "uppercase", "parameters", "no-slash", "trailing-newline"],
)
def test_bad_upload_is_rejected_before_any_sql(
    app_engine, workspaces, blob_store, data, media_type
):
    a, _ = workspaces
    with workspace_transaction(app_engine, a) as conn, recorded_sql(conn) as statements:
        with pytest.raises(InvalidUploadError):
            upload_document(
                new_command(conn, a, ACTOR, now=NOW),
                blob_store,
                data,
                media_type=media_type,
                source=DocumentSource.UPLOAD,
            )
        assert statements == []
    assert list(blob_store.root.iterdir()) == []


def test_rolled_back_upload_leaves_a_blob_that_a_retry_reuses(app_engine, workspaces, blob_store):
    a, _ = workspaces
    data = _bytes()

    class Boom(Exception):
        pass

    with pytest.raises(Boom), workspace_transaction(app_engine, a) as conn:
        upload_document(
            new_command(conn, a, ACTOR, now=NOW),
            blob_store,
            data,
            media_type=PDF,
            source=DocumentSource.UPLOAD,
        )
        raise Boom

    sha = hashlib.sha256(data).hexdigest()
    assert blob_store.exists(blob_key(a, sha))  # left behind, unreferenced
    with workspace_transaction(app_engine, a) as conn:
        assert sha not in {d.file_sha256 for d in list_documents(conn)}

    retried = _upload(app_engine, blob_store, a, data)
    assert len(_events(app_engine, a, retried.id)) == 1
    with workspace_transaction(app_engine, a) as conn:
        assert read_original(conn, blob_store, retried.id) == data


# --- immutability of the original ---------------------------------------------


def test_original_cannot_be_modified(app_engine, workspaces, blob_store, connect):
    a, _ = workspaces
    data = _bytes()
    doc = _upload(app_engine, blob_store, a, data)
    sha = bytes.fromhex(doc.file_sha256)
    key = blob_key(a, doc.file_sha256)

    # The app role has no UPDATE grant on document_file.
    no_grant = pytest.raises(DBAPIError, match="permission denied")
    with no_grant, workspace_transaction(app_engine, a) as conn:
        conn.execute(
            update(file_table).where(file_table.c.sha256 == sha).values(media_type="text/plain")
        )
    # Even the table owner is stopped by the trigger.
    with connect(OWNER, a) as conn, pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute("UPDATE document_file SET media_type = 'text/plain' WHERE sha256 = %s", (sha,))
    # The store refuses different bytes under the key and keeps the file read-only.
    with pytest.raises(BlobConflictError):
        blob_store.put(key, b"forged", PDF)
    assert stat.S_IMODE((blob_store.root / key).stat().st_mode) & 0o222 == 0

    with workspace_transaction(app_engine, a) as conn:
        assert read_original(conn, blob_store, doc.id) == data
        file = conn.execute(select(file_table).where(file_table.c.sha256 == sha)).one()
        assert file.media_type == PDF


@pytest.mark.parametrize("same_size", [True, False], ids=["same-size", "other-size"])
def test_read_original_detects_a_tampered_blob(app_engine, workspaces, blob_store, same_size):
    a, _ = workspaces
    data = _bytes()
    doc = _upload(app_engine, blob_store, a, data)
    path = blob_store.root / blob_key(a, doc.file_sha256)
    os.chmod(path, 0o644)  # tampering from outside the store's API
    path.write_bytes(b"?" * len(data) if same_size else b"?")

    with workspace_transaction(app_engine, a) as conn, pytest.raises(OriginalIntegrityError):
        read_original(conn, blob_store, doc.id)


def test_read_original_of_an_unknown_document_raises(app_engine, workspaces, blob_store):
    a, _ = workspaces
    with workspace_transaction(app_engine, a) as conn, pytest.raises(NotFoundError):
        read_original(conn, blob_store, uuid.uuid4())


# --- typing and extraction versions -------------------------------------------


def test_typing_creates_extraction_versions_and_current_is_the_latest(
    app_engine, workspaces, blob_store
):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    with workspace_transaction(app_engine, a) as conn:
        assert current_extraction(conn, doc.id) is None

    typed = _type(app_engine, a, doc.id, INVOICE, FIELDS_1)
    assert typed.typing == INVOICE
    assert typed.updated_at == LATER and typed.created_at == NOW
    _type(app_engine, a, doc.id, INVOICE, FIELDS_2)
    retyped = _type(app_engine, a, doc.id, INVOICE.model_copy(update={"tax_year": 2025}))

    with workspace_transaction(app_engine, a) as conn:
        current = current_extraction(conn, doc.id)
        assert (current.version, current.fields, current.source) == (2, FIELDS_2, "manual")
        assert current.fields.amount_total == Decimal("421.30")
        versions = conn.execute(
            select(extraction_table.c.version)
            .where(extraction_table.c.document_id == doc.id)
            .order_by(extraction_table.c.version)
        ).scalars()
        assert list(versions) == [1, 2]
        assert get_document(conn, doc.id) == retyped
    assert retyped.typing.tax_year == 2025

    typed_events = _events(app_engine, a, doc.id)[1:]
    assert [e.type for e in typed_events] == [EventType.DOCUMENT_TYPED] * 3
    invoice = {"type": "invoice", "subtype": None, "document_date": None}
    assert [e.payload for e in typed_events] == [
        invoice
        | {"tax_year": 2026, "changed_fields": ["type", "title", "tax_year"]}
        | {"extraction_version": 1},
        invoice | {"tax_year": 2026, "changed_fields": [], "extraction_version": 2},
        invoice | {"tax_year": 2025, "changed_fields": ["tax_year"], "extraction_version": None},
    ]


def test_typed_event_records_the_date_and_changed_fields_but_never_the_title(
    app_engine, workspaces, blob_store
):
    """Spec amendment 1: document.typed names the changed fields, title included, and traces
    document_date (a retention input); the title's value (it often names the tenant) never
    reaches a payload."""
    a, _ = workspaces
    titles = ["Mietvertrag Max Mieter", "Mietvertrag Max und Erika Mieter"]
    doc = _upload(app_engine, blob_store, a, _bytes())
    first = DocumentTyping(type=DocumentType.LETTER, title=titles[0])
    steps = [
        (first, ["type", "title"]),
        (first.model_copy(update={"title": titles[1]}), ["title"]),
        (
            DocumentTyping(
                type=DocumentType.CONTRACT,
                subtype=DocumentSubtype.LEASE,
                title=titles[1],
                document_date=date(2024, 5, 15),
                tax_year=2024,
            ),
            ["type", "subtype", "document_date", "tax_year"],
        ),
        (
            DocumentTyping(
                type=DocumentType.CONTRACT,
                subtype=DocumentSubtype.LEASE,
                document_date=date(2024, 5, 16),
                tax_year=2024,
            ),
            ["title", "document_date"],
        ),
    ]
    for typing, _ in steps:
        _type(app_engine, a, doc.id, typing)

    events = _events(app_engine, a, doc.id)
    typed_events = events[1:]
    assert [e.type for e in typed_events] == [EventType.DOCUMENT_TYPED] * len(steps)
    for event, (typing, changed) in zip(typed_events, steps, strict=True):
        assert event.payload["changed_fields"] == changed
        assert event.payload["document_date"] == (
            None if typing.document_date is None else typing.document_date.isoformat()
        )
        assert "title" not in event.payload
    assert typed_events[-1].payload == {
        "type": "contract",
        "subtype": "lease",
        "document_date": "2024-05-16",
        "tax_year": 2024,
        "changed_fields": ["title", "document_date"],
        "extraction_version": None,
    }
    recorded = json.dumps([e.payload for e in events])
    for title in titles:
        assert title not in recorded
    assert "Mieter" not in recorded


@pytest.mark.parametrize("fields", [None, FIELDS_1], ids=["no-fields", "same-fields"])
def test_unchanged_typing_writes_nothing(app_engine, workspaces, blob_store, fields):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    typed = _type(app_engine, a, doc.id, INVOICE, FIELDS_1)

    again = _type(app_engine, a, doc.id, INVOICE, fields, now=datetime(2027, 1, 1, tzinfo=UTC))

    assert again == typed
    assert len(_events(app_engine, a, doc.id)) == 2
    with workspace_transaction(app_engine, a) as conn:
        assert current_extraction(conn, doc.id).version == 1


def test_concurrent_typings_get_consecutive_versions(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    errors: list[BaseException] = []

    def second_typing():
        try:
            with workspace_transaction(app_engine, a) as conn:
                conn.execute(text("SET LOCAL lock_timeout = '10s'"))
                type_document(new_command(conn, a, ACTOR, now=LATER), doc.id, INVOICE, FIELDS_2)
        except BaseException as exc:
            errors.append(exc)

    with workspace_transaction(app_engine, a) as conn:
        type_document(new_command(conn, a, ACTOR, now=NOW), doc.id, INVOICE, FIELDS_1)
        other = threading.Thread(target=second_typing)
        other.start()
        other.join(timeout=0.5)
        assert other.is_alive()  # waiting for this transaction's row lock
    other.join(timeout=15)

    assert not other.is_alive() and errors == []
    with workspace_transaction(app_engine, a) as conn:
        current = current_extraction(conn, doc.id)
        assert (current.version, current.fields) == (2, FIELDS_2)


def test_typing_an_unknown_document_raises(app_engine, workspaces):
    a, _ = workspaces
    with pytest.raises(NotFoundError, match="document"):
        _type(app_engine, a, uuid.uuid4(), INVOICE)


# --- links ---------------------------------------------------------------------


def test_link_and_unlink_with_their_events(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    other = _upload(app_engine, blob_store, a, _bytes())
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)

    link = _link(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)

    assert (link.document_id, link.target_type, link.target_id) == (doc.id, "property", prop)
    assert link.created_at == LATER and link.workspace_id == a
    with workspace_transaction(app_engine, a) as conn:
        assert list_links(conn, doc.id) == [link]
        assert list_documents(conn, target_type=LinkTarget.PROPERTY, target_id=prop) == [doc]
        by_type = list_documents(conn, target_type=LinkTarget.PROPERTY)
        assert doc in by_type and other not in by_type
        all_docs = list_documents(conn)
        assert doc in all_docs and other in all_docs
        with pytest.raises(ValueError, match="target_type"):
            list_documents(conn, target_id=prop)

    _unlink(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)

    with workspace_transaction(app_engine, a) as conn:
        assert list_links(conn, doc.id) == []
        assert list_documents(conn, target_type=LinkTarget.PROPERTY, target_id=prop) == []
    _, linked, unlinked = _events(app_engine, a, doc.id)
    expected = {"target_type": "property", "target_id": str(prop)}
    assert (linked.type, linked.payload) == (EventType.DOCUMENT_LINKED, expected)
    assert (unlinked.type, unlinked.payload) == (EventType.DOCUMENT_UNLINKED, expected)
    assert linked.property_id is None and unlinked.property_id is None


@pytest.mark.parametrize("target_type", list(LinkTarget))
def test_every_target_type_can_be_linked(app_engine, workspaces, blob_store, target_type):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
        target_id = {
            LinkTarget.OWNER: lambda: _insert_owner(raw, a),
            LinkTarget.PROPERTY: lambda: prop,
            LinkTarget.UNIT: lambda: insert_unit(raw, a, prop),
            LinkTarget.TENANT: lambda: _insert_tenant(raw, a),
            LinkTarget.TENANCY: lambda: _tenancy(raw, a, property_id=prop),
            LinkTarget.LOAN: lambda: _insert_loan(raw, a, prop),
        }[target_type]()

    link = _link(app_engine, a, doc.id, target_type, target_id)

    assert (link.target_type, link.target_id) == (target_type, target_id)
    with workspace_transaction(app_engine, a) as conn:
        assert list_documents(conn, target_type=target_type, target_id=target_id) == [doc]


def test_linking_twice_raises_and_keeps_the_transaction_usable(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
        unit = insert_unit(raw, a, prop)
    _link(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)

    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=LATER)
        with pytest.raises(DuplicateLinkError, match="already linked"):
            link_document(ctx, doc.id, LinkTarget.PROPERTY, prop)
        link_document(ctx, doc.id, LinkTarget.UNIT, unit)  # savepoint: still usable

    with workspace_transaction(app_engine, a) as conn:
        assert {(lk.target_type, lk.target_id) for lk in list_links(conn, doc.id)} == {
            (LinkTarget.PROPERTY, prop),
            (LinkTarget.UNIT, unit),
        }
    assert [e.type for e in _events(app_engine, a, doc.id)].count(EventType.DOCUMENT_LINKED) == 2


def test_missing_or_cross_workspace_target_is_rejected(app_engine, workspaces, blob_store):
    a, b = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    with _seed(app_engine, b) as raw:
        b_property = insert_property(raw, b)

    for target_id in (b_property, uuid.uuid4()):
        with pytest.raises(LinkTargetNotFoundError) as excinfo:
            _link(app_engine, a, doc.id, LinkTarget.PROPERTY, target_id)
        assert (excinfo.value.entity_type, excinfo.value.entity_id) == ("property", target_id)
    assert isinstance(excinfo.value, NotFoundError)

    with workspace_transaction(app_engine, a) as conn:
        assert list_links(conn, doc.id) == []
    assert len(_events(app_engine, a, doc.id)) == 1


def test_link_and_unlink_errors(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)

    with pytest.raises(NotFoundError, match="document"):
        _link(app_engine, a, uuid.uuid4(), LinkTarget.PROPERTY, prop)
    with pytest.raises(LinkNotFoundError, match="not linked"):
        _unlink(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)
    assert len(_events(app_engine, a, doc.id)) == 1


@pytest.mark.parametrize("command", [link_document, unlink_document])
@pytest.mark.parametrize("bad_id", [None, "not-a-uuid"], ids=["none", "string"])
def test_a_non_uuid_target_is_rejected_before_any_sql(
    app_engine, workspaces, blob_store, command, bad_id
):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
        unit = insert_unit(raw, a, prop)
    _link(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)
    _link(app_engine, a, doc.id, LinkTarget.UNIT, unit)

    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=LATER)
        with recorded_sql(conn) as statements, pytest.raises(TypeError, match="target_id"):
            command(ctx, doc.id, LinkTarget.OWNER, bad_id)
        assert statements == []

    with workspace_transaction(app_engine, a) as conn:
        assert {lk.target_id for lk in list_links(conn, doc.id)} == {prop, unit}
    assert len(_events(app_engine, a, doc.id)) == 3


def test_documents_are_isolated_by_workspace(app_engine, workspaces, blob_store):
    a, b = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    _type(app_engine, a, doc.id, INVOICE, FIELDS_1)
    with _seed(app_engine, b) as raw:
        b_property = insert_property(raw, b)

    with workspace_transaction(app_engine, b) as conn:
        assert get_document(conn, doc.id) is None
        assert doc.id not in {d.id for d in list_documents(conn)}
        assert current_extraction(conn, doc.id) is None
        assert list_links(conn, doc.id) == []
        assert list_events(conn, entity_id=doc.id) == []
        with pytest.raises(NotFoundError):
            read_original(conn, blob_store, doc.id)
        with pytest.raises(NotFoundError):
            document_retention(conn, doc.id)
    with pytest.raises(NotFoundError):
        _type(app_engine, b, doc.id, INVOICE)
    with pytest.raises(NotFoundError):
        _link(app_engine, b, doc.id, LinkTarget.PROPERTY, b_property)
    with pytest.raises(LinkNotFoundError):
        _unlink(app_engine, b, doc.id, LinkTarget.PROPERTY, b_property)


# --- roll-up to a property or a unit -------------------------------------------------


def _uploads(engine, store, ws, count):
    """`count` documents received one minute apart, so the roll-up's order is checkable."""
    return [
        _upload(engine, store, ws, _bytes(), now=NOW + timedelta(minutes=i)) for i in range(count)
    ]


def _related(engine, ws, target_type, target_id):
    with workspace_transaction(engine, ws) as conn:
        return list_related_documents(conn, target_type=target_type, target_id=target_id)


def _ref(target_type, target_id):
    return LinkTargetRef(target_type=target_type, target_id=target_id)


def test_a_property_rolls_up_its_units_tenancies_and_loans(app_engine, workspaces, blob_store):
    a, _ = workspaces
    docs = _uploads(app_engine, blob_store, a, 9)
    direct, by_unit, by_tenancy, by_loan, both, by_owner, by_tenant, sibling, sibling_unit = docs
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
        unit = insert_unit(raw, a, prop)
        unit_2 = insert_unit(raw, a, prop, label="WE 4")
        tenancy = insert_tenancy(raw, a, unit, "2024-06-01")
        loan = _insert_loan(raw, a, prop)
        tenant = _insert_tenant(raw, a)
        _add_party(raw, a, tenancy, tenant)
        owner = _insert_owner(raw, a)
        other = insert_property(raw, a)
        other_unit = insert_unit(raw, a, other)
    links = [
        (direct, LinkTarget.PROPERTY, prop),
        (by_unit, LinkTarget.UNIT, unit),
        (by_tenancy, LinkTarget.TENANCY, tenancy),
        (by_loan, LinkTarget.LOAN, loan),
        # Linked in reverse of the `via` order, which must not depend on the link order.
        (both, LinkTarget.TENANCY, tenancy),
        (both, LinkTarget.UNIT, unit_2),
        (both, LinkTarget.UNIT, unit),
        (both, LinkTarget.PROPERTY, prop),
        (by_owner, LinkTarget.OWNER, owner),  # household-wide: not rolled up
        (by_tenant, LinkTarget.TENANT, tenant),  # personal data, via tenancy_tenant: not rolled up
        (sibling, LinkTarget.PROPERTY, other),
        (sibling_unit, LinkTarget.UNIT, other_unit),
    ]
    for doc, target_type, target_id in links:
        _link(app_engine, a, doc.id, target_type, target_id)

    related = _related(app_engine, a, LinkTarget.PROPERTY, prop)

    assert related == [
        RelatedDocument(document=direct, via=[_ref(LinkTarget.PROPERTY, prop)]),
        RelatedDocument(document=by_unit, via=[_ref(LinkTarget.UNIT, unit)]),
        RelatedDocument(document=by_tenancy, via=[_ref(LinkTarget.TENANCY, tenancy)]),
        RelatedDocument(document=by_loan, via=[_ref(LinkTarget.LOAN, loan)]),
        RelatedDocument(
            document=both,
            via=[
                _ref(LinkTarget.PROPERTY, prop),
                *(_ref(LinkTarget.UNIT, u) for u in sorted([unit, unit_2])),
                _ref(LinkTarget.TENANCY, tenancy),
            ],
        ),
    ]
    # The direct read is unchanged: only the property's own links.
    with workspace_transaction(app_engine, a) as conn:
        assert list_documents(conn, target_type=LinkTarget.PROPERTY, target_id=prop) == [
            direct,
            both,
        ]
    assert _related(app_engine, a, LinkTarget.PROPERTY, other) == [
        RelatedDocument(document=sibling, via=[_ref(LinkTarget.PROPERTY, other)]),
        RelatedDocument(document=sibling_unit, via=[_ref(LinkTarget.UNIT, other_unit)]),
    ]


def test_a_unit_rolls_up_its_tenancies_but_not_its_siblings(app_engine, workspaces, blob_store):
    a, _ = workspaces
    docs = _uploads(app_engine, blob_store, a, 7)
    direct, first, second, sibling, sibling_tenancy, of_property, of_loan = docs
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
        unit = insert_unit(raw, a, prop)
        tenancy_1 = insert_tenancy(raw, a, unit, "2023-01-01", "2023-12-31")
        tenancy_2 = insert_tenancy(raw, a, unit, "2024-06-01")
        other_unit = insert_unit(raw, a, prop, label="WE 4")
        other_tenancy = insert_tenancy(raw, a, other_unit, "2024-06-01")
        loan = _insert_loan(raw, a, prop)
    links = [
        (direct, LinkTarget.UNIT, unit),
        (first, LinkTarget.TENANCY, tenancy_1),
        (second, LinkTarget.TENANCY, tenancy_2),
        (sibling, LinkTarget.UNIT, other_unit),
        (sibling_tenancy, LinkTarget.TENANCY, other_tenancy),
        (of_property, LinkTarget.PROPERTY, prop),  # the parent's: not on the unit
        (of_loan, LinkTarget.LOAN, loan),
    ]
    for doc, target_type, target_id in links:
        _link(app_engine, a, doc.id, target_type, target_id)

    assert _related(app_engine, a, LinkTarget.UNIT, unit) == [
        RelatedDocument(document=direct, via=[_ref(LinkTarget.UNIT, unit)]),
        RelatedDocument(document=first, via=[_ref(LinkTarget.TENANCY, tenancy_1)]),
        RelatedDocument(document=second, via=[_ref(LinkTarget.TENANCY, tenancy_2)]),
    ]
    assert [r.document for r in _related(app_engine, a, LinkTarget.PROPERTY, prop)] == docs


def test_the_roll_up_of_an_empty_or_unknown_target_is_empty(app_engine, workspaces, blob_store):
    a, _ = workspaces
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
    assert _related(app_engine, a, LinkTarget.PROPERTY, prop) == []
    assert _related(app_engine, a, LinkTarget.UNIT, uuid.uuid4()) == []


def test_the_roll_up_is_isolated_by_workspace(app_engine, workspaces, blob_store):
    a, b = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
        unit = insert_unit(raw, a, prop)
    _link(app_engine, a, doc.id, LinkTarget.UNIT, unit)
    assert [r.document for r in _related(app_engine, a, LinkTarget.PROPERTY, prop)] == [doc]

    assert _related(app_engine, b, LinkTarget.PROPERTY, prop) == []
    assert _related(app_engine, b, LinkTarget.UNIT, unit) == []


@pytest.mark.parametrize(
    "target_type", [LinkTarget.OWNER, LinkTarget.TENANT, LinkTarget.TENANCY, LinkTarget.LOAN]
)
def test_only_a_property_or_a_unit_rolls_up(app_engine, workspaces, target_type):
    a, _ = workspaces
    with workspace_transaction(app_engine, a) as conn:
        with recorded_sql(conn) as statements, pytest.raises(ValueError, match="roll up"):
            list_related_documents(conn, target_type=target_type, target_id=uuid.uuid4())
        assert statements == []


@pytest.mark.parametrize("bad_id", [None, "not-a-uuid"], ids=["none", "string"])
def test_the_roll_up_rejects_a_non_uuid_target_before_any_sql(app_engine, workspaces, bad_id):
    a, _ = workspaces
    with workspace_transaction(app_engine, a) as conn:
        with recorded_sql(conn) as statements, pytest.raises(TypeError, match="target_id"):
            list_related_documents(conn, target_type=LinkTarget.PROPERTY, target_id=bad_id)
        assert statements == []


# --- retention -------------------------------------------------------------------

RETENTION = load("retention")
assert isinstance(RETENTION, RetentionFixture)
OWNERSHIP_PATHS = [LinkTarget.PROPERTY, LinkTarget.UNIT, LinkTarget.TENANCY, LinkTarget.LOAN]
TENANCY_PATHS = [LinkTarget.TENANCY, LinkTarget.TENANT]


def _retention_params():
    for case in RETENTION.cases:
        if case.input.cost_basis_reference:
            # Built from a cost-evidence record, not a link: covered by
            # test_cost_evidence_is_an_ownership_record_without_the_subtype.
            continue
        if case.input.ownership_end_dates:
            paths = OWNERSHIP_PATHS
        elif case.input.tenancy_end_dates:
            paths = TENANCY_PATHS
        else:
            yield pytest.param(case, None, id=case.id)
            continue
        for path in paths:
            yield pytest.param(case, path, id=f"{case.id}-via-{path}")


def _sorted_dates(dates):
    return sorted(dates, key=lambda d: (d is None, d or date.min))


@pytest.mark.parametrize(("case", "path"), list(_retention_params()))
def test_retention_matches_the_fixture_through_each_link_path(
    app_engine, workspaces, blob_store, case, path
):
    """Rebuild each retention.yaml case in the database, reaching its properties and
    tenancies through `path`; the gathered facts and the result must match the fixture."""
    a, _ = workspaces
    facts = case.input
    received_at = datetime.combine(facts.received_on, time(12), UTC)
    doc = _upload(app_engine, blob_store, a, _bytes(case.id), now=received_at)
    if facts.type is not None:
        typing = DocumentTyping(
            type=facts.type,
            subtype=facts.subtype,
            tax_year=facts.tax_year,
            document_date=facts.document_date,
        )
        _type(app_engine, a, doc.id, typing)

    # A tenancy link also reaches its unit's property, and that property's end counts too.
    extra_ownership, extra_tenancy = [], []
    with _seed(app_engine, a) as raw:
        targets = []
        for ended_on in facts.ownership_end_dates:
            prop = insert_property(raw, a, ownership_ended_on=ended_on)
            unit = insert_unit(raw, a, prop)
            if path is LinkTarget.TENANCY:
                extra_tenancy.append(None)
            targets.append(
                {
                    LinkTarget.PROPERTY: lambda p=prop: p,
                    LinkTarget.UNIT: lambda u=unit: u,
                    LinkTarget.TENANCY: lambda u=unit: insert_tenancy(raw, a, u, "2024-06-01"),
                    LinkTarget.LOAN: lambda p=prop: _insert_loan(raw, a, p),
                }[path]()
            )
        for end in facts.tenancy_end_dates:
            tenancy = _tenancy(raw, a, end)
            if path is LinkTarget.TENANT:
                tenant = _insert_tenant(raw, a)
                _add_party(raw, a, tenancy, tenant)
                targets.append(tenant)
            else:
                extra_ownership.append(None)  # the fresh property is still owned
                targets.append(tenancy)
    for target_id in targets:
        _link(app_engine, a, doc.id, path, target_id)

    expected_facts = facts.model_copy(
        update={
            "ownership_end_dates": _sorted_dates(facts.ownership_end_dates + extra_ownership),
            "tenancy_end_dates": _sorted_dates(facts.tenancy_end_dates + extra_tenancy),
        }
    )
    with workspace_transaction(app_engine, a) as conn:
        gathered = document_retention_input(conn, doc.id)
        assert (
            gathered.model_copy(
                update={
                    "ownership_end_dates": _sorted_dates(gathered.ownership_end_dates),
                    "tenancy_end_dates": _sorted_dates(gathered.tenancy_end_dates),
                }
            )
            == expected_facts
        )
        assert document_retention(conn, doc.id, case.policy) == case.expect


def test_retention_counts_each_property_once_across_link_paths(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    _type(app_engine, a, doc.id, PURCHASE_CONTRACT)
    with _seed(app_engine, a) as raw:
        owned = insert_property(raw, a)
        sold = insert_property(raw, a, ownership_ended_on="2035-06-30")
        links = [
            (LinkTarget.PROPERTY, owned),
            (LinkTarget.UNIT, insert_unit(raw, a, owned)),
            (LinkTarget.LOAN, _insert_loan(raw, a, owned)),
            (LinkTarget.UNIT, insert_unit(raw, a, sold)),
            (LinkTarget.LOAN, _insert_loan(raw, a, sold)),
        ]
    for target_type, target_id in links:
        _link(app_engine, a, doc.id, target_type, target_id)

    with workspace_transaction(app_engine, a) as conn:
        gathered = document_retention_input(conn, doc.id)
        assert _sorted_dates(gathered.ownership_end_dates) == [date(2035, 6, 30), None]
        assert gathered.tenancy_end_dates == []
        assert document_retention(conn, doc.id).hold is RetentionHold.WHILE_OWNED

    # Once the second property is sold too, the latest end counts.
    with _seed(app_engine, a) as raw:
        raw.execute("UPDATE property SET ownership_ended_on = '2031-01-31' WHERE id = %s", (owned,))
    with workspace_transaction(app_engine, a) as conn:
        result = document_retention(conn, doc.id)
    assert (result.retention_class, result.retain_until) == (
        RetentionClass.OWNERSHIP_RECORDS,
        date(2045, 12, 31),
    )


def test_retention_through_a_tenant_uses_all_its_tenancies_and_no_property(
    app_engine, workspaces, blob_store
):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    _type(app_engine, a, doc.id, LEASE)
    with _seed(app_engine, a) as raw:
        tenant = _insert_tenant(raw, a)
        for end in ("2025-06-30", "2027-03-31"):
            _add_party(raw, a, _tenancy(raw, a, end), tenant)
        _tenancy(raw, a, None)  # someone else's running tenancy: not reached
    _link(app_engine, a, doc.id, LinkTarget.TENANT, tenant)

    with workspace_transaction(app_engine, a) as conn:
        gathered = document_retention_input(conn, doc.id)
        assert _sorted_dates(gathered.tenancy_end_dates) == [date(2025, 6, 30), date(2027, 3, 31)]
        assert gathered.ownership_end_dates == []
        result = document_retention(conn, doc.id)
    assert (result.retention_class, result.retain_until) == (
        RetentionClass.TENANCY_RECORDS,
        date(2031, 12, 31),
    )


def test_retention_through_a_tenant_follows_a_tenancy_they_were_removed_from(
    app_engine, workspaces, blob_store
):
    """F1 (decided 6 Nov 2026): a tenant removed from a tenancy by set_parties no longer has a
    tenancy_tenant row, but documents linked to them still follow that tenancy's end."""
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    _type(app_engine, a, doc.id, LEASE)
    with _seed(app_engine, a) as raw:
        unit = insert_unit(raw, a, insert_property(raw, a))
        stays, leaves = _insert_tenant(raw, a), _insert_tenant(raw, a)
    lease = TenancyCreate(
        unit_id=unit,
        parties=[
            TenancyParty(tenant_id=stays, role=TenancyRole.PRIMARY),
            TenancyParty(tenant_id=leaves, role=TenancyRole.CO_TENANT),
        ],
        start_date=date(2024, 6, 1),
        end_date=date(2027, 3, 31),
        rent_model=RentModel.FIXED,
    )
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        tenancy = create_tenancy(ctx, lease, initial_rent=None, iban_key=TEST_IBAN_KEY)
        set_parties(ctx, tenancy.id, [TenancyParty(tenant_id=stays, role=TenancyRole.PRIMARY)])
    _link(app_engine, a, doc.id, LinkTarget.TENANT, leaves)

    with workspace_transaction(app_engine, a) as conn:
        assert document_retention_input(conn, doc.id).tenancy_end_dates == [date(2027, 3, 31)]
        result = document_retention(conn, doc.id)
    assert (result.retention_class, result.retain_until) == (
        RetentionClass.TENANCY_RECORDS,
        date(2031, 12, 31),
    )


def test_an_owner_link_reaches_no_property(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    _type(app_engine, a, doc.id, PURCHASE_CONTRACT)
    with _seed(app_engine, a) as raw:
        owner = _insert_owner(raw, a)
    _link(app_engine, a, doc.id, LinkTarget.OWNER, owner)

    with workspace_transaction(app_engine, a) as conn:
        gathered = document_retention_input(conn, doc.id)
        assert (gathered.ownership_end_dates, gathered.tenancy_end_dates) == ([], [])
        assert document_retention(conn, doc.id).hold is RetentionHold.UNLINKED


def test_cost_basis_evidence_is_kept_while_the_property_is_owned(
    app_engine, workspaces, blob_store
):
    """Spec amendment 1: a capitalised renovation invoice is an ownership record, not a
    10-year tax record (which would have been 2035-12-31 from its tax year)."""
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    typing = DocumentTyping(
        type=DocumentType.INVOICE,
        subtype=DocumentSubtype.COST_BASIS_EVIDENCE,
        tax_year=2025,
        document_date=date(2025, 9, 1),
    )
    _type(app_engine, a, doc.id, typing)
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
    _link(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)

    with workspace_transaction(app_engine, a) as conn:
        result = document_retention(conn, doc.id)
    assert (result.retention_class, result.hold, result.retain_until) == (
        RetentionClass.OWNERSHIP_RECORDS,
        RetentionHold.WHILE_OWNED,
        None,
    )


@pytest.mark.parametrize(
    ("ownership_ended_on", "expected"),
    [
        (None, (RetentionHold.WHILE_OWNED, None)),
        (date(2035, 6, 30), (None, date(2045, 12, 31))),
    ],
    ids=["while-owned", "after-sale"],
)
def test_cost_evidence_is_an_ownership_record_without_the_subtype(
    app_engine, workspaces, blob_store, ownership_ended_on, expected
):
    """Spec amendment 1 (Luis, 5 Nov 2026): a document recorded as the evidence of an
    acquisition cost backs the AfA basis, so it is kept for the life of ownership + 10 years
    even when typed as a plain invoice and never linked to the property."""
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    _type(app_engine, a, doc.id, DocumentTyping(type=DocumentType.INVOICE, tax_year=2025))
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a, ownership_ended_on=ownership_ended_on)
        raw.execute(
            "INSERT INTO property_acquisition_cost (workspace_id, property_id, kind, amount,"
            " document_id) VALUES (%s, %s, 'notary', 3400.00, %s)",
            (a, prop, doc.id),
        )

    with workspace_transaction(app_engine, a) as conn:
        result = document_retention(conn, doc.id)
    assert result.retention_class is RetentionClass.OWNERSHIP_RECORDS
    assert (result.hold, result.retain_until) == expected


def test_unlinking_changes_retention(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes())
    _type(app_engine, a, doc.id, PURCHASE_CONTRACT)
    with _seed(app_engine, a) as raw:
        prop = insert_property(raw, a)
    _link(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)
    with workspace_transaction(app_engine, a) as conn:
        assert document_retention(conn, doc.id).hold is RetentionHold.WHILE_OWNED

    _unlink(app_engine, a, doc.id, LinkTarget.PROPERTY, prop)

    with workspace_transaction(app_engine, a) as conn:
        assert document_retention(conn, doc.id).hold is RetentionHold.UNLINKED


@pytest.mark.parametrize("session_tz", ["UTC", "Europe/Berlin"])
@pytest.mark.parametrize(
    ("received_at", "received_on", "retain_until"),
    [
        (datetime(2026, 12, 31, 22, 59, tzinfo=UTC), date(2026, 12, 31), date(2036, 12, 31)),
        (datetime(2026, 12, 31, 23, 30, tzinfo=UTC), date(2027, 1, 1), date(2037, 12, 31)),
    ],
    ids=["23:59-in-berlin", "00:30-in-berlin"],
)
def test_received_on_is_the_berlin_date_whatever_the_session_time_zone(
    app_engine, workspaces, blob_store, session_tz, received_at, received_on, retain_until
):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes(), now=received_at)
    _type(app_engine, a, doc.id, DocumentTyping(type=DocumentType.LETTER))

    with workspace_transaction(app_engine, a) as conn:
        conn.execute(text(f"SET LOCAL TimeZone = '{session_tz}'"))
        assert document_retention_input(conn, doc.id).received_on == received_on
        result = document_retention(conn, doc.id)
    assert (result.retention_class, result.retain_until) == (
        RetentionClass.TAX_RECORDS,
        retain_until,
    )


# --- hand fields over an AI reading (phase 2 D37) -------------------------------------


def _cited(value, quote="…") -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote=quote),
        checks=["cited", "value_in_quote", "validates"],
        confidence="high",
    )


INVOICE_READING = InvoiceExtraction(
    title=_cited("Notarrechnung"),
    document_date=_cited("2024-03-20"),
    sender=_cited("Notar Dr. Beispiel"),
    subject=_cited("Beurkundung Kaufvertrag"),
    amount_total=_cited("4321.10"),
    amount_net=_cited("3631.18"),
    reference=_cited("UR 123/2024"),
)
NOTARY = DocumentTyping(type=DocumentType.INVOICE, title="Notarrechnung", tax_year=2024)


def _read(engine, ws, doc_id, reading=INVOICE_READING):
    """What the document_read job records: an `ai` version tied to its ai_call."""
    with workspace_transaction(engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.AI, "claude-test-model"), now=NOW)
        call_id = conn.execute(
            insert(ai_call_table)
            .values(
                workspace_id=ws,
                job="extract",
                document_id=doc_id,
                model="claude-test-model",
                prompt_version="test-1",
                input_sha256=b"\x03" * 32,
            )
            .returning(ai_call_table.c.id)
        ).scalar_one()
        return record_ai_extraction(ctx, doc_id, reading, ai_call_id=call_id)


def _hand_view(engine, ws, doc_id):
    with workspace_transaction(engine, ws) as conn:
        return hand_fields(current_extraction(conn, doc_id))


def test_an_unchanged_hand_section_over_a_reading_writes_no_version(
    app_engine, workspaces, blob_store
):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes("d37-unchanged"))
    reading = _read(app_engine, a, doc.id)
    view = _hand_view(app_engine, a, doc.id)
    assert view.available == ["amount_total", "counterparty", "reference", "notes"]
    assert view.values == ManualDocumentFields(
        amount_total=Decimal("4321.10"),
        counterparty="Notar Dr. Beispiel",
        reference="UR 123/2024",
        notes="Beurkundung Kaufvertrag",
    )

    _type(app_engine, a, doc.id, NOTARY, view.values)
    with workspace_transaction(app_engine, a) as conn:
        assert current_extraction(conn, doc.id) == reading
    typed = _events(app_engine, a, doc.id)[-1]
    assert typed.type is EventType.DOCUMENT_TYPED
    assert typed.payload["extraction_version"] is None


def test_a_changed_hand_field_writes_a_manual_version_of_the_same_kind(
    app_engine, workspaces, blob_store
):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes("d37-changed"))
    reading = _read(app_engine, a, doc.id)
    view = _hand_view(app_engine, a, doc.id)

    _type(
        app_engine,
        a,
        doc.id,
        NOTARY,
        view.values.model_copy(update={"amount_total": Decimal("4300")}),
    )
    with workspace_transaction(app_engine, a) as conn:
        current = current_extraction(conn, doc.id)
    assert (current.version, current.kind, current.source, current.ai_call_id) == (
        reading.version + 1,
        "invoice",
        "manual",
        None,
    )
    assert current.fields.amount_total == ExtractedValue(
        value="4300.00", checks=["validates"], confidence="high"
    )
    # Every other field is the reading's, source and all.
    assert current.fields.model_copy(update={"amount_total": reading.fields.amount_total}) == (
        reading.fields
    )
    assert _events(app_engine, a, doc.id)[-1].payload["extraction_version"] == current.version


def test_clearing_a_shown_hand_field_makes_it_missing(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes("d37-cleared"))
    reading = _read(app_engine, a, doc.id)
    view = _hand_view(app_engine, a, doc.id)

    _type(app_engine, a, doc.id, NOTARY, view.values.model_copy(update={"reference": None}))
    with workspace_transaction(app_engine, a) as conn:
        current = current_extraction(conn, doc.id)
    assert (current.kind, current.source) == ("invoice", "manual")
    assert current.fields.reference == ExtractedValue.missing()
    assert current.fields.sender == reading.fields.sender


def test_a_hand_field_the_reading_has_no_place_for_is_refused(app_engine, workspaces, blob_store):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes("d37-mismatch"))
    reading = _read(app_engine, a, doc.id)
    view = _hand_view(app_engine, a, doc.id)
    six_nulls_but_one = view.values.model_copy(update={"period_start": date(2024, 1, 1)})

    with pytest.raises(RuleError) as caught:
        _type(app_engine, a, doc.id, NOTARY, six_nulls_but_one)
    assert caught.value.code == "document.extraction_kind_mismatch"
    with workspace_transaction(app_engine, a) as conn:
        assert current_extraction(conn, doc.id) == reading
        assert get_document(conn, doc.id).typing is None  # nothing was written


def test_hand_fields_over_a_manual_extraction_keep_the_phase_1_behaviour(
    app_engine, workspaces, blob_store
):
    a, _ = workspaces
    doc = _upload(app_engine, blob_store, a, _bytes("d37-manual"))
    _type(app_engine, a, doc.id, INVOICE, FIELDS_1)
    view = _hand_view(app_engine, a, doc.id)
    assert (view.values, len(view.available)) == (FIELDS_1, 6)

    with_period = FIELDS_1.model_copy(update={"period_start": date(2026, 1, 1)})
    _type(app_engine, a, doc.id, INVOICE, with_period)
    with workspace_transaction(app_engine, a) as conn:
        current = current_extraction(conn, doc.id)
    assert (current.version, current.kind, current.fields) == (2, "manual", with_period)
