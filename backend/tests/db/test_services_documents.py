"""The document vault end to end: services under hausbuch_app and RLS, with a LocalBlobStore."""

import hashlib
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from decimal import Decimal

import psycopg
import pytest
from conftest import OWNER, insert_property, insert_tenancy, insert_unit
from fixture_models import RetentionFixture, load
from sqlalchemy import event as sa_event
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.schemas.document import DocumentTyping, ManualDocumentFields
from hausbuch.schemas.enums import (
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    EntityType,
    LinkTarget,
    RetentionClass,
    RetentionHold,
)
from hausbuch.schemas.events import EventType
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
    link_document,
    list_documents,
    list_links,
    read_original,
    type_document,
    unlink_document,
    upload_document,
)
from hausbuch.services.errors import NotFoundError
from hausbuch.services.events import list_events
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


# Small SQL seeds for link targets conftest has no helper for.


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
    assert [e.payload for e in typed_events] == [
        {"type": "invoice", "subtype": None, "tax_year": 2026, "extraction_version": 1},
        {"type": "invoice", "subtype": None, "tax_year": 2026, "extraction_version": 2},
        {"type": "invoice", "subtype": None, "tax_year": 2025, "extraction_version": None},
    ]


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


# --- retention -------------------------------------------------------------------

RETENTION = load("retention")
assert isinstance(RETENTION, RetentionFixture)
OWNERSHIP_PATHS = [LinkTarget.PROPERTY, LinkTarget.UNIT, LinkTarget.TENANCY, LinkTarget.LOAN]
TENANCY_PATHS = [LinkTarget.TENANCY, LinkTarget.TENANT]


def _retention_params():
    for case in RETENTION.cases:
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
