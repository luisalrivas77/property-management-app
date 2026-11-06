"""services/proposals.py against Postgres (phase 2 spec §6.1, §6.5, D14, D16).

The enqueue seam (jobs.enqueue.enqueue_document_read) is the worker chunk's; here it is
monkeypatched and its calls asserted. ai_call rows are inserted directly (the AI client is
the worker chunk's too).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY, _admin
from pydantic import ValidationError
from sqlalchemy import insert, select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import (
    AI_BLOCKED_TENANT_DATA,
    AI_UNPARSABLE,
    PROPOSAL_ALREADY_DECIDED,
    PROPOSAL_NOT_READY,
)
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.enums import (
    AcquisitionCostKind,
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    LinkTarget,
    PropertyKind,
    ProposalStatus,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import proposals
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import get_document, list_links, upload_document
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.events import list_events
from hausbuch.services.loans import list_loans
from hausbuch.services.properties import (
    create_property,
    list_acquisition_costs,
    list_price_splits,
    list_properties,
)
from hausbuch.services.units import list_units

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
JOB = Actor(ActorType.AI, "claude-test-model")
FLAT = PropertyCreate(
    label="Wohnung Lindenstraße",
    kind=PropertyKind.ETW,
    address=Address(street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"),
    build_year=1994,
    notarized_on=date(2024, 3, 15),
    purchase_date=date(2024, 5, 2),
    purchase_price=Decimal("289000.00"),
)


@pytest.fixture
def enqueued(monkeypatch):
    calls: list[dict] = []

    def fake(conn, *, workspace_id, document_id, proposal_id):
        calls.append(
            {"workspace_id": workspace_id, "document_id": document_id, "proposal_id": proposal_id}
        )

    monkeypatch.setattr(enqueue, "enqueue_document_read", fake)
    return calls


@pytest.fixture
def job(app_engine):
    """job(ws, command, *args, **kwargs): a command as the document_read job's AI actor."""

    def _job(ws, command, *args, **kwargs):
        with workspace_transaction(app_engine, ws) as conn:
            return command(new_command(conn, ws, JOB), *args, **kwargs)

    return _job


def _upload(run, ws, store, data: bytes = b"%PDF-1.7 kaufvertrag"):
    return run(
        ws,
        upload_document,
        store,
        data,
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename="kaufvertrag.pdf",
    )


def _ai_call(ctx, document_id, job_name):
    return ctx.conn.execute(
        insert(ai_call_table)
        .values(
            workspace_id=ctx.workspace_id,
            job=job_name,
            document_id=document_id,
            model="claude-test-model",
            prompt_version="test-1",
            input_sha256=b"\x01" * 32,
        )
        .returning(ai_call_table.c.id)
    ).scalar_one()


def _v(value, page=1, quote="…") -> ExtractedValue:
    return ExtractedValue(
        value=value, source=SourceRef(page=page, quote=quote), checks=["cited"], confidence="high"
    )


PURCHASE = ClassifyOutput(
    type=DocumentType.CONTRACT,
    subtype=DocumentSubtype.PURCHASE_CONTRACT,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
PURCHASE_VALUES = {
    "title": _v("Kaufvertrag Lindenstraße 12"),
    "document_date": _v("2024-03-15"),
    "property_kind": _v("Wohnungseigentum"),
    "street": _v("Lindenstraße"),
    "house_number": _v("12"),
    "postal_code": _v("10115"),
    "city": _v("Berlin"),
    "build_year": _v(1994),
    "notarized_on": _v("2024-03-15"),
    "purchase_date": _v("2024-05-02"),
    "purchase_price": _v("289000.00", page=4),
    "land_value": _v("57800.00", page=4),
    "unit_label": _v("WE 3"),
    "mea": _v({"numerator": 85, "denominator": 1000}),
    "floor_area_m2": _v("68.40"),
}
INVOICE = ClassifyOutput(
    type=DocumentType.INVOICE,
    subtype=DocumentSubtype.COST_BASIS_EVIDENCE,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
INVOICE_VALUES = {
    "title": _v("Notarrechnung"),
    "document_date": _v("2024-03-20"),
    "sender": _v("Notar Dr. Beispiel"),
    "amount_total": _v("4321.10"),
    "reference": _v("UR 123/2024"),
}
LOAN = ClassifyOutput(
    type=DocumentType.CONTRACT,
    subtype=DocumentSubtype.LOAN_CONTRACT,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
LOAN_VALUES = {
    "lender": _v("Sparkasse Berlin"),
    "contract_number": _v("4711 0815"),
    "principal": _v("200000.00"),
    "nominal_rate": _v("0.0365"),
    "monthly_payment": _v("941.67"),
    "fixed_until": _v("2034-04-30"),
    "disbursed_on": _v("2024-04-25"),
    "first_payment_on": _v("2024-05-30"),
    "signed_on": _v("2024-03-01"),
}


def _read_ready(run, job, ws, store, enqueued, classification, values, data):
    """Upload, request, and let the 'job' report a read: a ready proposal."""
    doc = _upload(run, ws, store, data)
    proposal = run(ws, proposals.request_read, doc.id)
    classify_id = run(ws, _ai_call, doc.id, "classify")
    extract_id = run(ws, _ai_call, doc.id, "extract")
    ready = job(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=classification,
        classify_call_id=classify_id,
        values=values,
        extract_call_id=extract_id,
    )
    return doc, ready, extract_id


def _accept_all(proposal, **overrides) -> ProposalApplyBody:
    """Every record with its proposed values, as the review screen sends them unchanged."""
    records = []
    for r in proposal.records:
        values = {f: v.value for f, v in r.fields.items() if v.value is not None}
        values |= overrides.get(r.key, {})
        records.append(ProposalApplyRecord(key=r.key, values=values))
    return ProposalApplyBody(records=records)


def _types(events):
    return [e.type for e in events]


# --- Creating: upload / request / re-run -------------------------------------------------


def test_request_read_queues_a_proposal_and_enqueues_its_job(
    run, read, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store)
    proposal = run(ws, proposals.request_read, doc.id)

    assert proposal.status is ProposalStatus.QUEUED
    assert proposal.kind is None and proposal.records == []
    assert enqueued == [{"workspace_id": ws, "document_id": doc.id, "proposal_id": proposal.id}]
    events = read(ws, list_events, entity_id=proposal.id)
    assert _types(events) == [EventType.PROPOSAL_CREATED]
    assert events[0].payload["document_id"] == str(doc.id)


def test_enqueue_failure_rolls_the_proposal_back(
    run, read, fresh_workspaces, blob_store, monkeypatch
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store)

    def broken(conn, **_):
        raise RuntimeError("queue down")

    monkeypatch.setattr(enqueue, "enqueue_document_read", broken)
    with pytest.raises(RuntimeError):
        run(ws, proposals.request_read, doc.id)
    assert read(ws, proposals.list_document_proposals, doc.id) == []


def test_tenant_data_is_blocked_before_any_job_unless_the_workspace_allows_it(
    run, dbname, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store)
    blocked = run(ws, proposals.request_read, doc.id, contains_tenant_data=True)
    assert blocked.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert blocked.error_code == AI_BLOCKED_TENANT_DATA
    assert enqueued == []

    with _admin(dbname) as conn:
        conn.execute("UPDATE workspace SET ai_tenant_documents = true WHERE id = %s", (ws,))
    allowed = run(ws, proposals.request_read, doc.id, contains_tenant_data=True)
    assert allowed.status is ProposalStatus.QUEUED
    assert [c["proposal_id"] for c in enqueued] == [allowed.id]


def test_a_duplicate_upload_returns_the_latest_proposal_instead_of_reading_again(
    run, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store)
    first = run(ws, proposals.proposal_for_upload, doc.id, created=True, contains_tenant_data=False)
    again = run(
        ws, proposals.proposal_for_upload, doc.id, created=False, contains_tenant_data=False
    )
    assert again.id == first.id
    assert len(enqueued) == 1


def test_rerun_supersedes_the_pending_proposal(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, PURCHASE, PURCHASE_VALUES, b"%PDF rerun"
    )
    fresh = run(ws, proposals.rerun_proposal, doc.id)

    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.SUPERSEDED
    assert fresh.status is ProposalStatus.QUEUED
    assert [p.id for p in read(ws, proposals.list_document_proposals, doc.id)] == [
        fresh.id,
        ready.id,
    ]
    superseded = read(ws, list_events, entity_id=ready.id)
    assert _types(superseded)[-1] == EventType.PROPOSAL_SUPERSEDED
    assert "property" in superseded[-1].payload["record_keys"]
    # The job of the superseded proposal reports late: nothing changes.
    late = job(
        ws, proposals.record_read_failure, ready.id, error_code=AI_UNPARSABLE, ai_call_id=None
    )
    assert late.status is ProposalStatus.SUPERSEDED


# --- The job's results -------------------------------------------------------------------


def test_read_result_stores_the_ai_extraction_and_a_ready_proposal(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready, extract_id = _read_ready(
        run, job, ws, blob_store, enqueued, PURCHASE, PURCHASE_VALUES, b"%PDF read"
    )

    assert ready.status is ProposalStatus.READY
    assert ready.kind == "purchase_contract"
    assert ready.ai_call_id == extract_id
    keys = [r.key for r in ready.records]
    assert keys[0] == "typing"
    assert {"property", "unit", "price_split", "link:property"} <= set(keys)

    def extraction(conn):
        return conn.execute(
            select(extraction_table).where(extraction_table.c.document_id == doc.id)
        ).one()

    row = read(ws, extraction)
    assert (row.source, row.kind, row.ai_call_id, row.id) == (
        "ai",
        "purchase_contract",
        extract_id,
        ready.extraction_id,
    )
    # D14: the document is not typed and nothing exists until the user applies.
    assert read(ws, get_document, doc.id).typing is None
    assert read(ws, list_properties) == []
    types = _types(read(ws, list_events, entity_id=doc.id))
    assert types[-2:] == [EventType.DOCUMENT_CLASSIFIED, EventType.DOCUMENT_EXTRACTED]
    extracted = read(ws, list_events, entity_id=doc.id)[-1]
    assert extracted.payload["field_names"] == sorted(PURCHASE_VALUES)
    assert extracted.actor_type is ActorType.AI


def test_blocked_after_classification_is_a_typing_only_proposal(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store, b"%PDF lease")
    proposal = run(ws, proposals.request_read, doc.id)
    classify_id = run(ws, _ai_call, doc.id, "classify")
    lease = PURCHASE.model_copy(
        update={"subtype": DocumentSubtype.LEASE, "contains_tenant_data": True}
    )
    blocked = job(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=lease,
        classify_call_id=classify_id,
        values=None,
        extract_call_id=None,
        blocked_tenant_data=True,
    )
    assert blocked.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert blocked.error_code == AI_BLOCKED_TENANT_DATA
    assert [r.key for r in blocked.records] == ["typing"]
    assert blocked.extraction_id is None and blocked.ai_call_id == classify_id
    assert _types(read(ws, list_events, entity_id=doc.id))[-1] == EventType.DOCUMENT_CLASSIFIED


def test_read_failure_marks_failed_with_the_code(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store)
    proposal = run(ws, proposals.request_read, doc.id)
    failed = job(
        ws,
        proposals.record_read_failure,
        proposal.id,
        error_code=AI_UNPARSABLE,
        ai_call_id=None,
        attempts=2,
    )
    assert (failed.status, failed.error_code) == (ProposalStatus.FAILED, AI_UNPARSABLE)
    event = read(ws, list_events, entity_id=doc.id)[-1]
    assert event.type == EventType.DOCUMENT_READ_FAILED
    assert event.payload == {"error_code": AI_UNPARSABLE, "attempts": 2}
    with pytest.raises(RuleViolationError) as refused:
        run(ws, proposals.reject_proposal, proposal.id)
    assert refused.value.code == PROPOSAL_NOT_READY


# --- Apply -------------------------------------------------------------------------------


def test_apply_creates_through_the_services_in_one_command(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, PURCHASE, PURCHASE_VALUES, b"%PDF apply"
    )
    body = _accept_all(ready, property={"build_year": 1995})
    result = run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    applied = {a.key: a for a in result.applied}
    [prop] = read(ws, list_properties)
    assert applied["property"].entity_id == prop.id
    assert (prop.build_year, prop.label) == (1995, "Lindenstraße 12")
    [unit] = read(ws, list_units, prop.id)
    assert applied["unit"].entity_id == unit.id and unit.label == "WE 3"
    [split] = read(ws, list_price_splits, prop.id)
    assert split.building_share == Decimal("0.8") and split.evidence_document_id == doc.id
    typed = read(ws, get_document, doc.id)
    assert typed.typing.subtype is DocumentSubtype.PURCHASE_CONTRACT
    assert [(lk.target_type, lk.target_id) for lk in read(ws, list_links, doc.id)] == [
        (LinkTarget.PROPERTY, prop.id)
    ]

    assert result.proposal.status is ProposalStatus.APPLIED
    assert result.proposal.decided_at is not None
    events = read(ws, list_events)
    apply_events = [e for e in events if e.correlation_id == events[-1].correlation_id]
    assert EventType.PROPERTY_CREATED in _types(apply_events)
    assert _types(apply_events)[-1] == EventType.PROPOSAL_APPLIED
    payload = apply_events[-1].payload
    by_key = {r["key"]: r for r in payload["records"]}
    assert by_key["property"]["decisions"]["build_year"] == "edited"
    assert by_key["property"]["decisions"]["purchase_price"] == "accepted"
    assert "289000" not in str(payload)  # field names and decisions, never values

    with pytest.raises(RuleViolationError) as twice:
        run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)
    assert twice.value.code == PROPOSAL_ALREADY_DECIDED


def test_a_failed_apply_writes_nothing_and_leaves_the_proposal_ready(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, PURCHASE, PURCHASE_VALUES, b"%PDF fail"
    )
    # The unit is fine, the property fails validation: the error names the record key.
    body = _accept_all(ready, property={"address.postal_code": "1011"})
    with pytest.raises(ValidationError) as invalid:
        run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)
    locs = [e["loc"] for e in invalid.value.errors()]
    assert locs == [("property", "address", "postal_code")]

    # A failure late in the order (a skipped property its unit needs) rolls back the rest.
    skip = ProposalApplyBody(records=[r for r in _accept_all(ready).records if r.key != "property"])
    with pytest.raises(ValueError, match="needs the property"):
        run(ws, proposals.apply_proposal, ready.id, skip, iban_key=TEST_IBAN_KEY)

    assert read(ws, list_properties) == []
    assert read(ws, get_document, doc.id).typing is None
    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.READY


def test_apply_skips_records_and_records_the_skip(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, PURCHASE, PURCHASE_VALUES, b"%PDF skip"
    )
    records = [
        r.model_copy(update={"skip": r.key == "price_split"}) for r in _accept_all(ready).records
    ]
    result = run(
        ws,
        proposals.apply_proposal,
        ready.id,
        ProposalApplyBody(records=records),
        iban_key=TEST_IBAN_KEY,
    )
    skipped = {a.key for a in result.applied if a.skipped}
    assert skipped == {"price_split"}
    [prop] = read(ws, list_properties)
    assert read(ws, list_price_splits, prop.id) == []


def test_a_cost_invoice_creates_the_cost_with_this_document_as_beleg(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT)
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, INVOICE, INVOICE_VALUES, b"%PDF notar"
    )
    assert {r.key for r in ready.records} == {"typing", "cost:notary", "link:property"}
    run(ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)

    [cost] = read(ws, list_acquisition_costs, prop.id)
    assert (cost.kind, cost.amount, cost.document_id, cost.note) == (
        AcquisitionCostKind.NOTARY,
        Decimal("4321.10"),
        doc.id,
        "UR 123/2024",
    )
    assert [lk.target_id for lk in read(ws, list_links, doc.id)] == [prop.id]


def test_a_loan_contract_creates_the_loan_on_the_only_property(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT)
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, LOAN, LOAN_VALUES, b"%PDF loan")
    body = _accept_all(ready, loan={"signed_on": "2024-03-01"})
    result = run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    [loan] = read(ws, list_loans, prop.id)
    assert {a.key: a.entity_id for a in result.applied}["loan"] == loan.id
    assert loan.terms.principal == Decimal("200000.00")
    assert [(lk.target_type, lk.target_id) for lk in read(ws, list_links, doc.id)] == [
        (LinkTarget.LOAN, loan.id)
    ]


def test_reject_records_the_decision_without_domain_writes(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, PURCHASE, PURCHASE_VALUES, b"%PDF reject"
    )
    rejected = run(ws, proposals.reject_proposal, ready.id)
    assert rejected.status is ProposalStatus.REJECTED and rejected.decided_at is not None
    assert _types(read(ws, list_events, entity_id=ready.id))[-1] == EventType.PROPOSAL_REJECTED
    assert read(ws, list_properties) == []
    with pytest.raises(RuleViolationError) as again:
        run(ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)
    assert again.value.code == PROPOSAL_ALREADY_DECIDED


def test_another_workspace_cannot_see_or_decide_a_proposal(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    a, b = fresh_workspaces
    _, ready, _ = _read_ready(
        run, job, a, blob_store, enqueued, PURCHASE, PURCHASE_VALUES, b"%PDF rls"
    )
    assert read(b, proposals.get_proposal, ready.id) is None
    with pytest.raises(NotFoundError):
        run(b, proposals.reject_proposal, ready.id)
    with pytest.raises(NotFoundError):
        run(b, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)
    with pytest.raises(NotFoundError):
        job(
            b,
            proposals.record_read_failure,
            ready.id,
            error_code=AI_UNPARSABLE,
            ai_call_id=None,
        )
    assert read(a, proposals.get_proposal, ready.id).status is ProposalStatus.READY


def test_request_read_for_another_workspaces_document_is_not_found(
    run, fresh_workspaces, blob_store, enqueued
):
    a, b = fresh_workspaces
    doc = _upload(run, a, blob_store)
    with pytest.raises(NotFoundError):
        run(b, proposals.request_read, doc.id)
    with pytest.raises(NotFoundError):
        run(b, proposals.rerun_proposal, uuid.uuid4())
    assert enqueued == []


def test_a_duplicate_upload_after_a_failure_reads_again(
    run, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store)
    first = run(ws, proposals.proposal_for_upload, doc.id, created=True, contains_tenant_data=False)
    job(ws, proposals.record_read_failure, first.id, error_code=AI_UNPARSABLE, ai_call_id=None)
    again = run(
        ws, proposals.proposal_for_upload, doc.id, created=False, contains_tenant_data=False
    )
    assert again.id != first.id and again.status is ProposalStatus.QUEUED
    assert [c["proposal_id"] for c in enqueued] == [first.id, again.id]


def test_the_service_repeats_the_tenant_gate_on_the_classifier_result(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """A job that forgot the gate still cannot store a tenant document's read (D16)."""
    ws, _ = fresh_workspaces
    doc, stored, _ = _read_ready(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        PURCHASE.model_copy(update={"contains_tenant_data": True}),
        PURCHASE_VALUES,
        b"%PDF gate",
    )
    assert stored.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert [r.key for r in stored.records] == ["typing"]
    assert stored.extraction_id is None

    def extractions(conn):
        return conn.execute(
            select(extraction_table).where(extraction_table.c.document_id == doc.id)
        ).all()

    assert read(ws, extractions) == []
