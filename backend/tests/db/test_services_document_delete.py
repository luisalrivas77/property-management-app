"""„Dokument löschen“ (D193): a document nothing depends on is deleted with its own readings
and suggestions; anything that depends on it blocks with a code. Services under hausbuch_app
and RLS, with a LocalBlobStore; the dependent rows are seeded as the superuser."""

import contextlib
import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from db.db_support import _admin, insert_property
from sqlalchemy.exc import OperationalError

from hausbuch.schemas.document import DocumentTyping, ManualDocumentFields
from hausbuch.schemas.enums import DocumentSource, DocumentType, EntityType, LinkTarget
from hausbuch.schemas.events import DocumentDeleted, EventType
from hausbuch.services import documents as svc
from hausbuch.services.errors import NotFoundError
from hausbuch.services.events import list_events
from hausbuch.storage import blob_key

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
PDF = "application/pdf"


def _bytes(label: str = "doc") -> bytes:
    return f"%PDF-1.7 {label} {uuid.uuid4()}".encode()


@pytest.fixture
def ws(fresh_workspaces):
    return fresh_workspaces[0]


@pytest.fixture
def upload(run, blob_store, ws):
    def _upload(data: bytes | None = None, *, workspace=None, filename="falsch.pdf"):
        return run(
            workspace or ws,
            svc.upload_document,
            blob_store,
            data or _bytes(),
            media_type=PDF,
            source=DocumentSource.UPLOAD,
            original_filename=filename,
            now=NOW,
        )

    return _upload


@pytest.fixture
def admin(dbname):
    with _admin(dbname) as conn:
        yield conn


def _delete(run, ws, document_id):
    return run(ws, svc.delete_document, document_id, now=NOW)


def _blocked(run, ws, document_id) -> list[str]:
    with pytest.raises(svc.DocumentInUseError) as caught:
        _delete(run, ws, document_id)
    return list(caught.value.codes)


def _count(admin, table: str, column: str, value) -> int:
    return admin.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)).fetchone()[
        0
    ]


def _ai_call(admin, ws, document_id) -> uuid.UUID:
    return admin.execute(
        "INSERT INTO ai_call (workspace_id, job, document_id, model, prompt_version,"
        " input_sha256) VALUES (%s, 'extract', %s, 'model', 'v1', %s) RETURNING id",
        (ws, document_id, hashlib.sha256(b"input").digest()),
    ).fetchone()[0]


def _proposal(admin, ws, document_id, status="ready", **extra) -> uuid.UUID:
    columns = {"workspace_id": ws, "document_id": document_id, "kind": "invoice", "status": status}
    if status == "applied":
        columns |= {
            "decided_at": NOW,
            "decisions": json.dumps({}),
            "applied_entities": json.dumps([]),
        }
    columns |= extra
    names = ", ".join(columns)
    marks = ", ".join(["%s"] * len(columns))
    return admin.execute(
        f"INSERT INTO proposal ({names}) VALUES ({marks}) RETURNING id", list(columns.values())
    ).fetchone()[0]


# --- Deleted -------------------------------------------------------------------------


def test_a_fresh_upload_with_a_waiting_suggestion_is_deleted_with_its_layers(
    run, read, upload, admin, ws, blob_store
):
    data = _bytes("wrong statement")
    document = upload(data)
    run(
        ws,
        svc.type_document,
        document.id,
        DocumentTyping(type=DocumentType.INVOICE, title="Rechnung Meier GmbH"),
        ManualDocumentFields(counterparty="Meier"),
        now=NOW,
    )
    call = _ai_call(admin, ws, document.id)
    extraction_id = admin.execute(
        "SELECT id FROM document_extraction WHERE document_id = %s", (document.id,)
    ).fetchone()[0]
    _proposal(admin, ws, document.id, "ready", ai_call_id=call, extraction_id=extraction_id)
    _proposal(admin, ws, document.id, "rejected", decided_at=NOW)
    assert read(ws, svc.deletion_blockers, document.id) == []
    key = blob_key(ws, hashlib.sha256(data).hexdigest())
    assert blob_store.exists(key)

    removed = _delete(run, ws, document.id)

    assert removed == svc.RemovedOriginal(hashlib.sha256(data).digest(), key)
    for table in ("document_extraction", "proposal", "ai_call"):
        assert _count(admin, table, "document_id", document.id) == 0, table
    assert _count(admin, "document", "id", document.id) == 0
    assert _count(admin, "document_file", "sha256", hashlib.sha256(data).digest()) == 0
    assert read(ws, svc.get_document, document.id) is None
    # The blob goes only after the commit (the route calls discard_original then).
    assert blob_store.exists(key)
    assert read(ws, svc.discard_original, blob_store, removed) is True
    assert not blob_store.exists(key)
    # The earlier events stay; the new one carries the typing only.
    events = read(ws, list_events, entity_type=EntityType.DOCUMENT, entity_id=document.id)
    assert [e.type for e in events][0] is EventType.DOCUMENT_UPLOADED
    deleted = events[-1]
    assert deleted.type is EventType.DOCUMENT_DELETED
    assert deleted.payload == DocumentDeleted(type=DocumentType.INVOICE).model_dump(mode="json")
    assert deleted.actor_type.value == "user"
    raw = json.dumps(deleted.payload)
    for secret in ("falsch.pdf", "Rechnung Meier", hashlib.sha256(data).hexdigest()):
        assert secret not in raw


def test_the_same_file_can_be_uploaded_again_after_deletion(run, read, upload, ws, blob_store):
    data = _bytes("again")
    first = upload(data)
    removed = _delete(run, ws, first.id)
    read(ws, svc.discard_original, blob_store, removed)

    second = upload(data)

    assert second.id != first.id
    assert second.created_at == NOW
    assert read(ws, svc.read_original, blob_store, second.id) == data


def test_the_blob_stays_when_a_tax_export_holds_the_same_bytes(
    run, read, upload, admin, ws, blob_store
):
    data = _bytes("exported sheet")
    document = upload(data)
    prop = insert_property(admin, ws)
    admin.execute(
        "INSERT INTO tax_export (workspace_id, property_id, tax_year, kind, rule_set_version,"
        " form_year, form_status, draft, file_sha256, size_bytes) VALUES (%s, %s, 2026,"
        " 'field_sheet', '2026.1', 2025, 'provisional', '{}', %s, %s)",
        (ws, prop, hashlib.sha256(data).digest(), len(data)),
    )

    assert _delete(run, ws, document.id) is None

    assert _count(admin, "document", "id", document.id) == 0
    assert _count(admin, "document_file", "sha256", hashlib.sha256(data).digest()) == 1
    assert blob_store.exists(blob_key(ws, hashlib.sha256(data).hexdigest()))


def test_discard_skips_a_blob_stored_again_meanwhile(run, read, upload, ws, blob_store):
    data = _bytes("race")
    removed = _delete(run, ws, upload(data).id)
    upload(data)  # stored again before the route removed the blob

    assert read(ws, svc.discard_original, blob_store, removed) is False
    assert blob_store.exists(removed.storage_key)


# --- Blocked -------------------------------------------------------------------------


def test_a_statement_import_blocks(run, read, upload, admin, ws):
    document = upload()
    account = admin.execute(
        "INSERT INTO account (workspace_id, label, bank, opening_balance, opening_balance_on,"
        " iban_masked, iban_hmac) VALUES (%s, 'Giro', 'Bank', 0, '2026-01-01',"
        " 'DE00 **** 0001', %s) RETURNING id",
        (ws, bytes(32)),
    ).fetchone()[0]
    admin.execute(
        "INSERT INTO statement_import (workspace_id, account_id, document_id, format)"
        " VALUES (%s, %s, %s, 'pdf')",
        (ws, account, document.id),
    )
    assert _blocked(run, ws, document.id) == [svc.BLOCK_STATEMENT_IMPORT]
    assert read(ws, svc.deletion_blockers, document.id) == [svc.BLOCK_STATEMENT_IMPORT]
    assert _count(admin, "document", "id", document.id) == 1


def test_an_allocation_blocks(run, upload, admin, ws):
    document = upload()
    account = admin.execute(
        "INSERT INTO account (workspace_id, label, bank, opening_balance, opening_balance_on,"
        " iban_masked, iban_hmac) VALUES (%s, 'Giro', 'Bank', 0, '2026-01-01',"
        " 'DE00 **** 0002', %s) RETURNING id",
        (ws, bytes(32)),
    ).fetchone()[0]
    statement = upload()
    import_id = admin.execute(
        "INSERT INTO statement_import (workspace_id, account_id, document_id, format)"
        " VALUES (%s, %s, %s, 'pdf') RETURNING id",
        (ws, account, statement.id),
    ).fetchone()[0]
    tx = admin.execute(
        "INSERT INTO transaction (workspace_id, account_id, import_id, line_hash, booking_date,"
        " amount) VALUES (%s, %s, %s, %s, '2026-03-01', -100.00) RETURNING id",
        (ws, account, import_id, hashlib.sha256(b"line").digest()),
    ).fetchone()[0]
    admin.execute(
        "INSERT INTO allocation (workspace_id, transaction_id, amount, category, method,"
        " confidence, document_id) VALUES (%s, %s, -100.00, 'repairs', 'user', 1, %s)",
        (ws, tx, document.id),
    )
    assert _blocked(run, ws, document.id) == [svc.BLOCK_ALLOCATION]


def test_links_block_both_ways(run, upload, admin, ws):
    document, proof = upload(), upload()
    prop = insert_property(admin, ws)
    run(ws, svc.link_document, document.id, LinkTarget.PROPERTY, prop, now=NOW)
    assert _blocked(run, ws, document.id) == [svc.BLOCK_LINK]

    run(ws, svc.unlink_document, document.id, LinkTarget.PROPERTY, prop, now=NOW)
    run(ws, svc.link_document, proof.id, LinkTarget.DOCUMENT, document.id, now=NOW)
    assert _blocked(run, ws, document.id) == [svc.BLOCK_LINKED_FROM_DOCUMENT]
    assert _blocked(run, ws, proof.id) == [svc.BLOCK_LINK]


def test_an_acquisition_cost_row_blocks(run, upload, admin, ws):
    document = upload()
    prop = insert_property(admin, ws)
    admin.execute(
        "INSERT INTO property_acquisition_cost (workspace_id, property_id, kind, amount,"
        " document_id) VALUES (%s, %s, 'notary', 1200.00, %s)",
        (ws, prop, document.id),
    )
    assert _blocked(run, ws, document.id) == [svc.BLOCK_ACQUISITION_COST]


def test_an_applied_suggestion_blocks(run, upload, admin, ws):
    document = upload()
    _proposal(admin, ws, document.id, "applied")
    assert _blocked(run, ws, document.id) == [svc.BLOCK_APPLIED_PROPOSAL]


def test_a_waiting_suggestion_of_another_document_naming_it_blocks(run, upload, admin, ws):
    document, other = upload(), upload()
    _proposal(
        admin,
        ws,
        other.id,
        "ready",
        target_mode="patch",
        targets=json.dumps([{"entity": "document", "id": str(document.id)}]),
    )
    assert _blocked(run, ws, document.id) == [svc.BLOCK_OTHER_PROPOSAL]
    assert _delete(run, ws, other.id) is not None  # its own proposal goes with it


def test_a_tax_export_citing_it_blocks(run, upload, admin, ws):
    document = upload()
    prop = insert_property(admin, ws)
    export_bytes = _bytes("archive")
    sha = hashlib.sha256(export_bytes).digest()
    admin.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, %s, 'application/zip', %s)",
        (ws, sha, len(export_bytes), blob_key(ws, sha.hex())),
    )
    draft = {"lines": [{"sources": [{"kind": "document", "id": str(document.id)}]}]}
    admin.execute(
        "INSERT INTO tax_export (workspace_id, property_id, tax_year, kind, rule_set_version,"
        " form_year, form_status, draft, file_sha256, size_bytes) VALUES (%s, %s, 2026,"
        " 'archive', '2026.1', 2025, 'provisional', %s, %s, %s)",
        (ws, prop, json.dumps(draft), sha, len(export_bytes)),
    )
    assert _blocked(run, ws, document.id) == [svc.BLOCK_TAX_EXPORT]


def test_every_reason_is_named(run, upload, admin, ws):
    document = upload()
    prop = insert_property(admin, ws)
    run(ws, svc.link_document, document.id, LinkTarget.PROPERTY, prop, now=NOW)
    _proposal(admin, ws, document.id, "applied")
    assert _blocked(run, ws, document.id) == [svc.BLOCK_APPLIED_PROPOSAL, svc.BLOCK_LINK]


# --- Isolation -----------------------------------------------------------------------


def test_another_workspaces_document_is_not_found(run, read, upload, fresh_workspaces, admin):
    a, b = fresh_workspaces
    document = upload(workspace=a)
    with pytest.raises(NotFoundError):
        _delete(run, b, document.id)
    assert read(b, svc.deletion_blockers, document.id) == []  # sees nothing, decides nothing
    assert _count(admin, "document", "id", document.id) == 1
    assert (
        read(a, list_events, entity_type=EntityType.DOCUMENT, entity_id=document.id)[-1].type
        is EventType.DOCUMENT_UPLOADED
    )


def test_a_model_call_cited_elsewhere_blocks(run, upload, admin, ws):
    document = upload()
    call = _ai_call(admin, ws, document.id)
    account = admin.execute(
        "INSERT INTO account (workspace_id, label, bank, opening_balance, opening_balance_on,"
        " iban_masked, iban_hmac) VALUES (%s, 'Giro', 'Bank', 0, '2026-01-01',"
        " 'DE00 **** 0003', %s) RETURNING id",
        (ws, bytes(32)),
    ).fetchone()[0]
    statement = upload()
    import_id = admin.execute(
        "INSERT INTO statement_import (workspace_id, account_id, document_id, format)"
        " VALUES (%s, %s, %s, 'pdf') RETURNING id",
        (ws, account, statement.id),
    ).fetchone()[0]
    tx = admin.execute(
        "INSERT INTO transaction (workspace_id, account_id, import_id, line_hash, booking_date,"
        " amount) VALUES (%s, %s, %s, %s, '2026-03-01', -100.00) RETURNING id",
        (ws, account, import_id, hashlib.sha256(b"line ai").digest()),
    ).fetchone()[0]
    admin.execute(
        "INSERT INTO allocation (workspace_id, transaction_id, amount, category, method,"
        " confidence, ai_call_id) VALUES (%s, %s, -100.00, 'repairs', 'llm', 0.9, %s)",
        (ws, tx, call),
    )
    assert _blocked(run, ws, document.id) == [svc.BLOCK_AI_CALL]
    assert _count(admin, "ai_call", "id", call) == 1


# --- The blob and a concurrent upload of the same bytes (review F1) -------------------
#
# Each test holds one transaction open and runs the competing step on a second connection with
# a short lock_timeout: it must wait (and so time out) instead of interleaving.


@pytest.fixture
def tx(app_engine):
    """tx(ws): an open workspace transaction as a test user, as a context manager yielding a
    CommandContext; leaving the block commits it."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command

    @contextlib.contextmanager
    def _tx(ws, *, lock_timeout: str | None = None):
        with workspace_transaction(app_engine, ws) as conn:
            if lock_timeout is not None:
                conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{lock_timeout}'")
            yield new_command(conn, ws, Actor(ActorType.USER, "user:test"), now=NOW)

    return _tx


def _upload_in(ctx, store, data):
    return svc.upload_document(
        ctx, store, data, media_type=PDF, source=DocumentSource.UPLOAD, original_filename="x.pdf"
    )


def test_discard_waits_for_an_upload_of_the_same_bytes(run, read, upload, ws, blob_store, tx):
    data = _bytes("race upload first")
    removed = _delete(run, ws, upload(data).id)
    with tx(ws) as uploading:
        again = _upload_in(uploading, blob_store, data)
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            tx(ws, lock_timeout="200ms") as other,
        ):
            svc.discard_original(other.conn, blob_store, removed)
    # The upload committed its row, so the blob it relies on stays.
    assert read(ws, svc.discard_original, blob_store, removed) is False
    assert blob_store.exists(removed.storage_key)
    assert read(ws, svc.get_document, again.id) == again


def test_an_upload_waits_for_a_discard_in_flight(run, read, upload, ws, blob_store, tx):
    data = _bytes("race discard first")
    removed = _delete(run, ws, upload(data).id)
    with tx(ws) as discarding:
        assert svc.discard_original(discarding.conn, blob_store, removed) is True
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            tx(ws, lock_timeout="200ms") as other,
        ):
            _upload_in(other, blob_store, data)
    # After the discard, the upload puts the blob anew.
    again = upload(data)
    assert blob_store.exists(removed.storage_key)
    assert read(ws, svc.get_document, again.id) == again


def test_a_re_upload_waits_for_a_deletion_in_flight(run, read, upload, ws, blob_store, tx):
    data = _bytes("race delete")
    document = upload(data)
    with tx(ws) as deleting:
        svc.delete_document(deleting, document.id)
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            tx(ws, lock_timeout="200ms") as other,
        ):
            _upload_in(other, blob_store, data)
    # Once the deletion committed, the same bytes are a new document, not the deleted one.
    again = upload(data)
    assert again.id != document.id
    assert read(ws, svc.get_document, again.id) == again
