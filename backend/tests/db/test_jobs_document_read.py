"""The document_read job against the real database (as hausbuch_app, RLS on), with a scripted
model client. services.proposals.record_read_* belong to the proposals chunk; here they are
replaced by recorders, so the tests assert exactly what the job hands over.

All model responses here are synthetic (hausbuch.ai.fake), never recordings of the real API.
"""

import inspect
import io
import json
import logging
import uuid
from datetime import UTC, datetime

import pytest
from pypdf import PdfWriter
from sqlalchemy import insert, select, update

from hausbuch.ai.client import ModelCallError
from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.jobs import document_read as job
from hausbuch.jobs import tasks
from hausbuch.jobs.document_read import JobDeps, RetryLater, run_document_read
from hausbuch.schemas.enums import ActorType, DocumentSource, DocumentType
from hausbuch.schemas.extraction import EXTRACTION_MODELS, ExtractionKind
from hausbuch.services import proposals
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import upload_document
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

KEY = "sk-ant-api03-SECRET-job-test-key"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
USER = Actor(ActorType.USER, "user:test")


def _pdf(pages: int, marker: str = "") -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Title": marker or uuid.uuid4().hex})  # distinct bytes per test
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _classify(type_="invoice", subtype=None, tenant=False) -> str:
    return json.dumps(
        {
            "type": type_,
            "subtype": subtype,
            "contains_tenant_data": tenant,
            "language": "de",
            "confidence": "high",
        }
    )


INVOICE_ANSWER = {
    "title": ("Rechnung Notar", 1, "Rechnung Notar Dr. Beispiel"),
    "sender": ("Notar Dr. Beispiel", 1, "Notar Dr. Beispiel, Musterstr. 1"),
    "amount_total": ("1.234,56 EUR", 1, "Gesamtbetrag 1.234,56 EUR"),
    "due_on": "unbekannt",
}


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple] = []
        self.failures: list[tuple] = []

    def record_result(self, ctx, proposal_id, **kwargs):
        assert ctx.conn.in_transaction()
        self.results.append((ctx, proposal_id, kwargs))

    def record_failure(self, ctx, proposal_id, **kwargs):
        assert ctx.conn.in_transaction()
        self.failures.append((ctx, proposal_id, kwargs))


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(proposals, "record_read_result", rec.record_result)
    monkeypatch.setattr(proposals, "record_read_failure", rec.record_failure)
    return rec


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": bytes(range(32)),
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": tmp_path / "blobs",
        "ai_enabled": True,
        "anthropic_api_key": KEY,
    } | overrides
    return Settings(**values)


@pytest.fixture
def setup(app_engine, fresh_workspaces, blob_store, tmp_path):
    """setup(data, media_type, client, **settings) -> (deps, ws, document_id, proposal_id)."""
    ws, _ = fresh_workspaces

    def _setup(data: bytes, client, *, media_type="application/pdf", tenant_data=False, **settings):
        with workspace_transaction(app_engine, ws) as conn:
            doc = upload_document(
                new_command(conn, ws, USER, now=NOW),
                blob_store,
                data,
                media_type=media_type,
                source=DocumentSource.UPLOAD,
            )
            proposal_id = _new_proposal(conn, ws, doc.id, tenant_data=tenant_data)
        deps = JobDeps(
            engine=app_engine,
            settings=_settings(tmp_path, **settings),
            blob_store=blob_store,
            model_client=client,
            clock=lambda: NOW,
        )
        return deps, ws, doc.id, proposal_id

    return _setup


def _new_proposal(conn, ws, document_id, *, tenant_data=False) -> uuid.UUID:
    return conn.execute(
        insert(proposal_table)
        .values(workspace_id=ws, document_id=document_id, contains_tenant_data=tenant_data)
        .returning(proposal_table.c.id)
    ).scalar_one()


def _run(deps, ws, doc, proposal, attempt=0, **kwargs):
    run_document_read(
        deps, workspace_id=ws, document_id=doc, proposal_id=proposal, attempt=attempt, **kwargs
    )


def _calls(engine, ws, doc):
    with workspace_transaction(engine, ws) as conn:
        return conn.execute(
            select(ai_call_table)
            .where(ai_call_table.c.document_id == doc)
            .order_by(ai_call_table.c.created_at, ai_call_table.c.job)
        ).all()


def _allow_tenant_documents(engine, ws) -> None:
    with workspace_transaction(engine, ws) as conn:
        conn.execute(
            update(workspace_table)
            .where(workspace_table.c.id == ws)
            .values(ai_tenant_documents=True)
        )


# --- the happy path --------------------------------------------------------------------


def test_invoice_is_classified_extracted_and_handed_over(setup, recorder, app_engine):
    client = FakeModelClient(
        [text_response(_classify()), cited_response(INVOICE_ANSWER, model="claude-sonnet-5-5")]
    )
    deps, ws, doc, proposal = setup(_pdf(5), client)
    _run(deps, ws, doc, proposal)

    assert recorder.failures == []
    [(ctx, proposal_id, kw)] = recorder.results
    assert proposal_id == proposal
    assert ctx.workspace_id == ws and ctx.now == NOW
    assert ctx.actor == Actor(ActorType.AI, "claude-sonnet-5-5")
    assert kw["classification"].type is DocumentType.INVOICE
    assert kw["blocked_tenant_data"] is False
    values = kw["values"]
    assert set(values) == set(EXTRACTION_MODELS[ExtractionKind.INVOICE].model_fields)
    assert values["amount_total"].value == "1234.56"
    assert values["amount_total"].confidence == "high"
    assert values["amount_total"].source.page == 1
    assert values["due_on"].value is None
    assert values["reference"].value is None  # not stated at all

    rows = _calls(app_engine, ws, doc)
    assert [r.job for r in rows] == ["classify", "extract"]
    assert kw["classify_call_id"] == rows[0].id and kw["extract_call_id"] == rows[1].id
    for row, request in zip(rows, client.requests, strict=True):
        assert row.input_sha256 == request.sha256()
        assert row.inference_geo == "us"
        assert row.error_code is None
        assert KEY not in (row.raw_text or "") and KEY not in json.dumps(row.output)
    assert rows[0].output["type"] == "invoice"
    assert rows[1].output["amount_total"]["value"] == "1234.56"

    classify, extract = client.requests
    assert classify.model == "claude-haiku-5-5" and extract.model == "claude-sonnet-5-5"
    # Classification sends the first three pages, extraction the whole (short) file.
    assert classify.content[0]["source"]["data"] != extract.content[0]["source"]["data"]
    assert extract.content[0]["citations"] == {"enabled": True}


def test_long_pdf_is_split_and_citations_get_the_page_offset(setup, recorder, app_engine):
    client = FakeModelClient(
        [
            text_response(_classify()),
            cited_response({"title": ("Rechnung", 1, "Rechnung")}),
            cited_response({"amount_total": ("99,00 EUR", 1, "Summe 99,00 EUR")}),
            cited_response({"reference": "unbekannt"}),
        ]
    )
    deps, ws, doc, proposal = setup(_pdf(5), client, ai_max_pages=2)
    _run(deps, ws, doc, proposal)

    [(_, _, kw)] = recorder.results
    values = kw["values"]
    assert values["title"].source.page == 1
    assert values["amount_total"].source.page == 3  # page 1 of the second chunk (pages 3-4)
    assert len(client.requests) == 4
    rows = _calls(app_engine, ws, doc)
    assert [r.job for r in rows].count("extract") == 3
    extract_ids = {r.id for r in rows if r.job == "extract"}
    assert kw["extract_call_id"] in extract_ids


# --- the privacy gate (§5.5) -----------------------------------------------------------


def test_gate_1_ai_disabled_sends_nothing(setup, recorder, app_engine):
    client = FakeModelClient()
    deps, ws, doc, proposal = setup(_pdf(1), client, ai_enabled=False, anthropic_api_key=None)
    _run(deps, ws, doc, proposal)
    assert client.requests == []
    [(ctx, proposal_id, kw)] = recorder.failures
    assert (proposal_id, kw) == (
        proposal,
        {"error_code": "ai.disabled", "ai_call_id": None, "attempts": 1},
    )
    assert ctx.actor.actor_type is ActorType.SYSTEM
    assert _calls(app_engine, ws, doc) == []


def test_gate_2_marked_upload_is_blocked_before_any_call(setup, recorder, app_engine):
    """The flag is read from the proposal row (D16 gate 2): with the workspace setting off,
    a proposal marked as tenant data makes zero model calls and writes no ai_call row."""
    client = FakeModelClient()
    deps, ws, doc, proposal = setup(_pdf(1), client, tenant_data=True)
    _run(deps, ws, doc, proposal)
    assert client.requests == []
    assert _calls(app_engine, ws, doc) == []
    [(_, proposal_id, kw)] = recorder.failures
    assert (proposal_id, kw) == (
        proposal,
        {"error_code": "ai.blocked_tenant_data", "ai_call_id": None, "attempts": 1},
    )


def test_gate_2_lets_a_marked_upload_through_when_allowed(setup, recorder, app_engine):
    client = FakeModelClient([text_response(_classify("letter"))])
    deps, ws, doc, proposal = setup(_pdf(1), client, tenant_data=True)
    _allow_tenant_documents(app_engine, ws)
    _run(deps, ws, doc, proposal)
    assert recorder.failures == []
    assert len(recorder.results) == 1


def test_gate_2_uses_the_flag_of_the_proposal_it_serves(setup, recorder, app_engine):
    """A job whose proposal is no longer pending serves the document's newest pending one,
    and that proposal's flag decides gate 2, not the original's."""
    client = FakeModelClient()
    deps, ws, doc, first = setup(_pdf(1), client)
    with workspace_transaction(app_engine, ws) as conn:
        conn.execute(
            update(proposal_table)
            .where(proposal_table.c.id == first)
            .values(status="failed", error_code="ai.api_error")
        )
        second = _new_proposal(conn, ws, doc, tenant_data=True)
    _run(deps, ws, doc, first)
    assert client.requests == []
    [(_, proposal_id, kw)] = recorder.failures
    assert (proposal_id, kw["error_code"]) == (second, "ai.blocked_tenant_data")


def test_the_task_takes_no_tenant_data_argument():
    """Nothing can bypass gate 2 by enqueueing with contains_tenant_data=False."""
    assert "contains_tenant_data" not in inspect.signature(run_document_read).parameters
    assert "contains_tenant_data" not in inspect.signature(tasks.document_read).parameters


@pytest.mark.parametrize(
    "answer",
    [_classify("contract", "lease"), _classify("letter", None, tenant=True)],
    ids=["lease", "tenant-letter"],
)
def test_gate_3_tenant_document_stops_after_classification(setup, recorder, app_engine, answer):
    client = FakeModelClient([text_response(answer)])
    deps, ws, doc, proposal = setup(_pdf(2), client)
    _run(deps, ws, doc, proposal)

    assert len(client.requests) == 1  # no extract request
    [(ctx, _, kw)] = recorder.results
    assert kw["blocked_tenant_data"] is True
    assert kw["values"] is None and kw["extract_call_id"] is None
    [row] = _calls(app_engine, ws, doc)  # the classification's row is kept
    assert kw["classify_call_id"] == row.id
    assert ctx.actor == Actor(ActorType.AI, "claude-haiku-5-5")


def test_gate_3_open_when_the_workspace_allows_tenant_documents(setup, recorder, app_engine):
    client = FakeModelClient(
        [
            text_response(_classify("contract", "lease", tenant=True)),
            cited_response({"cold_rent": ("850,00 EUR", 2, "Nettokaltmiete 850,00 EUR")}),
        ]
    )
    deps, ws, doc, proposal = setup(_pdf(2), client)
    _allow_tenant_documents(app_engine, ws)
    _run(deps, ws, doc, proposal)
    [(_, _, kw)] = recorder.results
    assert kw["blocked_tenant_data"] is False
    assert kw["values"]["cold_rent"].value == "850.00"


def test_type_without_extraction_is_typing_only(setup, recorder, app_engine):
    client = FakeModelClient([text_response(_classify("letter"))])
    deps, ws, doc, proposal = setup(_pdf(1), client)
    _run(deps, ws, doc, proposal)
    [(_, _, kw)] = recorder.results
    assert kw["values"] is None and kw["blocked_tenant_data"] is False
    assert len(client.requests) == 1


# --- answers that cannot be used (§5.3) ------------------------------------------------


def test_refusal_twice_fails_with_refused(setup, recorder, app_engine):
    client = FakeModelClient(
        [text_response("", stop_reason="refusal"), text_response("", stop_reason="max_tokens")]
    )
    deps, ws, doc, proposal = setup(_pdf(1), client)
    _run(deps, ws, doc, proposal)
    rows = _calls(app_engine, ws, doc)
    assert [r.error_code for r in rows] == ["ai.refused", "ai.refused"]
    [(_, _, kw)] = recorder.failures
    assert kw["error_code"] == "ai.refused"
    assert kw["ai_call_id"] in {r.id for r in rows}


def test_unparsable_classification_is_retried_once(setup, recorder, app_engine):
    client = FakeModelClient(
        [
            text_response("not json"),
            text_response(_classify("letter")),
        ]
    )
    deps, ws, doc, proposal = setup(_pdf(1), client)
    _run(deps, ws, doc, proposal)
    assert recorder.failures == [] and len(recorder.results) == 1
    rows = _calls(app_engine, ws, doc)
    assert sorted(r.error_code or "" for r in rows) == ["", "ai.unparsable"]


def test_extract_without_a_parsable_line_twice_fails(setup, recorder, app_engine):
    client = FakeModelClient(
        [
            text_response(_classify()),
            text_response("Ich kann das nicht lesen."),
            text_response("nothing: here"),
        ]
    )
    deps, ws, doc, proposal = setup(_pdf(1), client)
    _run(deps, ws, doc, proposal)
    [(_, _, kw)] = recorder.failures
    assert kw["error_code"] == "ai.unparsable"
    assert recorder.results == []


def test_unsupported_media_type_fails_without_a_call(setup, recorder):
    client = FakeModelClient()
    deps, ws, doc, proposal = setup(b"plain text", client, media_type="text/plain")
    _run(deps, ws, doc, proposal)
    assert client.requests == []
    [(_, _, kw)] = recorder.failures
    assert kw == {"error_code": "ai.unsupported_media_type", "ai_call_id": None, "attempts": 1}


def test_damaged_pdf_fails_as_unreadable(setup, recorder):
    deps, ws, doc, proposal = setup(b"%PDF-1.7 broken " + uuid.uuid4().bytes, FakeModelClient())
    _run(deps, ws, doc, proposal)
    [(_, _, kw)] = recorder.failures
    assert kw["error_code"] == "ai.unreadable"


# --- retries (§5.1) --------------------------------------------------------------------


def test_retryable_error_asks_for_a_retry_then_fails_on_the_last_attempt(
    setup, recorder, app_engine
):
    error = ModelCallError(retryable=True, status=529, request_id="req_1")
    client = FakeModelClient([error, error, error, error])
    deps, ws, doc, proposal = setup(_pdf(1), client)
    for attempt, wait in enumerate((10, 60, 300)):
        with pytest.raises(RetryLater) as info:
            _run(deps, ws, doc, proposal, attempt=attempt)
        assert info.value.wait_s == wait
        assert info.value.__context__ is None or info.value.__suppress_context__
    assert recorder.failures == []
    _run(deps, ws, doc, proposal, attempt=job.MAX_RETRIES)
    [(_, _, kw)] = recorder.failures
    rows = _calls(app_engine, ws, doc)
    assert [r.error_code for r in rows] == ["ai.unavailable"] * 4
    assert kw["error_code"] == "ai.unavailable" and kw["ai_call_id"] in {r.id for r in rows}
    # document.read_failed carries the real count: the first run and three retries.
    assert kw["attempts"] == job.MAX_RETRIES + 1 == 4


def test_a_4xx_is_never_retried(setup, recorder, app_engine):
    client = FakeModelClient([ModelCallError(retryable=False, status=400)])
    deps, ws, doc, proposal = setup(_pdf(1), client)
    _run(deps, ws, doc, proposal, attempt=0)
    [(_, _, kw)] = recorder.failures
    assert kw["error_code"] == "ai.api_error"


def test_an_internal_error_is_retried_then_recorded(setup, recorder, monkeypatch):
    def broken(ctx, proposal_id, **kwargs):
        raise RuntimeError("secret value 1.234,56 in the message")

    monkeypatch.setattr(proposals, "record_read_result", broken)
    client = FakeModelClient([text_response(_classify("letter"))] * 2)
    deps, ws, doc, proposal = setup(_pdf(1), client)
    with pytest.raises(RetryLater):
        _run(deps, ws, doc, proposal, attempt=0)
    _run(deps, ws, doc, proposal, attempt=3)
    [(_, _, kw)] = recorder.failures
    assert kw == {"error_code": "ai.internal_error", "ai_call_id": None, "attempts": 4}


def test_failure_that_cannot_be_recorded_raises_job_failed(setup, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(proposals, "record_read_failure", broken)
    deps, ws, doc, proposal = setup(_pdf(1), FakeModelClient(), ai_enabled=False)
    with pytest.raises(job.JobFailed) as info:
        _run(deps, ws, doc, proposal)
    assert info.value.__suppress_context__ or info.value.__context__ is None


# --- which proposal a run serves -------------------------------------------------------


def _set_status(engine, ws, proposal, status):
    with workspace_transaction(engine, ws) as conn:
        conn.execute(
            update(proposal_table).where(proposal_table.c.id == proposal).values(status=status)
        )


def test_collapsed_rerun_serves_the_newest_pending_proposal(setup, recorder, app_engine):
    client = FakeModelClient([text_response(_classify("letter"))])
    deps, ws, doc, first = setup(_pdf(1), client)
    _set_status(app_engine, ws, first, "superseded")
    with workspace_transaction(app_engine, ws) as conn:
        newer = _new_proposal(conn, ws, doc)
    _run(deps, ws, doc, first)
    [(_, proposal_id, _)] = recorder.results
    assert proposal_id == newer


def test_nothing_pending_means_nothing_sent(setup, recorder, app_engine):
    client = FakeModelClient()
    deps, ws, doc, proposal = setup(_pdf(1), client)
    _set_status(app_engine, ws, proposal, "ready")
    _run(deps, ws, doc, proposal)
    assert client.requests == [] and recorder.results == [] and recorder.failures == []


# --- nothing secret in the logs --------------------------------------------------------


def test_logs_carry_no_key_and_no_values(setup, recorder, caplog):
    caplog.set_level(logging.DEBUG)
    error = ModelCallError(retryable=True, status=429)
    client = FakeModelClient([error, text_response(_classify()), cited_response(INVOICE_ANSWER)])
    deps, ws, doc, proposal = setup(_pdf(1), client)
    with pytest.raises(RetryLater):
        _run(deps, ws, doc, proposal, attempt=0)
    _run(deps, ws, doc, proposal, attempt=1)
    text = caplog.text + json.dumps([getattr(r, "fields", None) for r in caplog.records])
    assert KEY not in text
    assert "1.234,56" not in text and "Notar" not in text
