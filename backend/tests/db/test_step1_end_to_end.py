"""Phase 2 step 1 end to end, across both chunks: upload → request_read (real enqueue) →
document_read with a scripted model client → the real record_read_result → a ready proposal
→ apply through the phase 1 services. The chunk tests replace one side each; this one
replaces only the model. All model responses are synthetic (hausbuch.ai.fake).
"""

import pytest
from sqlalchemy import select, text
from test_jobs_document_read import INVOICE_ANSWER, NOW, USER, _classify, _pdf, _settings

from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.schemas.enums import DocumentSource, ProposalStatus
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import documents, proposals
from hausbuch.services.context import new_command

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _empty_queue(app_engine):
    """request_read enqueues for real; leave the session's shared queue as we found it."""

    def clear():
        with app_engine.begin() as conn:
            conn.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'document_read'"))

    clear()
    yield
    clear()


def _upload_and_request(app_engine, blob_store, ws, data, *, tenant_data=False):
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, USER, now=NOW)
        doc = documents.upload_document(
            ctx, blob_store, data, media_type="application/pdf", source=DocumentSource.UPLOAD
        )
        proposal = proposals.request_read(ctx, doc.id, contains_tenant_data=tenant_data)
    return doc.id, proposal


def test_upload_read_and_apply_an_invoice(app_engine, fresh_workspaces, blob_store, tmp_path):
    ws, _ = fresh_workspaces
    doc, proposal = _upload_and_request(app_engine, blob_store, ws, _pdf(2, "e2e-invoice"))
    assert proposal.status is ProposalStatus.QUEUED

    with app_engine.begin() as conn:  # the real enqueue wrote exactly one job
        jobs = conn.execute(
            text("SELECT args FROM procrastinate_jobs WHERE task_name = 'document_read'")
        ).all()
    assert len(jobs) == 1 and jobs[0].args["proposal_id"] == str(proposal.id)

    client = FakeModelClient(
        [text_response(_classify()), cited_response(INVOICE_ANSWER, model="claude-sonnet-5-5")]
    )
    deps = JobDeps(
        engine=app_engine,
        settings=_settings(tmp_path),
        blob_store=blob_store,
        model_client=client,
        clock=lambda: NOW,
    )
    run_document_read(deps, workspace_id=ws, document_id=doc, proposal_id=proposal.id, attempt=0)

    with workspace_transaction(app_engine, ws) as conn:
        ready = proposals.get_proposal(conn, proposal.id)
        calls = conn.execute(select(ai_call_table.c.id, ai_call_table.c.job)).all()
        extraction = documents.current_extraction(conn, doc)
    assert ready.status is ProposalStatus.READY, ready.error_code
    assert ready.records, "a ready proposal with no records"
    assert {c.job for c in calls} == {"classify", "extract"}
    assert extraction is not None and extraction.kind != "manual"
    assert extraction.ai_call_id in {c.id for c in calls}
    keys = {r.key for r in ready.records}
    assert "typing" in keys

    typing = next(r for r in ready.records if r.key == "typing")
    body = ProposalApplyBody(
        records=[
            ProposalApplyRecord(
                key=r.key,
                skip=r.key != "typing",
                values={k: v.value for k, v in typing.fields.items()} if r.key == "typing" else {},
            )
            for r in ready.records
        ]
    )
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, USER, now=NOW)
        result = proposals.apply_proposal(ctx, proposal.id, body, iban_key=bytes(range(32)))
    with workspace_transaction(app_engine, ws) as conn:
        applied = proposals.get_proposal(conn, proposal.id)
        document = documents.get_document(conn, doc)
    assert applied.status is ProposalStatus.APPLIED
    assert result is not None
    assert document.typing is not None  # typed through the phase 1 service


def test_a_marked_upload_never_reaches_the_model(
    app_engine, fresh_workspaces, blob_store, tmp_path
):
    ws, _ = fresh_workspaces
    doc, proposal = _upload_and_request(
        app_engine, blob_store, ws, _pdf(1, "e2e-tenant"), tenant_data=True
    )
    assert proposal.status is ProposalStatus.BLOCKED_TENANT_DATA
    client = FakeModelClient([])
    deps = JobDeps(
        engine=app_engine,
        settings=_settings(tmp_path),
        blob_store=blob_store,
        model_client=client,
        clock=lambda: NOW,
    )
    # Even if a job were run for it anyway, the row's flag stops it before any call.
    run_document_read(deps, workspace_id=ws, document_id=doc, proposal_id=proposal.id, attempt=0)
    assert client.requests == []
    with workspace_transaction(app_engine, ws) as conn:
        assert conn.execute(select(ai_call_table.c.id)).all() == []
