"""Enqueueing in the caller's transaction, the document lock, and the worker running a job
end to end (Procrastinate on the test database, as hausbuch_app).

The model is the scripted fake; services.proposals.record_read_* are recorders where a test
only checks the hand-over, the real service where a second run must see the first's outcome.
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
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.jobs.enqueue import document_lock, enqueue_document_read
from hausbuch.jobs.worker import build_worker, procrastinate_conninfo
from hausbuch.schemas.enums import ActorType, DocumentSource, ProposalStatus
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
                "FROM procrastinate_jobs WHERE lock = :lock ORDER BY id"
            ),
            {"lock": document_lock(doc)},
        ).all()


def _delete_document_jobs(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'document_read'"))


@pytest.fixture(autouse=True)
def _no_leftover_jobs(app_engine):
    """The queue is session-wide and not per workspace: jobs left in it would be fetched
    by any later test that runs a worker or fetches the next job (test_phase2_migrations'
    fetch-as-hausbuch_app test did, in the full suite). Each test starts and ends empty."""
    _delete_document_jobs(app_engine)
    yield
    _delete_document_jobs(app_engine)


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
    assert row.lock == f"doc:{doc}"
    assert row.queueing_lock is None  # see jobs.enqueue: a queueing lock breaks retries
    assert (row.status, row.attempts) == ("todo", 0)


def _letter_answer() -> str:
    return json.dumps(
        {
            "type": "letter",
            "subtype": None,
            "contains_tenant_data": False,
            "language": "de",
            "confidence": "high",
        }
    )


def _settings(dbname, tmp_path) -> Settings:
    return Settings(
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


def test_a_double_enqueue_gives_two_jobs_and_the_second_sends_nothing(
    app_engine, dbname, fresh_workspaces, blob_store, tmp_path
):
    """No queueing lock: both jobs are stored. The second run finds the proposal no longer
    pending (the first one served it) and sends no model request."""
    ws, _ = fresh_workspaces
    doc = _document(app_engine, ws, blob_store)
    with workspace_transaction(app_engine, ws) as conn:
        proposal = _proposal(conn, ws, doc)
        enqueue_document_read(conn, workspace_id=ws, document_id=doc, proposal_id=proposal)
    with workspace_transaction(app_engine, ws) as conn:
        enqueue_document_read(conn, workspace_id=ws, document_id=doc, proposal_id=proposal)
    rows = _jobs(app_engine, ws, doc)
    assert [(r.status, r.args["proposal_id"]) for r in rows] == [("todo", str(proposal))] * 2

    client = FakeModelClient([text_response(_letter_answer())])
    deps = JobDeps(
        engine=app_engine,
        settings=_settings(dbname, tmp_path),
        blob_store=blob_store,
        model_client=client,
        clock=lambda: NOW,
    )
    for row in rows:  # what the worker does with each job, one after the other (doc lock)
        run_document_read(
            deps,
            workspace_id=uuid.UUID(row.args["workspace_id"]),
            document_id=uuid.UUID(row.args["document_id"]),
            proposal_id=uuid.UUID(row.args["proposal_id"]),
            attempt=0,
        )
    assert len(client.requests) == 1
    with workspace_transaction(app_engine, ws) as conn:
        assert proposals.get_proposal(conn, proposal).status is ProposalStatus.READY


def test_retrying_a_running_job_while_a_rerun_waits_does_not_collide(
    app_engine, fresh_workspaces, blob_store
):
    """Job A is running (doing) when a re-run queues job B for the same document. A's
    retry (doing -> todo) must not hit a unique index on the queueing lock, which left A
    stuck in doing and, through its lock, blocked every later job of the document."""
    ws, _ = fresh_workspaces
    doc = _document(app_engine, ws, blob_store)
    with workspace_transaction(app_engine, ws) as conn:
        enqueue_document_read(
            conn, workspace_id=ws, document_id=doc, proposal_id=_proposal(conn, ws, doc)
        )
    with app_engine.begin() as conn:
        job_a = conn.execute(
            text("UPDATE procrastinate_jobs SET status = 'doing' WHERE lock = :lock RETURNING id"),
            {"lock": document_lock(doc)},
        ).scalar_one()
    with workspace_transaction(app_engine, ws) as conn:
        enqueue_document_read(
            conn, workspace_id=ws, document_id=doc, proposal_id=_proposal(conn, ws, doc)
        )
    with app_engine.begin() as conn:
        conn.execute(
            text("SELECT procrastinate_retry_job_v2(:id, now(), NULL, NULL, NULL)"),
            {"id": job_a},
        )
    assert [r.status for r in _jobs(app_engine, ws, doc)] == ["todo", "todo"]


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
    client = FakeModelClient([text_response(_letter_answer())])
    settings = _settings(dbname, tmp_path)
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
