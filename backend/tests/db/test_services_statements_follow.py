"""Statements follow their account, and a depot document finds the depot it prints (phase 6,
14 Nov 2026, D165 (a) and D176 (3) amended; Luis: "when importing all the documents for a
depot (or another account in general) the app should recognize that they're all for the same
account and when you create the account they are all assigned directly to it"), as
hausbuch_app under RLS.

- Every statement file waiting under Dokumente (its read refused as a statement file) that
  prints an account's IBAN or number goes onto that account when the account is created from
  one of them, created with the number, or gets it; in statement order, so the chain
  reconciles. A file uploaded once its account exists goes onto it at once.
- A depot document's reading pre-selects the depot whose number its text layer prints, and a
  waiting reading is rebuilt when a depot is created.

Invented numbers and files only (tests/test_depot_pdf.py's layout).
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_cli_evals_report import _owner_url
from db.test_services_proposals import _accept_all, _ai_call, _v
from sqlalchemy import select, text, update
from test_bank_parsers import CAMT, build
from test_card_pdf import draw
from test_depot_pdf import KONTO, page
from test_jobs_document_read import _settings as _test_settings
from test_services_statement_account import _create, _offer, _uploaded_and_refused
from test_services_statement_account import hook_calls as hook_calls  # noqa: F401 (a fixture)

from hausbuch import cli
from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import event as event_table
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.jobs.enqueue import FOLLOW_ACCOUNTS_TASK
from hausbuch.jobs.follow_accounts import run_follow_accounts
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import (
    AccountKind,
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    ImportStatus,
    ProposalStatus,
)
from hausbuch.schemas.events import EventType
from hausbuch.services import documents, proposals, statements
from hausbuch.services.accounts import create_account, get_account
from hausbuch.services.context import Actor, new_command
from hausbuch.services.iban import protect_iban
from hausbuch.services.statements import document_statement_imports, list_imports

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
OTHER_KONTO = "9900000456"
DEPOT_A_NUMBER = "9900000111"
DEPOT_B_NUMBER = "9900000222"
JOB = Actor(ActorType.AI, "claude-test-model")
# The anonymised Sparkasse camt ZIP (tests/test_bank_parsers.py) and the IBAN it prints.
CAMT_ZIP = build(next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports").input)
CAMT_IBAN = "DE50100000000000000001"


def _statement(n: int, konto: str = KONTO) -> bytes:
    """The n-th (1-based) of a depot's monthly cash statements: one deposit of 100,00 on the
    15th of month 2 + n, its opening the previous one's closing (100,00 before the first)."""
    on = date(2026, 2 + n, 15).strftime("%d.%m.%Y")
    opening, closing = 100 * n, 100 * (n + 1)
    row = f"100,00 EUR{on}{on}Einlage, Ref.: 55000010{n:02d}"
    return draw(
        [
            page(
                rows=[row], opening=f"{opening},00", total="100,00", closing=f"{closing},00"
            ).replace(KONTO, konto)
        ]
    )


def _upload_and_read(
    app_engine,
    blob_store,
    ws,
    tmp_path,
    data: bytes,
    media_type: str = "application/pdf",
    filename: str = "kontoauszug.pdf",
):
    """Uploaded through Dokumente and its read run (no model call): the document and the
    proposal the read wrote, whatever its status."""
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.USER, "user:test"), now=T0)
        doc = documents.upload_document(
            ctx,
            blob_store,
            data,
            media_type=media_type,
            source=DocumentSource.UPLOAD,
            original_filename=filename,
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
    return doc, queued


def _depot(run, ws, blob_store=None, *, label="Globales Portfolio", number=None, n=0):
    data = AccountCreate(
        label=label,
        bank="Beispiel-Vermögensverwaltung",
        kind=AccountKind.DEPOT,
        provider="quirion",
        external_ref=number,
        opening_balance=Decimal("100.00"),
        opening_balance_on=date(2026, 3, 14),
    )
    return run(
        ws,
        create_account,
        data,
        iban_key=TEST_IBAN_KEY,
        store=blob_store,
        now=T0 + timedelta(minutes=n),
    )


def _imported_order(read, ws) -> list[uuid.UUID]:
    """The import ids in the order their statement.imported events were written."""

    def query(conn):
        return list(
            conn.execute(
                select(event_table.c.entity_id)
                .where(event_table.c.type == EventType.STATEMENT_IMPORTED.value)
                .order_by(event_table.c.seq)
            ).scalars()
        )

    return read(ws, query)


# --- Statements follow their account ----------------------------------------------------


def test_creating_the_account_from_statement_3_of_5_imports_the_other_four_in_order(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    docs = {}
    for n in (3, 1, 5, 2, 4):  # uploaded out of order
        docs[n], _ = _uploaded_and_refused(
            app_engine, blob_store, ws, tmp_path, _statement(n), "application/pdf"
        )
    offer = _offer(read, blob_store, ws, docs[3].id)
    assert offer.new_account.other_statements == 4
    # The anchor is the earliest file's opening, so the whole chain reconciles from it.
    assert offer.new_account.opening_balance == Decimal("100.00")
    assert offer.new_account.opening_balance_on == date(2026, 3, 14)

    created = _create(run, blob_store, ws, docs[3].id, label="Globales Portfolio")
    assert created.other_imported == 4 and created.other_refused == []
    assert created.statement_import.document_id == docs[3].id
    account = created.account
    assert (account.opening_balance, account.opening_balance_on) == (
        Decimal("100.00"),
        date(2026, 3, 14),
    )
    imports = read(ws, list_imports, account.id)
    assert len(imports) == 5
    assert {i.status for i in imports} == {ImportStatus.RECONCILED}
    by_id = {i.id: i for i in imports}
    order = [by_id[i].document_id for i in _imported_order(read, ws)]
    assert order == [docs[n].id for n in (1, 2, 3, 4, 5)]  # statement order, not upload order
    for doc in docs.values():
        assert _offer(read, blob_store, ws, doc.id) is None
    assert read(ws, get_account, account.id).external_ref_masked == f"**** {KONTO[-4:]}"


def test_a_file_of_another_account_stays_where_it_is(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    own, _ = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, _statement(1), "application/pdf"
    )
    other, other_read = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, _statement(2, OTHER_KONTO), "application/pdf"
    )
    assert _offer(read, blob_store, ws, own.id).new_account.other_statements == 0
    created = _create(run, blob_store, ws, own.id, label="Globales Portfolio")
    assert created.other_imported == 0
    assert read(ws, document_statement_imports, other.id) == []
    assert read(ws, proposals.get_proposal, other_read.id).status is ProposalStatus.FAILED
    offer = _offer(read, blob_store, ws, other.id)
    assert offer.new_account is not None  # still its own account to create
    assert offer.suggested_account_id is None


def test_a_later_upload_goes_straight_onto_its_account(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    first, _ = _uploaded_and_refused(
        app_engine, blob_store, ws, tmp_path, _statement(1), "application/pdf"
    )
    account = _create(run, blob_store, ws, first.id, label="Globales Portfolio").account
    later, queued = _upload_and_read(app_engine, blob_store, ws, tmp_path, _statement(2))
    (imported,) = read(ws, document_statement_imports, later.id)
    assert imported.account_id == account.id
    assert imported.status is ImportStatus.RECONCILED
    # The refusal the read recorded is superseded by the import, as „Auf Konto importieren“.
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED
    assert _offer(read, blob_store, ws, later.id) is None


def test_a_later_bank_export_goes_straight_onto_the_account_with_its_iban(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """The same for a bank's export (a camt ZIP), matched by the IBAN it prints."""
    ws, _ = fresh_workspaces
    account = run(
        ws,
        create_account,
        AccountCreate(
            label="Girokonto",
            bank="Beispielbank",
            iban=CAMT_IBAN,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 3, 31),
            is_owner_account=True,
            purpose="private",
        ),
        iban_key=TEST_IBAN_KEY,
        store=blob_store,
        now=T0,
    )
    later, queued = _upload_and_read(
        app_engine, blob_store, ws, tmp_path, CAMT_ZIP, "application/zip", "camt.zip"
    )
    (imported,) = read(ws, document_statement_imports, later.id)
    assert imported.account_id == account.id
    assert imported.status is ImportStatus.RECONCILED
    assert read(ws, proposals.get_proposal, queued.id).status is ProposalStatus.SUPERSEDED


def test_an_account_created_with_the_number_takes_its_waiting_files(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    ws, _ = fresh_workspaces
    docs = [
        _uploaded_and_refused(
            app_engine, blob_store, ws, tmp_path, _statement(n), "application/pdf"
        )[0]
        for n in (2, 1)
    ]
    account = _depot(run, ws, blob_store, number=KONTO)
    imports = read(ws, list_imports, account.id)
    assert {i.document_id for i in imports} == {d.id for d in docs}
    assert {i.status for i in imports} == {ImportStatus.RECONCILED}


def test_a_refused_file_keeps_its_offer_and_blocks_no_other(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls, monkeypatch
):
    ws, _ = fresh_workspaces
    docs = {
        n: _uploaded_and_refused(
            app_engine, blob_store, ws, tmp_path, _statement(n), "application/pdf"
        )
        for n in (1, 2, 3)
    }
    refused_doc, refused_read = docs[2]
    real = statements.import_stored_statement

    def refusing(ctx, store, document_id, account_id, **kwargs):
        if document_id == refused_doc.id:
            raise RuleError("statement.unparsable", "refused for the test")
        return real(ctx, store, document_id, account_id, **kwargs)

    monkeypatch.setattr(statements, "import_stored_statement", refusing)
    created = _create(run, blob_store, ws, docs[1][0].id, label="Globales Portfolio")
    assert created.other_imported == 1
    (refusal,) = created.other_refused
    assert (refusal.document_id, refusal.code) == (refused_doc.id, "statement.unparsable")
    assert read(ws, document_statement_imports, refused_doc.id) == []
    assert read(ws, proposals.get_proposal, refused_read.id).status is ProposalStatus.FAILED
    monkeypatch.setattr(statements, "import_stored_statement", real)
    offer = _offer(read, blob_store, ws, refused_doc.id)
    assert offer.suggested_account_id == created.account.id
    # Statement 3 was imported after the refusal; the gap names the missing one, never bridged.
    (third,) = read(ws, document_statement_imports, docs[3][0].id)
    assert third.status is not ImportStatus.RECONCILED


# --- A depot document finds the depot it prints ------------------------------------------


REPORT = ClassifyOutput(
    type=DocumentType.LETTER,
    subtype=DocumentSubtype.DEPOT_REPORT,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)


def _report_values(positions: list[str]) -> dict:
    return {
        "title": _v("Quartalsbericht"),
        "document_date": _v("2026-07-05"),
        "report_date": _v("2026-06-30"),
        "positions": _v(positions),
        "trades": _v([]),
    }


TRANSFER = ClassifyOutput(
    type=DocumentType.LETTER,
    subtype=DocumentSubtype.SECURITIES_TRANSFER,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)


def _report(
    run,
    job,
    ws,
    blob_store,
    number: str,
    positions=None,
    *,
    text: str | None = None,
    classification: ClassifyOutput = REPORT,
    values: dict | None = None,
):
    """A depot report printing „Depot-Nr. <number>“ (or another depot document's `text`),
    uploaded and read (the job's result recorded with the store, as the document job does)."""
    data = draw(
        [text or f"Quartalsbericht\nDepot-Nr. {number}\nStammnummer 12345678\nSeite 1 von 1"],
        salt=uuid.uuid4().hex,
    )
    doc = run(
        ws,
        documents.upload_document,
        blob_store,
        data,
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename="bericht.pdf",
    )
    queued = run(ws, proposals.request_read, doc.id)
    classify_id = run(ws, _ai_call, doc.id, "classify")
    extract_id = run(ws, _ai_call, doc.id, "extract")
    ready = job(
        ws,
        proposals.record_read_result,
        queued.id,
        classification=classification,
        classify_call_id=classify_id,
        values=values
        or _report_values(
            ["IE00BEISPL01 | Beispiel Welt ETF | 2,5000 | 101,00 | 252,50 | EUR"]
            if positions is None
            else positions
        ),
        extract_call_id=extract_id,
        iban_key=TEST_IBAN_KEY,
        store=blob_store,
    )
    assert ready.status is ProposalStatus.READY
    return doc, ready


@pytest.fixture
def job(app_engine):
    def _job(ws, command, *args, **kwargs):
        with workspace_transaction(app_engine, ws) as conn:
            return command(new_command(conn, ws, JOB, now=T0), *args, **kwargs)

    return _job


def _account_field(proposal, key: str = "holding:1"):
    holding = next(r for r in proposal.records if r.key == key)
    return holding.fields["account_id"]


def _ready_now(read, ws, doc_id):
    (current,) = [
        p
        for p in read(ws, proposals.list_document_proposals, doc_id)
        if p.status is ProposalStatus.READY
    ]
    return current


class _Flaky:
    """A store that fails the second read of any key (review, 15 Nov 2026): a followed
    file's first read is the search for waiting files, its second the import."""

    def __init__(self, inner):
        self._inner, self._seen = inner, set()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get(self, key):
        if key in self._seen:
            raise TimeoutError("object storage timed out")
        self._seen.add(key)
        return self._inner.get(key)


class _Unreachable:
    """A store whose every read fails."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get(self, key):
        raise TimeoutError("object storage timed out")


def _tax_year(proposal):
    return next(r for r in proposal.records if r.key == "typing").fields["tax_year"].value


def test_a_report_printing_depot_b_pre_selects_b_among_two(
    run, read, job, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    _depot(run, ws, number=DEPOT_A_NUMBER)
    b = _depot(run, ws, label="Megatrends", number=DEPOT_B_NUMBER, n=1)
    _, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    chosen = _account_field(ready)
    assert (chosen.value, chosen.confidence) == (str(b.id), "high")
    assert _tax_year(ready) == 2026  # the report's Stichtag
    assert DEPOT_B_NUMBER not in ready.model_dump_json()


def test_creating_the_depot_rebuilds_a_ready_reading_with_it_pre_selected(
    run, read, job, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    _depot(run, ws, number=DEPOT_A_NUMBER)
    _depot(run, ws, label="Zweites Depot", number=OTHER_KONTO, n=1)
    doc, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    assert _account_field(ready).value is None  # two depots, neither printed

    b = _depot(run, ws, blob_store, label="Megatrends", number=DEPOT_B_NUMBER, n=2)
    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.SUPERSEDED
    current = [
        p
        for p in read(ws, proposals.list_document_proposals, doc.id)
        if p.status is ProposalStatus.READY
    ]
    (new,) = current
    assert new.extraction_id == ready.extraction_id  # the same reading (D132's stale guard)
    assert new.ai_call_id == ready.ai_call_id
    assert (_account_field(new).value, _account_field(new).confidence) == (str(b.id), "high")
    assert str(b.id) in [o.value for o in new.records[1].options["account_id"]]

    # Nothing changed for it a second time: no new suggestion.
    assert run(ws, proposals.rebuild_depot_proposals, blob_store, iban_key=TEST_IBAN_KEY) == 0


def test_an_applied_reading_is_never_rebuilt(run, read, job, blob_store, fresh_workspaces):
    """A reading applied on the only depot (pre-selected by the rule of one) stays as it is
    when a second depot, the one it prints, is created: what was applied is never redone."""
    ws, _ = fresh_workspaces
    a = _depot(run, ws)  # no number yet, so nothing tells it is not the depot printed
    doc, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    assert _account_field(ready).value == str(a.id)
    applied = run(
        ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY
    )
    assert applied.proposal.status is ProposalStatus.APPLIED
    _depot(run, ws, blob_store, label="Megatrends", number=DEPOT_B_NUMBER, n=1)
    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.APPLIED
    assert [p.id for p in read(ws, proposals.list_document_proposals, doc.id)] == [ready.id]


ELSEWHERE_NOTE = (
    "Dieses Dokument nennt ein anderes Depot, als unter Konten angelegt ist. Bitte zuerst "
    "dieses Depot anlegen (Konten › „Konto anlegen“, Kontoart „Depot“), dann hier wählen."
)


def test_a_printed_number_no_depot_holds_pre_selects_nothing(
    run, read, job, blob_store, fresh_workspaces
):
    """Review, 15 Nov 2026: the only depot has another number, so the document is another
    depot's; it is never put on the only one (it was, at low confidence, until then)."""
    ws, _ = fresh_workspaces
    a = _depot(run, ws, number=DEPOT_A_NUMBER)
    _, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    chosen = _account_field(ready)
    assert chosen.value is None
    holding = next(r for r in ready.records if r.key == "holding:1")
    assert holding.notes[0] == ELSEWHERE_NOTE
    assert [o.value for o in holding.options["account_id"]] == [str(a.id)]


def test_a_printed_number_with_the_only_depot_unnumbered_falls_back_to_it(
    run, read, job, blob_store, fresh_workspaces
):
    """A depot without a number could be the one printed, so the rule of one still holds."""
    ws, _ = fresh_workspaces
    a = _depot(run, ws)
    _, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    chosen = _account_field(ready)
    assert (chosen.value, chosen.confidence) == (str(a.id), "low")


def test_an_einbuchung_into_a_depot_not_created_yet_pre_selects_nothing(
    run, read, job, blob_store, fresh_workspaces
):
    """The giving depot (A) is the only one under Konten; the Einbuchung prints the
    receiving depot's number (B) under its label and A's only beside the holder's name."""
    ws, _ = fresh_workspaces
    _depot(run, ws, number=DEPOT_A_NUMBER)
    _, ready = _report(
        run,
        job,
        ws,
        blob_store,
        DEPOT_B_NUMBER,
        text=(
            f"Einbuchung von Wertpapieren\nDepot-Nr. {DEPOT_B_NUMBER}\n"
            f"Übertrag von Beispiel, Erika {DEPOT_A_NUMBER}\nSeite 1 von 1"
        ),
        classification=TRANSFER,
        values={
            "title": _v("Einbuchung von Wertpapieren"),
            "document_date": _v("2026-04-02"),
            "direction": _v("Einbuchung"),
            "transfer_date": _v("2026-04-02"),
            "reference": _v("UMB0001"),
            "positions": _v(["IE00BEISPL01 | Beispiel Welt ETF | 3,0000"]),
        },
    )
    assert _account_field(ready, "movement:1").value is None
    movement = next(r for r in ready.records if r.key == "movement:1")
    assert movement.notes[0] == ELSEWHERE_NOTE


DEPOT_A_IBAN = "DE89370400440532013000"  # the public documentation example


def _give_iban(ctx, account_id, iban: str):
    protected = protect_iban(iban, TEST_IBAN_KEY)
    ctx.conn.execute(
        update(account_table)
        .where(account_table.c.id == account_id)
        .values(iban_masked=protected.masked, iban_hmac=protected.hmac)
    )


def test_a_labelled_number_decides_alone_over_a_printed_iban(
    run, read, job, blob_store, fresh_workspaces
):
    """Review, 15 Nov 2026: the document prints depot B's number under its label and depot
    A's IBAN; B is not under Konten, so it is another depot's and A's IBAN does not count
    (it would have pre-selected A until then)."""
    ws, _ = fresh_workspaces
    a = _depot(run, ws, number=DEPOT_A_NUMBER)
    run(ws, _give_iban, a.id, DEPOT_A_IBAN)
    _, ready = _report(
        run,
        job,
        ws,
        blob_store,
        DEPOT_B_NUMBER,
        text=f"Quartalsbericht\nDepot-Nr. {DEPOT_B_NUMBER}\nIBAN {DEPOT_A_IBAN}\nSeite 1 von 1",
    )
    assert _account_field(ready).value is None
    # Without a labelled number, the IBAN finds its depot.
    _, by_iban = _report(
        run,
        job,
        ws,
        blob_store,
        DEPOT_B_NUMBER,
        text=f"Quartalsbericht\nIBAN {DEPOT_A_IBAN}\nSeite 1 von 1",
    )
    assert (_account_field(by_iban).value, _account_field(by_iban).confidence) == (
        str(a.id),
        "high",
    )


def test_a_rebuild_that_cannot_read_the_document_leaves_its_suggestion(
    run, read, job, blob_store, fresh_workspaces
):
    """Review, 15 Nov 2026: a passing store error during a rebuild never takes a
    pre-selection away; the suggestion stays as it is."""
    ws, _ = fresh_workspaces
    a = _depot(run, ws)
    doc, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    assert _account_field(ready).value == str(a.id)
    _depot(run, ws, _Unreachable(blob_store), label="Megatrends", number=OTHER_KONTO, n=1)
    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.READY
    assert _ready_now(read, ws, doc.id).id == ready.id


def test_one_suggestion_that_cannot_be_rebuilt_blocks_no_other(
    run, read, job, blob_store, fresh_workspaces, monkeypatch, caplog
):
    ws, _ = fresh_workspaces
    _depot(run, ws)
    broken_doc, broken = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    doc, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    real = proposals.build_proposal

    def failing(kind, extraction, context):
        if context.document_id == broken_doc.id:
            raise ValueError("a secret value 4711")
        return real(kind, extraction, context)

    monkeypatch.setattr(proposals, "build_proposal", failing)
    caplog.set_level(logging.WARNING, logger="hausbuch.services.proposals")
    b = _depot(run, ws, blob_store, label="Megatrends", number=DEPOT_B_NUMBER, n=1)
    assert read(ws, proposals.get_proposal, broken.id).status is ProposalStatus.READY
    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.SUPERSEDED
    assert _account_field(_ready_now(read, ws, doc.id)).value == str(b.id)
    (record,) = [r for r in caplog.records if r.name == "hausbuch.services.proposals"]
    assert record.fields == {"proposal_id": str(broken.id), "exc_type": "ValueError"}
    assert "4711" not in caplog.text


# --- Review fixes of 15 Nov 2026: the follow -----------------------------------------------


def test_the_import_returns_the_status_the_follow_left_it_with(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """Statement 3 imported onto a depot without a number: its number is set and statements
    1 and 2 follow, after which 3 reconciles; the answer says so, not what it was before."""
    ws, _ = fresh_workspaces
    docs = {
        n: _uploaded_and_refused(
            app_engine, blob_store, ws, tmp_path, _statement(n), "application/pdf"
        )[0]
        for n in (1, 2, 3)
    }
    depot = _depot(run, ws)
    returned = run(
        ws,
        statements.import_stored_statement,
        blob_store,
        docs[3].id,
        depot.id,
        iban_key=TEST_IBAN_KEY,
        now=T0 + timedelta(minutes=5),
    )
    stored = {i.document_id: i for i in read(ws, list_imports, depot.id)}
    assert len(stored) == 3
    assert returned.status is stored[docs[3].id].status
    assert returned.status is ImportStatus.RECONCILED


def test_a_followed_file_that_cannot_be_read_keeps_its_offer_and_rolls_back_nothing(
    run, read, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls, caplog
):
    ws, _ = fresh_workspaces
    docs = [
        _uploaded_and_refused(
            app_engine, blob_store, ws, tmp_path, _statement(n), "application/pdf"
        )[0]
        for n in (1, 2)
    ]
    caplog.set_level(logging.WARNING, logger="hausbuch.services.statements")
    account = _depot(run, ws, _Flaky(blob_store), number=KONTO)
    assert read(ws, get_account, account.id).id == account.id  # the account stays
    assert read(ws, list_imports, account.id) == []
    for doc in docs:
        assert _offer(read, blob_store, ws, doc.id).suggested_account_id == account.id
    logged = [r.fields for r in caplog.records if r.name == "hausbuch.services.statements"]
    assert logged == [{"document_id": str(doc.id), "exc_type": "TimeoutError"} for doc in docs]

    followed = run(
        ws,
        statements.import_waiting_statements,
        _Flaky(blob_store),
        account.id,
        iban_key=TEST_IBAN_KEY,
    )
    assert followed.imported == []
    assert [(r.document_id, r.code) for r in followed.refused] == [
        (doc.id, "statement.follow_unreadable") for doc in docs
    ]
    # Once the store answers again, they follow.
    again = run(
        ws, statements.import_waiting_statements, blob_store, account.id, iban_key=TEST_IBAN_KEY
    )
    assert [i.document_id for i in again.imported] == [doc.id for doc in docs]


def test_the_catch_up_command_queues_one_job_that_follows_the_existing_accounts(
    dbname,
    run,
    read,
    job,
    app_engine,
    blob_store,
    fresh_workspaces,
    tmp_path,
    hook_calls,  # noqa: F811
    capsys,
):
    """`hausbuch follow-accounts` once after the deploy: files that waited for an account that
    existed before this change are imported onto it, and a ready depot reading is rebuilt with
    the depot pre-selected. --dry-run counts and queues nothing; the worker's job does it."""
    ws, _ = fresh_workspaces
    docs = [
        _uploaded_and_refused(
            app_engine, blob_store, ws, tmp_path, _statement(n), "application/pdf"
        )[0]
        for n in (2, 1)
    ]
    _, ready = _report(run, job, ws, blob_store, DEPOT_B_NUMBER)
    assert _account_field(ready).value is None  # no depot yet
    _depot(run, ws, number=KONTO)  # created before the deploy: nothing followed
    b = _depot(run, ws, label="Megatrends", number=DEPOT_B_NUMBER, n=1)
    assert set(read(ws, statements.waiting_statement_documents)) == {d.id for d in docs}

    def queued() -> int:
        with app_engine.begin() as conn:
            return conn.execute(
                text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = :t"),
                {"t": FOLLOW_ACCOUNTS_TASK},
            ).scalar_one()

    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["follow-accounts", "--workspace", str(ws)]
    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "2 statement file(s)" in out and "1 ready depot suggestion(s)" in out
    assert "nothing queued" in out and queued() == 0

    try:
        assert cli.main(argv, environ) == cli.EXIT_OK
        assert "postgresql" not in capsys.readouterr().out
        assert queued() == 1
    finally:
        with app_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM procrastinate_jobs WHERE task_name = :t"),
                {"t": FOLLOW_ACCOUNTS_TASK},
            )
    deps = JobDeps(
        engine=app_engine,
        settings=_test_settings(tmp_path),
        blob_store=blob_store,
        model_client=None,
        clock=lambda: T0,
    )
    result = run_follow_accounts(deps, workspace_id=ws)
    assert (result.imported, result.refused, result.rebuilt) == (2, 0, 1)
    assert read(ws, statements.waiting_statement_documents) == []
    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.SUPERSEDED
    (rebuilt,) = [
        p
        for p in read(ws, proposals.list_document_proposals, ready.document_id)
        if p.status is ProposalStatus.READY
    ]
    assert (_account_field(rebuilt).value, _account_field(rebuilt).confidence) == (
        str(b.id),
        "high",
    )
    again = run_follow_accounts(deps, workspace_id=ws)
    assert (again.imported, again.refused, again.rebuilt) == (0, 0, 0)
