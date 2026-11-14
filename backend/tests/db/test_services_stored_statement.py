"""„Auf Konto importieren“ (phase 6 D176 (3), step 4): a card statement or a depot's cash
statement uploaded through Dokumente is refused by the document job (proposal.bank_file_not_read)
and then imported onto its account from the stored original, with no second upload, as
hausbuch_app under RLS. Invented statements only (tests/test_card_pdf.py,
tests/test_depot_pdf.py).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import text
from test_card_pdf import case_bytes, draw
from test_depot_pdf import ROWS, page
from test_jobs_document_read import _settings as _test_settings
from test_services_card_import import _card, _first

from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.session import workspace_transaction
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate, StatementFormat
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import (
    AccountKind,
    AccountPurpose,
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    ImportStatus,
    ProposalStatus,
)
from hausbuch.services import documents, proposals, review, statements
from hausbuch.services.accounts import create_account, get_account
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import NotFoundError
from hausbuch.services.statements import (
    document_statement_imports,
    import_statement,
    import_stored_statement,
    list_imports,
    statement_offer,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


@pytest.fixture
def hook_calls(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        statements, "_on_reconciled", lambda ctx, import_id: calls.append(import_id)
    )
    return calls


def _card_bytes() -> bytes:
    return case_bytes(_first())


def _depot_bytes(rows=None, total="722,20", closing="822,20") -> bytes:
    return draw([page(rows=rows or ROWS, total=total, closing=closing)])


def _depot(run, ws, **overrides):
    data = {
        "label": "Globales Portfolio",
        "bank": "Beispiel-Vermögensverwaltung",
        "kind": AccountKind.DEPOT,
        "provider": "quirion",
        "opening_balance": Decimal("100.00"),
        "opening_balance_on": datetime(2026, 3, 1).date(),
    } | overrides
    return run(ws, create_account, AccountCreate(**data), iban_key=TEST_IBAN_KEY, now=T0)


def _giro(run, ws):
    return run(
        ws,
        create_account,
        AccountCreate(
            label="Girokonto",
            bank="Beispielbank",
            iban="DE50100000000000003008",
            opening_balance=Decimal("0.00"),
            opening_balance_on=T0.date(),
            purpose=AccountPurpose.PRIVATE,
        ),
        iban_key=TEST_IBAN_KEY,
    )


def _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, data: bytes):
    """The file uploaded through Dokumente with ai on and its read run: the job refuses it
    with proposal.bank_file_not_read before any model call. The document and its proposal."""
    actor = Actor(ActorType.USER, "user:test")
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, actor, now=T0)
        doc = documents.upload_document(
            ctx,
            blob_store,
            data,
            media_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename="scan.pdf",
        )
        queued = proposals.request_read(ctx, doc.id)
    client = FakeModelClient([])
    deps = JobDeps(
        engine=app_engine,
        settings=_test_settings(tmp_path),
        blob_store=blob_store,
        model_client=client,
        clock=lambda: T0,
    )
    try:
        run_document_read(
            deps, workspace_id=ws, document_id=doc.id, proposal_id=queued.id, attempt=0
        )
    finally:
        with app_engine.begin() as conn:
            conn.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'document_read'"))
    assert client.requests == []
    with workspace_transaction(app_engine, ws) as conn:
        failed = proposals.get_proposal(conn, queued.id)
    assert failed.status is ProposalStatus.FAILED
    assert failed.error_code == proposals.PROPOSAL_BANK_FILE_NOT_READ
    return doc, queued


def _offer(read, blob_store, ws, document_id):
    return read(ws, statement_offer, blob_store, document_id, iban_key=TEST_IBAN_KEY)


def _import_stored(run, blob_store, ws, document_id, account_id, n=0):
    return run(
        ws,
        import_stored_statement,
        blob_store,
        document_id,
        account_id,
        iban_key=TEST_IBAN_KEY,
        now=T0 + timedelta(minutes=n),
    )


def _read_failed(read, ws):
    return [i for i in read(ws, review.inbox) if i.kind is InboxItemKind.READ_FAILED]


@pytest.mark.parametrize("kind", ["card", "depot"])
def test_a_refused_statement_is_imported_from_its_stored_original(
    kind, run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    data = _card_bytes() if kind == "card" else _depot_bytes()
    doc, queued = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, data)
    (card,) = _read_failed(read, ws)
    assert card.error_code == proposals.PROPOSAL_BANK_FILE_NOT_READ
    assert card.question == "Auf welches Konto soll sie importiert werden?"
    # Typed by code when refused, so Dokumente shows what it is before the import.
    typing = read(ws, documents.get_document, doc.id).typing
    assert typing is not None and typing.type is DocumentType.STATEMENT
    assert typing.subtype is (None if kind == "card" else DocumentSubtype.DEPOT_STATEMENT)

    account = _card(run, ws) if kind == "card" else _depot(run, ws)
    offer = _offer(read, blob_store, ws, doc.id)
    assert offer.format is (
        StatementFormat.CARD_PDF if kind == "card" else StatementFormat.DEPOT_PDF
    )
    assert offer.account_kind is account.kind
    assert [c.account_id for c in offer.candidates] == [account.id]
    assert offer.suggested_account_id == account.id  # the only one, without a number yet

    imported = _import_stored(run, blob_store, ws, doc.id, account.id)
    assert imported.document_id == doc.id  # no second document (D9)
    assert imported.account_id == account.id
    assert imported.status is ImportStatus.RECONCILED  # as an upload on the account's page
    assert hook_calls == [imported.id]
    assert read(ws, get_account, account.id).external_ref_masked is not None
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED
    assert _read_failed(read, ws) == []
    assert _offer(read, blob_store, ws, doc.id) is None
    (on,) = read(ws, document_statement_imports, doc.id)
    assert (on.import_id, on.account_id, on.account_label) == (
        imported.id,
        account.id,
        account.label,
    )

    # Idempotent: the same document onto the same account returns the same import.
    again = _import_stored(run, blob_store, ws, doc.id, account.id, n=1)
    assert again.id == imported.id and again.created_at == imported.created_at
    assert [i.id for i in read(ws, list_imports, account.id)] == [imported.id]
    assert hook_calls == [imported.id]


@pytest.mark.parametrize("kind", ["card", "depot"])
def test_the_imported_file_is_the_same_as_one_uploaded_on_the_account(
    kind, run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """The account page's upload of the same bytes after „Auf Konto importieren“ finds that
    import: one document, one import."""
    ws, _ = fresh_workspaces
    data = _card_bytes() if kind == "card" else _depot_bytes()
    doc, _ = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, data)
    account = _card(run, ws) if kind == "card" else _depot(run, ws)
    imported = _import_stored(run, blob_store, ws, doc.id, account.id)
    uploaded = run(
        ws,
        import_statement,
        blob_store,
        account.id,
        data,
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        now=T0 + timedelta(minutes=2),
    )
    assert uploaded.id == imported.id


def test_an_account_of_another_kind_is_refused(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    card_doc, card_read = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, _card_bytes())
    depot = _depot(run, ws)
    giro = _giro(run, ws)
    for account in (depot, giro):
        with pytest.raises(RuleError) as caught:
            _import_stored(run, blob_store, ws, card_doc.id, account.id)
        assert caught.value.code == rules.ACCOUNT_MISMATCH
    # Nothing was written: the card still waits, its offer has no card account to name.
    assert read(ws, proposals.get_proposal, card_read.id).status is ProposalStatus.FAILED
    offer = _offer(read, blob_store, ws, card_doc.id)
    assert offer.account_kind is AccountKind.CREDIT_CARD
    assert offer.candidates == [] and offer.suggested_account_id is None


def test_the_account_whose_number_the_file_prints_is_suggested(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """Two depots: the one whose number an earlier statement set is the suggestion and the only
    candidate; a depot with another number is never offered."""
    ws, _ = fresh_workspaces
    first, second = _depot(run, ws), _depot(run, ws, label="Megatrends")
    run(
        ws,
        import_statement,
        blob_store,
        second.id,
        _depot_bytes(),
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )
    later = _depot_bytes(rows=ROWS[4:7], total="-500,00", closing="-400,00")
    doc, _ = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, later)
    offer = _offer(read, blob_store, ws, doc.id)
    assert [c.account_id for c in offer.candidates] == [second.id]
    assert offer.suggested_account_id == second.id
    with pytest.raises(RuleError) as caught:
        _import_stored(run, blob_store, ws, doc.id, first.id)
    assert caught.value.code == rules.ACCOUNT_MISMATCH


def test_several_accounts_without_a_number_are_offered_none_suggested(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path
):
    ws, _ = fresh_workspaces
    doc, _ = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, _card_bytes())
    one, two = _card(run, ws), _card(run, ws, label="Zweitkarte")
    offer = _offer(read, blob_store, ws, doc.id)
    assert {c.account_id for c in offer.candidates} == {one.id, two.id}
    assert offer.suggested_account_id is None


def test_a_document_never_refused_has_no_offer(run, read, blob_store, fresh_workspaces):
    """Only a document whose read failed with proposal.bank_file_not_read is parsed for an
    offer; any other document has none."""
    ws, _ = fresh_workspaces
    _card(run, ws)
    doc = run(
        ws,
        documents.upload_document,
        blob_store,
        _card_bytes(),
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
    )
    assert _offer(read, blob_store, ws, doc.id) is None
    assert read(ws, document_statement_imports, doc.id) == []


def test_another_workspaces_document_is_not_found(
    run, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws_a, ws_b = fresh_workspaces
    doc, _ = _uploaded_and_refused(app_engine, blob_store, ws_a, tmp_path, _card_bytes())
    card_b = _card(run, ws_b)
    with pytest.raises(NotFoundError):
        _import_stored(run, blob_store, ws_b, doc.id, card_b.id)
    with pytest.raises(NotFoundError):  # nor an account of another workspace
        _import_stored(run, blob_store, ws_a, doc.id, card_b.id)


def test_a_hand_typed_statement_still_leaves_zu_pruefen_when_imported(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """The stale „couldn't read this“ card is superseded on import even when the person typed
    the document by hand meanwhile (the typing is left alone)."""
    ws, _ = fresh_workspaces
    doc, queued = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, _depot_bytes())
    run(ws, documents.type_document, doc.id, DocumentTyping(type=DocumentType.OTHER))
    account = _depot(run, ws)
    _import_stored(run, blob_store, ws, doc.id, account.id)
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED
    assert read(ws, documents.get_document, doc.id).typing.type is DocumentType.OTHER
