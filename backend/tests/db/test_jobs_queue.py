"""Enqueueing in the caller's transaction, the queueing lock, and the worker running a job
end to end (Procrastinate on the test database, as hausbuch_app).

The model is the scripted fake; services.proposals.record_read_* are recorders (the proposals
chunk implements them).
"""

import asyncio
import io
import json
import uuid
from datetime import UTC, datetime

import pytest
from db.db_support import ADMIN_URL, APP
from pypdf import PdfWriter
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url

from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.jobs.enqueue import document_lock, enqueue_document_read
from hausbuch.jobs.worker import build_worker, procrastinate_conninfo
from hausbuch.schemas.enums import ActorType, DocumentSource
from hausbuch.services import proposals
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import upload_document
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
USER = Actor(ActorType.USER, "user:test")


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Title": uuid.uuid4().hex})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _document(engine, ws, store) -> uuid.UUID:
    with workspace_transaction(engine, ws) as conn:
        return upload_document(
            new_command(conn, ws, USER, now=NOW),
            store,
            _pdf(),
            media_type="application/pdf",
            source=DocumentSource.UPLOAD,
        ).id


def _proposal(conn, ws, doc) -> uuid.UUID:
    return conn.execute(
        insert(proposal_table)
        .values(workspace_id=ws, document_id=doc)
        .returning(proposal_table.c.id)
    ).scalar_one()


def _jobs(engine, ws, doc):
    with workspace_transaction(engine, ws) as conn:
        return conn.execute(
            text(
                "SELECT task_name, args, queueing_lock, lock, status, attempts "
                "FROM procrastinate_jobs WHERE queueing_lock = :lock ORDER BY id"
            ),
            {"lock": document_lock(doc)},
        ).all()


def test_job_exists_only_if_the_transaction_commits(app_engine, fresh_workspaces, blob_store):
    ws, _ = fresh_workspaces
    doc = _document(app_engine, ws, blob_store)

    with pytest.raises(RuntimeError), workspace_transaction(app_engine, ws) as conn:
        proposal = _proposal(conn, ws, doc)
        enqueue_document_read(conn, workspace_id=ws, document_id=doc, proposal_id=proposal)
        raise RuntimeError("the request failed after enqueueing")
    assert _jobs(app_engine, ws, doc) == []

    with workspace_transaction(app_engine, ws) as conn:
        proposal = _proposal(conn, ws, doc)
        enqueue_document_read(conn, workspace_id=ws, document_id=doc, proposal_id=proposal)
    [row] = _jobs(app_engine, ws, doc)
    assert row.task_name == "document_read"
    assert row.args == {
        "workspace_id": str(ws),
        "document_id": str(doc),
        "proposal_id": str(proposal),
    }
    assert row.queueing_lock == row.lock == f"doc:{doc}"
    assert (row.status, row.attempts) == ("todo", 0)


def test_double_enqueue_collapses_and_keeps_the_transaction(
    app_engine, fresh_workspaces, blob_store
):
    ws, _ = fresh_workspaces
    doc = _document(app_engine, ws, blob_store)
    with workspace_transaction(app_engine, ws) as conn:
        first = _proposal(conn, ws, doc)
        enqueue_document_read(conn, workspace_id=ws, document_id=doc, proposal_id=first)
    with workspace_transaction(app_engine, ws) as conn:
        second = _proposal(conn, ws, doc)
        enqueue_document_read(conn, workspace_id=ws, document_id=doc, proposal_id=second)
        # The refused insert was rolled back to its savepoint: the transaction still works.
        assert (
            conn.execute(
                text("SELECT count(*) FROM proposal WHERE document_id = :d"), {"d": doc}
            ).scalar_one()
            == 2
        )
    [row] = _jobs(app_engine, ws, doc)
    assert row.args["proposal_id"] == str(first)


def test_worker_runs_a_queued_job(
    app_engine, dbname, fresh_workspaces, blob_store, tmp_path, monkeypatch
):
    ws, _ = fresh_workspaces
    doc = _document(app_engine, ws, blob_store)
    with workspace_transaction(app_engine, ws) as conn:
        proposal = _proposal(conn, ws, doc)
        enqueue_document_read(conn, workspace_id=ws, document_id=doc, proposal_id=proposal)

    results: list = []
    monkeypatch.setattr(
        proposals, "record_read_result", lambda ctx, pid, **kw: results.append((pid, kw))
    )
    monkeypatch.setattr(
        proposals, "record_read_failure", lambda ctx, pid, **kw: pytest.fail(f"failed: {kw}")
    )
    answer = json.dumps(
        {
            "type": "letter",
            "subtype": None,
            "contains_tenant_data": False,
            "language": "de",
            "confidence": "high",
        }
    )
    client = FakeModelClient([text_response(answer)])
    settings = Settings(
        database_url=make_url(ADMIN_URL).set(database=dbname).render_as_string(hide_password=False),
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root=tmp_path / "blobs",
        ai_enabled=True,
        anthropic_api_key="sk-ant-test",
    )
    worker = build_worker(
        settings,
        app_engine,
        blob_store,
        model_client=client,
        connector_kwargs={"kwargs": {"options": f"-c role={APP}"}},
        clock=lambda: NOW,
    )
    worker._run_options = {"wait": False, "listen_notify": False}

    async def scenario():
        await worker.start()
        await asyncio.wait_for(worker.wait(), timeout=60)
        await worker.stop(5)

    asyncio.run(scenario())

    assert [pid for pid, _ in results] == [proposal]
    assert len(client.requests) == 1
    [row] = _jobs(app_engine, ws, doc)
    assert row.status == "succeeded"
    assert not worker.running


def test_conninfo_drops_the_driver_suffix():
    url = "postgresql+psycopg://app:pw@db.internal:5432/hausbuch?sslmode=require"
    assert procrastinate_conninfo(url) == (
        "postgresql://app:pw@db.internal:5432/hausbuch?sslmode=require"
    )
