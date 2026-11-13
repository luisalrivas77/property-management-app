"""An exchange export uploaded through Dokumente never reaches the model (phase 6 D166: "never
classified or sent to the model"; a Binance file holds the account's User ID and the
withdrawal addresses). The document job recognises BISON's export and each of Binance's three
files by the header line before anything else and fails the read with
proposal.exchange_export_not_read, whose Zu prüfen card names the exchange account's page;
importing the same file there supersedes that card, and the imported original is refused by
request_read. Invented files only (tests/fixtures/phase6/)."""

from datetime import UTC, datetime, timedelta

import fixture_models_phase6 as fixtures
import pytest
from db.test_jobs_document_read import _settings as _test_settings
from db.test_services_exchange_import import _exchange, _upload, _zip
from sqlalchemy import text

from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.session import workspace_transaction
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.enums import ActorType, DocumentSource, ProposalStatus
from hausbuch.services import documents, proposals, review
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import RuleViolationError

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
BISON = fixtures.load("bison_csv")
BINANCE = fixtures.load("binance_csv")


def _case(cases, case_id):
    return next(c for c in cases if c.id == case_id)


BISON_TEXT = _case(BISON.parse_cases, "real_layout").text
TRANSACTIONS = _case(BINANCE.cases, "typed_balance_matches").transactions
WITHDRAWALS = next(c.withdrawals for c in BINANCE.cases if c.withdrawals)
SPOT = _case(BINANCE.cases, "spot_trade_history_refused").transactions

FILES = {
    "bison_csv": (BISON_TEXT.encode(), "text/csv"),
    "binance_transactions_zip": (_zip(TRANSACTIONS), "application/zip"),
    "binance_withdrawals_csv": (WITHDRAWALS.encode(), "text/csv"),
    "binance_spot_history_zip": (_zip(SPOT), "application/zip"),
    # The declared type is the browser's; a CSV sent as text/plain is stopped the same way.
    "binance_transactions_plain": (TRANSACTIONS.encode(), "text/plain"),
}


def _upload_through_dokumente(app_engine, blob_store, ws, data, media_type):
    actor = Actor(ActorType.USER, "user:test")
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, actor, now=T0)
        doc = documents.upload_document(
            ctx,
            blob_store,
            data,
            media_type=media_type,
            source=DocumentSource.UPLOAD,
            original_filename="export.csv",
        )
        queued = proposals.request_read(ctx, doc.id)
    return doc, queued


def _run_read(app_engine, blob_store, ws, tmp_path, doc, queued) -> FakeModelClient:
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
    return client


def _read_failed(read, ws):
    return [i for i in read(ws, review.inbox) if i.kind is InboxItemKind.READ_FAILED]


@pytest.mark.parametrize("name", list(FILES))
def test_an_exchange_export_never_reaches_the_model(
    name, read, app_engine, blob_store, fresh_workspaces, tmp_path
):
    ws, _ = fresh_workspaces
    data, media_type = FILES[name]
    doc, queued = _upload_through_dokumente(app_engine, blob_store, ws, data, media_type)
    client = _run_read(app_engine, blob_store, ws, tmp_path, doc, queued)
    assert client.requests == []  # neither the classifier nor an extraction
    with workspace_transaction(app_engine, ws) as conn:
        failed = proposals.get_proposal(conn, queued.id)
    assert failed.status is ProposalStatus.FAILED
    assert failed.error_code == proposals.PROPOSAL_EXCHANGE_EXPORT_NOT_READ
    (card,) = _read_failed(read, ws)
    assert card.error_code == proposals.PROPOSAL_EXCHANGE_EXPORT_NOT_READ
    assert "Konto der Börse" in card.reasoning
    assert "„Export der Börse hochladen“" in card.question
    if name.startswith("binance"):
        user_id = BINANCE.never_stored.user_id
        assert user_id not in card.reasoning + card.question


def test_another_csv_is_not_taken_for_an_exchange_export(
    app_engine, blob_store, fresh_workspaces, tmp_path
):
    """Only the exchanges' headers stop the read here; any other CSV fails as before
    (its media type, never a model call)."""
    ws, _ = fresh_workspaces
    data = b"Datum;Betrag;Verwendungszweck\n01.03.2026;12,00;Beispiel\n"
    doc, queued = _upload_through_dokumente(app_engine, blob_store, ws, data, "text/csv")
    client = _run_read(app_engine, blob_store, ws, tmp_path, doc, queued)
    assert client.requests == []
    with workspace_transaction(app_engine, ws) as conn:
        failed = proposals.get_proposal(conn, queued.id)
    assert failed.status is ProposalStatus.FAILED
    assert failed.error_code != proposals.PROPOSAL_EXCHANGE_EXPORT_NOT_READ


def test_importing_it_on_the_exchange_account_ends_the_card_and_refuses_a_new_read(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path
):
    ws, _ = fresh_workspaces
    data = _zip(TRANSACTIONS)
    doc, queued = _upload_through_dokumente(app_engine, blob_store, ws, data, "application/zip")
    _run_read(app_engine, blob_store, ws, tmp_path, doc, queued)
    assert len(_read_failed(read, ws)) == 1

    account = _exchange(run, ws, provider="Binance")
    imported = _upload(run, blob_store, ws, account.id, data, media_type="application/zip")
    assert imported.movement_import.document_id == doc.id  # the same stored original
    assert _read_failed(read, ws) == []
    with workspace_transaction(app_engine, ws) as conn:
        assert proposals.get_proposal(conn, queued.id).status is ProposalStatus.SUPERSEDED

    # „Neu auslesen“ on the imported original: refused before anything is queued.
    with pytest.raises(RuleViolationError) as refused:
        run(ws, proposals.request_read, doc.id, now=T0 + timedelta(minutes=5))
    assert refused.value.codes == (proposals.PROPOSAL_EXCHANGE_EXPORT_NOT_READ,)
