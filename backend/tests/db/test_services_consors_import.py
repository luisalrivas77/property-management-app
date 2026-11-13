"""A Consors Finanz card statement imported end to end, as hausbuch_app under RLS (phase 6
D176 (2)-(4) for a second issuer; fixture tests/fixtures/phase6/card_pdf_consors.yaml, its
`import` half).

Each case is imported onto a card account made from `bases.account` without a number (as the
real card account was created by hand), after the cases its `import.after` names. A reconciled
import's lines are then matched with the model off: the payment „EINGEGANGENE ZAHLUNG“ is
booked by code as internal_transfer_in, every other line waits. Also: the statement uploaded
through Dokumente never reaches the model and is offered for the card account without a
number (not for another issuer's card), „Konto anlegen und einlesen“ creates its account, and
the payment IBAN is confirmed from its original. Invented statements only.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import text
from test_card_pdf import BUILT as AVARDA_BUILT
from test_consors_pdf import BUILT, CONSORS, case_bytes
from test_jobs_document_read import _settings as _test_settings
from test_services_card_import import _card as _avarda_card
from test_services_card_import import _import as _avarda_import

from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.session import workspace_transaction
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.bank import (
    AccountCreate,
    ConfirmPaymentIbanBody,
    StatementAccountBody,
    TransactionAllocationStatus,
)
from hausbuch.schemas.enums import (
    AccountKind,
    ActorType,
    DocumentSource,
    ImportStatus,
    ProposalStatus,
)
from hausbuch.services import documents, proposals, statements
from hausbuch.services.accounts import (
    confirm_payment_iban,
    create_account,
    create_account_from_statement,
    get_account,
)
from hausbuch.services.context import Actor, new_command
from hausbuch.services.statements import (
    import_statement,
    import_stored_statement,
    list_imports,
    list_transactions,
    statement_offer,
)
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
STORED = [c for c in BUILT if c.import_.status is not None]
REFUSED = [c for c in BUILT if c.import_.error is not None]


@pytest.fixture
def hook_calls(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        statements, "_on_reconciled", lambda ctx, import_id: calls.append(import_id)
    )
    return calls


def _case(name):
    return next(c for c in CONSORS.cases if c.id == name)


def _deps(app_engine, tmp_path) -> JobDeps:
    settings = Settings(
        database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
        iban_hmac_key=TEST_IBAN_KEY,
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root=tmp_path / "blobs",
        ai_enabled=False,
    )
    return JobDeps(
        engine=app_engine,
        settings=settings,
        blob_store=None,  # type: ignore[arg-type]  # matching reads no original
        model_client=None,  # type: ignore[arg-type]  # the model is off
        clock=lambda: T0,
    )


def _card(run, ws, **overrides):
    """The card account as it was created by hand: kind credit_card, no number."""
    base = CONSORS.bases["account"]
    data = {
        "label": "Kreditkarte Consors",
        "bank": "Beispielbank",
        "kind": AccountKind(base["kind"]),
        "provider": base["provider"],
        "opening_balance": Decimal(base["opening_balance"]),
        "opening_balance_on": base["opening_balance_on"],
    } | overrides
    return run(ws, create_account, AccountCreate(**data), iban_key=TEST_IBAN_KEY, now=T0)


def _import(run, blob_store, ws, account_id, case, n=0):
    return run(
        ws,
        import_statement,
        blob_store,
        account_id,
        case_bytes(case),
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        original_filename="kontoauszug.pdf",
        now=T0 + timedelta(minutes=n),
    )


def _after(run, blob_store, ws, account_id, case):
    for n, name in enumerate(case.import_.after):
        _import(run, blob_store, ws, account_id, _case(name), n)
    return len(case.import_.after)


@pytest.mark.parametrize("case", STORED, ids=lambda c: c.id)
def test_a_consors_statement_imports_as_the_fixture_says(
    case, run, read, blob_store, fresh_workspaces, hook_calls, app_engine, tmp_path
):
    ws, _ = fresh_workspaces
    outcome = case.import_
    account = _card(run, ws)
    assert account.external_ref_masked is None  # created without a number
    n = _after(run, blob_store, ws, account.id, case)
    hook_calls.clear()
    imported = _import(run, blob_store, ws, account.id, case, n)

    assert imported.format.value == "card_pdf"
    assert imported.status is outcome.status
    assert imported.error_code == outcome.error_code
    assert (imported.lines_new, imported.lines_skipped) == (
        outcome.lines_new,
        outcome.lines_skipped,
    )
    after = read(ws, get_account, account.id)
    if outcome.account_ref_masked is not None:
        assert after.external_ref_masked == outcome.account_ref_masked
    if outcome.payment_iban_masked is not None:
        assert after.payment_iban_masked is None  # only proposed (principle 3)
        assert after.payment_iban_proposal.payment_iban_masked == outcome.payment_iban_masked
    assert after.card_last4 == case.expect.card_last4

    reconciled = imported.status is ImportStatus.RECONCILED
    if not reconciled:
        assert hook_calls == []  # D34: a broken file's lines are never matched
    elif imported.lines_new:
        assert hook_calls == [imported.id]
    mine = read(ws, list_transactions, account.id, import_id=imported.id)
    deps = _deps(app_engine, tmp_path)
    for line in mine:
        run_match_transaction(deps, workspace_id=ws, transaction_id=line.id, attempt=0)
    stored = {(t.booking_date, t.amount): t for t in read(ws, list_transactions, account.id)}
    booked = {b.index: b.category for b in outcome.booked}
    parsed = statements.parse_statement(case_bytes(case), "application/pdf").statement
    for index, line in enumerate(parsed.lines):
        found = stored[(line.booking_date, line.amount)]
        assert found.bank_reference is None  # D22's position path
        if outcome.none_matched:
            assert found.allocation_status is TransactionAllocationStatus.NOT_RECONCILED
        elif index in booked:
            assert found.allocation_status is TransactionAllocationStatus.CONFIRMED
            assert found.allocation_categories == [AllocationCategory(booked[index])]
        elif imported.lines_new:
            assert found.allocation_status is TransactionAllocationStatus.OPEN


@pytest.mark.parametrize("case", REFUSED, ids=lambda c: c.id)
def test_a_statement_of_another_card_account_is_refused(
    case, run, read, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    account = _card(run, ws)
    n = _after(run, blob_store, ws, account.id, case)
    with pytest.raises(RuleError) as caught:
        _import(run, blob_store, ws, account.id, case, n)
    assert caught.value.code == case.import_.error
    assert len(read(ws, list_imports, account.id)) == n


def test_every_case_is_covered():
    assert {c.id for c in STORED} | {c.id for c in REFUSED} == {c.id for c in BUILT}


def test_the_other_issuers_card_never_takes_it(run, blob_store, fresh_workspaces, hook_calls):
    """Two cards in one household: the first issuer's card, numbered by its own statement,
    refuses a Consors statement (another number)."""
    ws, _ = fresh_workspaces
    first = _avarda_card(run, ws)
    _avarda_import(run, blob_store, ws, first.id, AVARDA_BUILT[0])
    with pytest.raises(RuleError) as caught:
        _import(run, blob_store, ws, first.id, _case("plain_month"))
    assert caught.value.code == "statement.account_mismatch"


def _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, data: bytes):
    """Uploaded through Dokumente with ai on and read: refused before any model call."""
    actor = Actor(ActorType.USER, "user:test")
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, actor, now=T0)
        doc = documents.upload_document(
            ctx,
            blob_store,
            data,
            media_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename="kontoauszug.pdf",
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


def test_uploaded_under_dokumente_it_is_offered_for_the_unnumbered_card(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """The household's situation: the first issuer's card carries its number, the Consors
    card was created by hand without one. The statement is offered for the Consors card only,
    pre-selected, and „Auf Konto importieren“ sets its number."""
    ws, _ = fresh_workspaces
    first = _avarda_card(run, ws)
    _avarda_import(run, blob_store, ws, first.id, AVARDA_BUILT[0])
    consors = _card(run, ws)
    case = _case("plain_month")
    doc, queued = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, case_bytes(case))

    offer = read(ws, statement_offer, blob_store, doc.id, iban_key=TEST_IBAN_KEY)
    assert offer.account_kind is AccountKind.CREDIT_CARD
    assert [c.account_id for c in offer.candidates] == [consors.id]
    assert offer.suggested_account_id == consors.id
    assert offer.new_account is not None  # no account holds the number yet
    assert offer.new_account.external_ref_masked == case.import_.account_ref_masked

    imported = run(
        ws,
        import_stored_statement,
        blob_store,
        doc.id,
        consors.id,
        iban_key=TEST_IBAN_KEY,
        now=T0 + timedelta(minutes=1),
    )
    assert imported.status is ImportStatus.RECONCILED
    assert read(ws, get_account, consors.id).external_ref_masked == case.import_.account_ref_masked
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED


def test_konto_anlegen_und_einlesen_creates_the_card(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    case = _case("plain_month")
    doc, _ = _uploaded_and_refused(app_engine, blob_store, ws, tmp_path, case_bytes(case))
    new = read(ws, statement_offer, blob_store, doc.id, iban_key=TEST_IBAN_KEY).new_account
    assert new.kind is AccountKind.CREDIT_CARD
    assert (new.opening_balance, new.opening_from_file) == (case.expect.opening_balance, True)
    created = run(
        ws,
        create_account_from_statement,
        blob_store,
        doc.id,
        StatementAccountBody(label=new.label, bank="Beispielbank"),
        iban_key=TEST_IBAN_KEY,
        now=T0 + timedelta(minutes=1),
    )
    assert case.expect.account_ref not in created.model_dump_json()
    assert created.account.external_ref_masked == case.import_.account_ref_masked
    assert created.statement_import.status is ImportStatus.RECONCILED


def test_the_payment_iban_is_confirmed_from_the_statement(
    run, read, blob_store, fresh_workspaces, hook_calls, app_engine
):
    ws, _ = fresh_workspaces
    case = _case("plain_month")
    card = _card(run, ws)
    imported = _import(run, blob_store, ws, card.id, case)
    body = ConfirmPaymentIbanBody(import_id=imported.id)
    result = run(ws, confirm_payment_iban, blob_store, card.id, body, iban_key=TEST_IBAN_KEY)
    assert result.account.payment_iban_masked == case.import_.payment_iban_masked
    with app_engine.begin() as conn:  # the re-check of waiting debits it queues
        conn.execute(text("DELETE FROM procrastinate_jobs"))
