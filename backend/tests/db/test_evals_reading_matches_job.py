"""The eval request builders (hausbuch.evals.reading, used by `hausbuch evals run` and the
recorded evals) build exactly the requests the document_read job sends: the job is run
against the real database with the scripted fake client, on a PDF long enough to be split
into chunks, and every request it sent is compared by its SHA-256 (ai_call.input_sha256).

The model answers are synthetic (hausbuch.ai.fake), never API output."""

import io
import json
import uuid
from datetime import UTC, datetime

import pytest
from pypdf import PdfWriter
from sqlalchemy import insert

from hausbuch.ai import documents
from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.evals.reading import classify_request_for, eval_settings, extract_requests_for
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.schemas.enums import ActorType, DocumentSource, ExtractionKind
from hausbuch.services import proposals
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import upload_document

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
USER = Actor(ActorType.USER, "user:test")
CLASSIFY = json.dumps(
    {
        "type": "invoice",
        "subtype": None,
        "contains_tenant_data": False,
        "language": "de",
        "confidence": "high",
    }
)


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Title": uuid.uuid4().hex})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_the_job_sends_the_eval_builders_requests(
    app_engine, fresh_workspaces, blob_store, monkeypatch
):
    monkeypatch.setattr(proposals, "record_read_result", lambda ctx, proposal_id, **kw: None)
    ws, _ = fresh_workspaces
    data = _pdf(5)
    settings = eval_settings(
        ai_enabled=True, anthropic_api_key="sk-ant-test-not-a-key", ai_max_pages=2
    )
    answer = cited_response({"sender": ("Notar Dr. Beispiel", 1, "Notar Dr. Beispiel")})
    client = FakeModelClient([text_response(CLASSIFY), answer, answer, answer])
    with workspace_transaction(app_engine, ws) as conn:
        doc = upload_document(
            new_command(conn, ws, USER, now=NOW),
            blob_store,
            data,
            media_type=documents.PDF,
            source=DocumentSource.UPLOAD,
        )
        proposal_id = conn.execute(
            insert(proposal_table)
            .values(workspace_id=ws, document_id=doc.id)
            .returning(proposal_table.c.id)
        ).scalar_one()
    deps = JobDeps(
        engine=app_engine,
        settings=settings,
        blob_store=blob_store,
        model_client=client,
        clock=lambda: NOW,
    )

    run_document_read(deps, workspace_id=ws, document_id=doc.id, proposal_id=proposal_id, attempt=0)

    extract = extract_requests_for(settings, ExtractionKind.INVOICE, data, documents.PDF)
    assert [offset for offset, _ in extract] == [0, 2, 4]  # three chunks
    expected = [classify_request_for(settings, data, documents.PDF)] + [r for _, r in extract]
    assert [r.sha256() for r in client.requests] == [r.sha256() for r in expected]
