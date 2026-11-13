"""A statement file uploaded through Dokumente with no account for it (phase 6, 13 Nov 2026,
amends D176 (3) and D165 (b); Luis: "If I upload a statement and there's no account for it
could the app suggest to create an account?"), as hausbuch_app under RLS.

- A bank's own export (a Sparkasse camt ZIP, a DKB CSV) is stopped by the document job before
  the model, like a card or depot statement (proposal.bank_file_not_read); its offer names the
  bank account whose IBAN is the file's own, and „Auf Konto importieren“ imports it there.
- „Konto anlegen und einlesen“ (create_account_from_statement) creates the account the file
  implies, read from the stored original (kind, IBAN or number, opening balance), and imports
  the file onto it, in one transaction.

Invented files only: the anonymised Sparkasse samples (tests/test_bank_parsers.py), the DKB
fixture (tests/fixtures/phase6/dkb_csv.yaml) and the invented card and depot statements
(tests/test_card_pdf.py, tests/test_depot_pdf.py).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import text
from test_bank_parsers import CAMT, DKB, build
from test_card_pdf import case_bytes, draw
from test_depot_pdf import KONTO, ROWS, page
from test_jobs_document_read import _settings as _test_settings
from test_services_card_import import _card, _first

from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.session import workspace_transaction
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import mask_iban, normalize_iban
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate, StatementAccountBody, StatementFormat
from hausbuch.schemas.enums import (
    AccountKind,
    AccountPurpose,
    ActorType,
    DocumentSource,
    ImportStatus,
    ProposalStatus,
)
from hausbuch.schemas.household import OwnershipShare
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services import documents, proposals, review, statements
from hausbuch.services.accounts import (
    create_account,
    create_account_from_statement,
    get_account,
    list_accounts,
)
from hausbuch.services.bank import parse_statement
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import ConflictError, NotFoundError, RuleViolationError
from hausbuch.services.owners import create_owner
from hausbuch.services.statements import (
    document_statement_imports,
    import_stored_statement,
    list_imports,
    statement_offer,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def _case(fixture, case_id):
    return next(c for c in fixture.cases if c.id == case_id)


CAMT_ZIP = build(_case(CAMT, "sparkasse_zip_of_daily_reports").input)
CAMT_IBAN = "DE50100000000000000001"
DKB_CASE = _case(DKB, "export_2025_layout")
DKB_BYTES = build(DKB_CASE.input)
DKB_IBAN = DKB_CASE.import_.account_iban
OTHER_IBAN = "DE89370400440532013000"

BANK_FILES = {
    "sparkasse_camt_zip": (CAMT_ZIP, "application/zip", CAMT_IBAN),
    "dkb_csv": (DKB_BYTES, "text/csv", DKB_IBAN),
}


@pytest.fixture
def hook_calls(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        statements, "_on_reconciled", lambda ctx, import_id: calls.append(import_id)
    )
    return calls


def _card_bytes() -> bytes:
    return case_bytes(_first())


def _depot_bytes(konto: str = KONTO) -> bytes:
    return draw([page(rows=ROWS).replace(KONTO, konto)])


def _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, data: bytes, media_type: str):
    """The file uploaded through Dokumente with ai on and its read run: the job refuses it
    with proposal.bank_file_not_read before any model call. The document and its proposal."""
    actor = Actor(ActorType.USER, "user:test")
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, actor, now=T0)
        doc = documents.upload_document(
            ctx,
            blob_store,
            data,
            media_type=media_type,
            source=DocumentSource.UPLOAD,
            original_filename="export",
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
    assert client.requests == []  # neither the classifier nor an extraction
    with workspace_transaction(app_engine, ws) as conn:
        failed = proposals.get_proposal(conn, queued.id)
    assert failed.status is ProposalStatus.FAILED
    assert failed.error_code == proposals.PROPOSAL_BANK_FILE_NOT_READ
    return doc, queued


def _offer(read, blob_store, ws, document_id):
    return read(ws, statement_offer, blob_store, document_id, iban_key=TEST_IBAN_KEY)


def _read_failed(read, ws):
    return [i for i in read(ws, review.inbox) if i.kind is InboxItemKind.READ_FAILED]


def _bank_account(run, ws, iban, label="Girokonto"):
    return run(
        ws,
        create_account,
        AccountCreate(
            label=label,
            bank="Beispielbank",
            iban=iban,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 3, 31),
            purpose=AccountPurpose.PRIVATE,
            is_owner_account=True,
        ),
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )


def _create(run, blob_store, ws, document_id, n=0, **body):
    data = {"label": "Neues Konto", "bank": "Beispielbank"} | body
    return run(
        ws,
        create_account_from_statement,
        blob_store,
        document_id,
        StatementAccountBody(**data),
        iban_key=TEST_IBAN_KEY,
        now=T0 + timedelta(minutes=n),
    )


# --- A bank's export under Dokumente ----------------------------------------------------


@pytest.mark.parametrize("name", list(BANK_FILES))
def test_a_bank_export_never_reaches_the_model_and_goes_onto_its_account(
    name, run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    data, media_type, iban = BANK_FILES[name]
    doc, queued = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, data, media_type)
    (card,) = _read_failed(read, ws)
    assert card.error_code == proposals.PROPOSAL_BANK_FILE_NOT_READ
    assert card.question == "Auf welches Konto soll sie importiert werden?"

    other = _bank_account(run, ws, OTHER_IBAN, label="Anderes Konto")
    own = _bank_account(run, ws, iban)
    offer = _offer(read, blob_store, ws, doc.id)
    assert offer.account_kind is AccountKind.BANK_CURRENT
    assert [c.account_id for c in offer.candidates] == [own.id]  # never the other IBAN's
    assert offer.suggested_account_id == own.id
    assert offer.new_account is None  # an account holds the file's IBAN

    with pytest.raises(RuleError) as caught:
        run(ws, import_stored_statement, blob_store, doc.id, other.id, iban_key=TEST_IBAN_KEY)
    assert caught.value.code == rules.ACCOUNT_MISMATCH

    imported = run(
        ws, import_stored_statement, blob_store, doc.id, own.id, iban_key=TEST_IBAN_KEY, now=T0
    )
    assert imported.document_id == doc.id and imported.account_id == own.id
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED
    assert _read_failed(read, ws) == []
    assert _offer(read, blob_store, ws, doc.id) is None

    # „Neu auslesen“ on the imported original: refused before anything is queued.
    with pytest.raises(RuleViolationError) as refused:
        run(ws, proposals.request_read, doc.id, now=T0 + timedelta(minutes=5))
    assert refused.value.codes == (proposals.PROPOSAL_BANK_FILE_NOT_READ,)


def test_a_bank_export_imported_on_the_account_page_is_refused_for_a_new_read(
    run, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account = _bank_account(run, ws, CAMT_IBAN)
    imported = run(
        ws,
        statements.import_statement,
        blob_store,
        account.id,
        CAMT_ZIP,
        media_type="application/zip",
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )
    with pytest.raises(RuleViolationError) as refused:
        run(ws, proposals.request_read, imported.document_id)
    assert refused.value.codes == (proposals.PROPOSAL_BANK_FILE_NOT_READ,)


# --- „Konto anlegen und einlesen“ -------------------------------------------------------


def test_a_camt_export_creates_its_account_and_imports_in_one_step(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    doc, queued = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, CAMT_ZIP, "application/zip"
    )
    owner_a = run(ws, create_owner, OwnerCreate(full_name="Owner A"))
    owner_b = run(ws, create_owner, OwnerCreate(full_name="Owner B"))
    offer = _offer(read, blob_store, ws, doc.id)
    assert offer.format is StatementFormat.CAMT
    assert offer.candidates == [] and offer.suggested_account_id is None
    new = offer.new_account
    assert new.kind is AccountKind.BANK_CURRENT
    assert new.iban_masked == mask_iban(normalize_iban(CAMT_IBAN))
    assert new.external_ref_masked is None
    assert new.bank is None  # a camt file names no bank by its format
    assert CAMT_IBAN not in offer.model_dump_json()

    half = [
        OwnershipShare(owner_id=o.id, share_numerator=1, share_denominator=2, reason="gemeinsam")
        for o in (owner_a, owner_b)
    ]
    created = _create(run, blob_store, ws, doc.id, label="Girokonto", shares=half)
    account, imported = created.account, created.statement_import
    assert CAMT_IBAN not in created.model_dump_json()
    assert account.kind is AccountKind.BANK_CURRENT
    assert account.purpose is AccountPurpose.PRIVATE
    assert account.iban_masked == new.iban_masked
    # The anchor from the file: its opening, the day before its first booking.
    assert (account.opening_balance, account.opening_balance_on) == (
        Decimal("0.00"),
        date(2026, 3, 31),
    )
    assert {(s.owner_id, s.share) for s in account.ownership} == {
        (owner_a.id, Fraction(1, 2)),
        (owner_b.id, Fraction(1, 2)),
    }
    assert account.ownership_missing is False
    assert imported.document_id == doc.id and imported.account_id == account.id
    assert imported.status is ImportStatus.RECONCILED
    assert hook_calls == [imported.id]
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED
    assert _read_failed(read, ws) == []
    assert _offer(read, blob_store, ws, doc.id) is None
    (on,) = read(ws, document_statement_imports, doc.id)
    assert on.account_id == account.id

    # Done once: a second call has no offer to act on.
    with pytest.raises(RuleError) as caught:
        _create(run, blob_store, ws, doc.id, n=1)
    assert caught.value.code == rules.NO_ACCOUNT_OFFER


def test_a_dkb_csv_creates_its_account_and_waits_for_its_closing(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """A CSV states no opening: the account starts at 0,00 the day before the first booking,
    and the import waits for its closing balance as an upload on the account page does."""
    ws, _ = fresh_workspaces
    doc, _ = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, DKB_BYTES, "text/csv")
    new = _offer(read, blob_store, ws, doc.id).new_account
    assert (new.kind, new.bank, new.provider) == (AccountKind.BANK_CURRENT, "DKB", "DKB")
    created = _create(run, blob_store, ws, doc.id, label=new.label, bank=new.bank)
    account = created.account
    assert account.opening_balance == Decimal("0.00")
    assert account.opening_balance_on == DKB_CASE.expect.first_booking_on - timedelta(days=1)
    assert account.ownership_missing is True  # no shares: the to-do stays
    assert created.statement_import.status is ImportStatus.IMPORTED  # pending_balance
    assert created.statement_import.stated_closing == DKB_CASE.import_.prefilled_closing
    assert _read_failed(read, ws) == []


@pytest.mark.parametrize("kind", ["card", "depot"])
def test_a_card_or_depot_statement_creates_its_account_and_imports(
    kind, run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    data = _card_bytes() if kind == "card" else _depot_bytes()
    doc, queued = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, data, "application/pdf"
    )
    statement = parse_statement(data, "application/pdf").statement
    new = _offer(read, blob_store, ws, doc.id).new_account
    expected = AccountKind.CREDIT_CARD if kind == "card" else AccountKind.DEPOT
    assert new.kind is expected
    assert new.iban_masked is None
    assert new.external_ref_masked == f"**** {statement.account_ref[-4:]}"
    if kind == "card":
        # The card statement names no issuer the reader keeps: no institution is guessed, the
        # person types the bank (13 Nov 2026).
        assert (new.label, new.bank, new.provider) == ("Kreditkarte", None, None)
    else:
        assert new.bank == "quirion"

    created = _create(run, blob_store, ws, doc.id, label=new.label, bank=new.bank or "Beispielbank")
    account = created.account
    assert statement.account_ref not in created.model_dump_json()
    assert account.kind is expected
    assert account.purpose is AccountPurpose.PRIVATE
    assert account.external_ref_masked == new.external_ref_masked
    assert account.opening_balance == statement.opening_balance
    assert account.iban_masked is None  # a depot's IBAN is confirmed on its page (D156)
    assert created.statement_import.status is ImportStatus.RECONCILED
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED


def test_a_failing_import_leaves_no_account(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, monkeypatch
):
    ws, _ = fresh_workspaces
    doc, queued = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, _card_bytes(), "application/pdf"
    )

    def refuse(*args, **kwargs):
        raise RuleError(rules.UNPARSABLE, "refused for the test")

    monkeypatch.setattr(statements, "import_statement", refuse)
    with pytest.raises(RuleError):
        _create(run, blob_store, ws, doc.id)
    assert read(ws, list_accounts) == []
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.FAILED
    monkeypatch.undo()
    assert _offer(read, blob_store, ws, doc.id).new_account is not None


def test_a_second_depot_with_another_number_is_offered_to_be_created(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """A depot whose number an earlier statement set: a statement of another quirion account
    (a second strategy) is not offered for it, but for a new depot."""
    ws, _ = fresh_workspaces
    first = run(
        ws,
        create_account,
        AccountCreate(
            label="Globales Portfolio",
            bank="Beispiel-Vermögensverwaltung",
            kind=AccountKind.DEPOT,
            provider="quirion",
            external_ref=KONTO,
            opening_balance=Decimal("100.00"),
            opening_balance_on=date(2026, 3, 1),
        ),
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )
    data = _depot_bytes(konto="9900000456")
    doc, _ = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, data, "application/pdf")
    offer = _offer(read, blob_store, ws, doc.id)
    assert offer.candidates == []
    assert offer.new_account.external_ref_masked == "**** 0456"
    created = _create(run, blob_store, ws, doc.id, label="Megatrends", bank="quirion")
    assert created.account.id != first.id
    assert created.account.external_ref_masked == "**** 0456"
    assert [i.id for i in read(ws, list_imports, first.id)] == []


def test_an_unnumbered_depot_is_a_candidate_beside_the_create_offer(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path
):
    ws, _ = fresh_workspaces
    unnumbered = run(
        ws,
        create_account,
        AccountCreate(
            label="Depot",
            bank="quirion",
            kind=AccountKind.DEPOT,
            opening_balance=Decimal("100.00"),
            opening_balance_on=date(2026, 3, 1),
        ),
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )
    doc, _ = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, _depot_bytes(), "application/pdf"
    )
    offer = _offer(read, blob_store, ws, doc.id)
    assert [c.account_id for c in offer.candidates] == [unnumbered.id]
    assert offer.new_account is not None


def test_an_account_holding_the_files_iban_or_number_refuses_a_create(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    bank_doc, _ = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, CAMT_ZIP, "application/zip"
    )
    card_data = _card_bytes()
    card_doc, _ = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, card_data, "application/pdf"
    )
    _bank_account(run, ws, CAMT_IBAN)
    customer = parse_statement(card_data, "application/pdf").statement.account_ref
    card = _card(run, ws, external_ref=customer)
    for doc in (bank_doc, card_doc):
        assert _offer(read, blob_store, ws, doc.id).new_account is None
        with pytest.raises(ConflictError):
            _create(run, blob_store, ws, doc.id)
    assert len(read(ws, list_accounts)) == 2
    assert read(ws, get_account, card.id).external_ref_masked is not None


def test_a_document_without_an_offer_is_refused(run, blob_store, fresh_workspaces):
    """A document whose read was never refused (here: never read) has no offer."""
    ws, _ = fresh_workspaces
    doc = run(
        ws,
        documents.upload_document,
        blob_store,
        _card_bytes(),
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
    )
    with pytest.raises(RuleError) as caught:
        _create(run, blob_store, ws, doc.id)
    assert caught.value.code == rules.NO_ACCOUNT_OFFER


def test_a_rental_card_is_refused(run, read, app_engine, blob_store, fresh_workspaces, tmp_path):
    """The purpose rule of create_account holds: a card is always private."""
    ws, _ = fresh_workspaces
    doc, _ = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, _card_bytes(), "application/pdf"
    )
    with pytest.raises(RuleError):
        _create(run, blob_store, ws, doc.id, purpose=AccountPurpose.RENTAL)
    assert read(ws, list_accounts) == []


def test_another_workspaces_document_is_not_found(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path
):
    ws_a, ws_b = fresh_workspaces
    doc, _ = _uploaded_and_refused(
        app_engine, blob_store, ws_a, tmp_path, CAMT_ZIP, "application/zip"
    )
    with pytest.raises(NotFoundError):
        _create(run, blob_store, ws_b, doc.id)
    assert read(ws_b, list_accounts) == []
    assert read(ws_a, list_accounts) == []
