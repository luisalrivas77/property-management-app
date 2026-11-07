"""The scrub_proposals periodic task (D19, D41) against the real database, as hausbuch_app
with RLS on: which proposals are scrubbed, in every workspace, and that the eval record (the
proposal.applied event) survives."""

import io
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from db.db_support import OWNER, make_engine
from pypdf import PdfWriter
from sqlalchemy import insert, null, select

from hausbuch.auth.provisioning import provision_workspace
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.jobs import scrub, tasks
from hausbuch.jobs.document_read import JobDeps
from hausbuch.schemas.enums import ActorType, DocumentSource, ExtractionKind
from hausbuch.schemas.events import AppliedRecord, EventType, ProposalApplied
from hausbuch.schemas.proposal import FieldDecision
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import upload_document
from hausbuch.services.events import append_event

pytestmark = pytest.mark.db

NOW = datetime(2027, 3, 1, 3, 15, tzinfo=UTC)
USER = Actor(ActorType.USER, "user:test")
FIELDS = [{"key": "property", "fields": {"purchase_price": {"value": "325000.00"}}}]
DECISIONS = {"property": {"purchase_price": "accepted"}}


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
            new_command(conn, ws, USER, now=NOW - timedelta(days=200)),
            store,
            _pdf(),
            media_type="application/pdf",
            source=DocumentSource.UPLOAD,
        ).id


def _proposal(engine, ws, doc, status: str, age_days: float, **extra) -> uuid.UUID:
    """A proposal in `status` whose deciding (or last update) was `age_days` before NOW."""
    at = NOW - timedelta(days=age_days)
    values = {
        "workspace_id": ws,
        "document_id": doc,
        "kind": "purchase_contract",
        "status": status,
        "fields": FIELDS,
        "created_at": at - timedelta(days=1),
        "updated_at": at,
    }
    if status == "applied":
        values |= {"decided_at": at, "decisions": DECISIONS, "applied_entities": []}
    elif status == "rejected":
        values |= {"decided_at": at}
    elif status in ("failed", "blocked_tenant_data"):
        values |= {"error_code": "ai.unparsable"}
    values |= extra
    with workspace_transaction(engine, ws) as conn:
        return conn.execute(
            insert(proposal_table).values(**values).returning(proposal_table.c.id)
        ).scalar_one()


def _row(engine, ws, proposal_id):
    with workspace_transaction(engine, ws) as conn:
        return conn.execute(select(proposal_table).where(proposal_table.c.id == proposal_id)).one()


def _scrubbed(row) -> bool:
    return row.fields is None and row.decisions is None and row.scrubbed_at == NOW


@pytest.fixture
def docs(app_engine, fresh_workspaces, blob_store):
    a, b = fresh_workspaces
    return {a: _document(app_engine, a, blob_store), b: _document(app_engine, b, blob_store)}


def test_old_final_proposals_are_scrubbed_and_recent_or_pending_ones_kept(app_engine, docs):
    ws, doc = next(iter(docs.items()))
    scrubbed = {
        status: _proposal(app_engine, ws, doc, status, 91)
        for status in ("applied", "rejected", "superseded", "failed")
    }
    kept = {
        "applied at 89 days": _proposal(app_engine, ws, doc, "applied", 89),
        "rejected at 89 days": _proposal(app_engine, ws, doc, "rejected", 89),
        "ready": _proposal(app_engine, ws, doc, "ready", 400),
        "queued": _proposal(app_engine, ws, doc, "queued", 400),
        "running": _proposal(app_engine, ws, doc, "running", 400),
        # Still decidable, so not final for the scrub (D41).
        "blocked_tenant_data": _proposal(app_engine, ws, doc, "blocked_tenant_data", 400),
    }
    # The age is decided_at's, not updated_at's, when the proposal was decided.
    kept["decided recently, updated long ago"] = _proposal(
        app_engine,
        ws,
        doc,
        "applied",
        30,
        updated_at=NOW - timedelta(days=300),
    )

    assert scrub.scrub_workspace(app_engine, ws, now=NOW) == len(scrubbed)

    for status, proposal_id in scrubbed.items():
        row = _row(app_engine, ws, proposal_id)
        assert _scrubbed(row), status
        assert row.status == status  # nothing else changes
    for label, proposal_id in kept.items():
        row = _row(app_engine, ws, proposal_id)
        assert row.scrubbed_at is None, label
        assert row.fields == FIELDS, label


def test_an_already_scrubbed_proposal_is_untouched(app_engine, docs):
    ws, doc = next(iter(docs.items()))
    earlier = NOW - timedelta(days=5)
    proposal_id = _proposal(
        app_engine,
        ws,
        doc,
        "applied",
        200,
        fields=null(),
        decisions=null(),
        scrubbed_at=earlier,
    )
    assert scrub.scrub_workspace(app_engine, ws, now=NOW) == 0
    assert _row(app_engine, ws, proposal_id).scrubbed_at == earlier


def test_each_workspace_is_scrubbed_in_its_own_workspace(app_engine, docs):
    (a, doc_a), (b, doc_b) = docs.items()
    in_a = _proposal(app_engine, a, doc_a, "applied", 120)
    in_b = _proposal(app_engine, b, doc_b, "rejected", 120)

    assert scrub.scrub_workspace(app_engine, a, now=NOW) == 1  # RLS: b's row is not seen
    assert _row(app_engine, b, in_b).scrubbed_at is None
    assert scrub.scrub_workspace(app_engine, b, now=NOW) == 1
    assert _scrubbed(_row(app_engine, a, in_a))
    assert _scrubbed(_row(app_engine, b, in_b))


def test_the_proposal_applied_event_keeps_its_decisions(app_engine, docs):
    ws, doc = next(iter(docs.items()))
    proposal_id = _proposal(app_engine, ws, doc, "applied", 100)
    payload = ProposalApplied(
        document_id=doc,
        kind=ExtractionKind.PURCHASE_CONTRACT,
        records=[
            AppliedRecord(
                key="property",
                mode="create",
                entity_type="property",
                entity_id=uuid.uuid4(),
                decisions={"purchase_price": FieldDecision.ACCEPTED},
            )
        ],
    )
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, USER, now=NOW - timedelta(days=100))
        append_event(ctx, EventType.PROPOSAL_APPLIED, proposal_id, payload)

    assert scrub.scrub_workspace(app_engine, ws, now=NOW) == 1

    with workspace_transaction(app_engine, ws) as conn:
        stored = conn.execute(
            select(event_table.c.payload).where(
                event_table.c.entity_id == proposal_id,
                event_table.c.type == EventType.PROPOSAL_APPLIED.value,
            )
        ).scalar_one()
    assert stored["records"][0]["decisions"] == {"purchase_price": "accepted"}
    assert _scrubbed(_row(app_engine, ws, proposal_id))


@pytest.fixture
def provisioned(dbname):
    """Two workspaces provisioned the production way (bootstrap's path), so they carry an
    invite and provisioned_workspace_ids lists them."""
    owner = make_engine(dbname, OWNER)
    try:
        made = [
            provision_workspace(
                owner,
                name=f"Scrub {n}",
                email=f"scrub-{uuid.uuid4().hex[:8]}@example.org",
                display_name="Test",
                now=NOW - timedelta(days=300),
            ).workspace_id
            for n in range(2)
        ]
    finally:
        owner.dispose()
    return made


def test_the_task_scrubs_every_provisioned_workspace(app_engine, blob_store, provisioned, caplog):
    proposals = {}
    for ws in provisioned:
        doc = _document(app_engine, ws, blob_store)
        proposals[ws] = (
            _proposal(app_engine, ws, doc, "applied", 95),
            _proposal(app_engine, ws, doc, "applied", 10),
        )
    deps = JobDeps(
        engine=app_engine,
        settings=None,  # type: ignore[arg-type]  # the scrub reads no setting
        blob_store=blob_store,
        model_client=None,
        clock=lambda: NOW,
    )
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.scrub"):
        total = scrub.run_scrub_proposals(deps)

    assert total >= 2  # other tests' provisioned workspaces may hold old proposals too
    for ws, (old, recent) in proposals.items():
        assert _scrubbed(_row(app_engine, ws, old))
        assert _row(app_engine, ws, recent).scrubbed_at is None
    (line,) = [r for r in caplog.records if r.name == "hausbuch.jobs.scrub"]
    assert line.fields["scrubbed"] == total
    assert line.fields["failed_workspaces"] == 0
    assert "325000" not in line.getMessage() + str(line.fields)


def test_scrub_is_registered_as_a_daily_periodic_task():
    import procrastinate

    connector = procrastinate.SyncPsycopgConnector()  # never opened
    app = tasks.register_tasks(procrastinate.App(connector=connector))
    registered = app.periodic_registry.periodic_tasks.items()
    (periodic,) = [p for (name, _), p in registered if name == "scrub_proposals"]
    assert periodic.cron == scrub.SCRUB_CRON
    assert periodic.task.name == scrub.SCRUB_TASK
