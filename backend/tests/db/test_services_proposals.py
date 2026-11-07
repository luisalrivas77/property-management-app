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
from hausbuch.db.tables import proposal as proposal_table
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
    DepositForm,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    LinkTarget,
    PropertyKind,
    ProposalStatus,
    RentModel,
    TenancyRole,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.schemas.tenancy import TenancyCreate, TenancyParty, TenantCreate
from hausbuch.services import proposals
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import get_document, list_links, upload_document
from hausbuch.services.errors import ConflictError, NotFoundError, RuleViolationError
from hausbuch.services.events import list_events
from hausbuch.services.loans import list_loans
from hausbuch.services.properties import (
    create_property,
    list_acquisition_costs,
    list_price_splits,
    list_properties,
)
from hausbuch.services.tenancies import create_tenancy, deposit_receipts
from hausbuch.services.tenants import create_tenant, erase_tenant
from hausbuch.services.units import create_unit, list_units
from hausbuch.services.workspace_settings import update_settings

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


# --- The uploader's tenant-data flag on the row (D16 gate 2) -----------------------------


def test_the_upload_flag_is_stored_and_a_rerun_keeps_it(
    run, read, dbname, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    plain = _upload(run, ws, blob_store, b"%PDF plain")
    assert run(ws, proposals.request_read, plain.id).contains_tenant_data is False

    doc = _upload(run, ws, blob_store, b"%PDF flagged")
    blocked = run(ws, proposals.request_read, doc.id, contains_tenant_data=True)
    assert read(ws, proposals.get_proposal, blocked.id).contains_tenant_data is True

    with _admin(dbname) as conn:
        conn.execute("UPDATE workspace SET ai_tenant_documents = true WHERE id = %s", (ws,))
    # A re-run copies the flag from the latest proposal; an explicit false does not clear it.
    again = run(ws, proposals.rerun_proposal, doc.id, contains_tenant_data=False)
    assert again.status is ProposalStatus.QUEUED and again.contains_tenant_data is True
    # An explicit true adds it to a document that did not have it.
    flagged = run(ws, proposals.rerun_proposal, plain.id, contains_tenant_data=True)
    assert flagged.contains_tenant_data is True


def test_a_blocked_document_is_read_again_once_the_setting_is_on(
    run, read, fresh_workspaces, blob_store, enqueued
):
    """D42 (2): blocked while ai_tenant_documents was off; after switching it on, a re-run
    supersedes the blocked proposal (with its event) and queues one read, still flagged."""
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store, b"%PDF blocked then allowed")
    blocked = run(ws, proposals.request_read, doc.id, contains_tenant_data=True)
    assert blocked.status is ProposalStatus.BLOCKED_TENANT_DATA and enqueued == []

    run(ws, update_settings, ai_tenant_documents=True)
    fresh = run(ws, proposals.rerun_proposal, doc.id)

    old = read(ws, proposals.get_proposal, blocked.id)
    assert old.status is ProposalStatus.SUPERSEDED and old.error_code is None
    assert _types(read(ws, list_events, entity_id=blocked.id))[-1] == (
        EventType.PROPOSAL_SUPERSEDED
    )
    undecided = [
        p
        for p in read(ws, proposals.list_document_proposals, doc.id)
        if p.status
        in (
            ProposalStatus.QUEUED,
            ProposalStatus.RUNNING,
            ProposalStatus.READY,
            ProposalStatus.BLOCKED_TENANT_DATA,
        )
    ]
    assert [p.id for p in undecided] == [fresh.id]
    assert fresh.status is ProposalStatus.QUEUED and fresh.error_code is None
    assert fresh.contains_tenant_data is True
    assert [c["proposal_id"] for c in enqueued] == [fresh.id]


def test_a_rerun_with_the_setting_still_off_blocks_again_without_a_second_blocked_item(
    run, read, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store, b"%PDF blocked twice")
    first = run(ws, proposals.request_read, doc.id, contains_tenant_data=True)
    again = run(ws, proposals.rerun_proposal, doc.id)

    assert again.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert read(ws, proposals.get_proposal, first.id).status is ProposalStatus.SUPERSEDED
    blocked = [
        p
        for p in read(ws, proposals.list_document_proposals, doc.id)
        if p.status is ProposalStatus.BLOCKED_TENANT_DATA
    ]
    assert [p.id for p in blocked] == [again.id]
    assert enqueued == []


_UNDECIDED = (
    ProposalStatus.QUEUED,
    ProposalStatus.RUNNING,
    ProposalStatus.READY,
    ProposalStatus.BLOCKED_TENANT_DATA,
)


def _undecided(read, ws, document_id):
    found = read(ws, proposals.list_document_proposals, document_id)
    return [p for p in found if p.status in _UNDECIDED]


@pytest.mark.parametrize("switch_on", [False, True], ids=["switch-off", "switch-on"])
def test_a_reupload_of_a_blocked_document_leaves_one_undecided_proposal(
    run, read, fresh_workspaces, blob_store, enqueued, switch_on
):
    """Off: the blocked proposal is the answer, as is (a re-run would only block again).
    On: it is superseded with its event and a read is queued, the tenant flag kept."""
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store, b"%PDF blocked re-upload")
    blocked = run(
        ws, proposals.proposal_for_upload, doc.id, created=True, contains_tenant_data=True
    )
    assert blocked.status is ProposalStatus.BLOCKED_TENANT_DATA
    if switch_on:
        run(ws, update_settings, ai_tenant_documents=True)

    again = run(
        ws, proposals.proposal_for_upload, doc.id, created=False, contains_tenant_data=False
    )

    assert [p.id for p in _undecided(read, ws, doc.id)] == [again.id]
    if not switch_on:
        assert again.id == blocked.id and again.status is ProposalStatus.BLOCKED_TENANT_DATA
        assert enqueued == []
        return
    assert again.id != blocked.id
    assert again.status is ProposalStatus.QUEUED and again.contains_tenant_data is True
    assert [c["proposal_id"] for c in enqueued] == [again.id]
    old = read(ws, proposals.get_proposal, blocked.id)
    assert old.status is ProposalStatus.SUPERSEDED and old.error_code is None
    assert _types(read(ws, list_events, entity_id=blocked.id))[-1] == (
        EventType.PROPOSAL_SUPERSEDED
    )


@pytest.mark.parametrize("switch_on", [False, True], ids=["switch-off", "switch-on"])
def test_a_classifier_tenant_result_marks_the_proposal_like_the_uploader(
    run, read, job, fresh_workspaces, blob_store, enqueued, switch_on
):
    """§5.5 step 3: the classifier's tenant result sets contains_tenant_data on the row
    (blocked with the switch off, read with it on), so the mark is sticky."""
    ws, _ = fresh_workspaces
    if switch_on:
        run(ws, update_settings, ai_tenant_documents=True)
    doc = _upload(run, ws, blob_store, b"%PDF classifier lease")
    proposal = run(ws, proposals.request_read, doc.id)
    assert proposal.contains_tenant_data is False
    classify_id = run(ws, _ai_call, doc.id, "classify")
    lease = PURCHASE.model_copy(update={"subtype": DocumentSubtype.LEASE})
    stored = job(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=lease,
        classify_call_id=classify_id,
        values=None,
        extract_call_id=None,
        blocked_tenant_data=not switch_on,
    )
    assert stored.contains_tenant_data is True
    expected = ProposalStatus.READY if switch_on else ProposalStatus.BLOCKED_TENANT_DATA
    assert stored.status is expected


def test_a_classifier_blocked_document_is_blocked_at_creation_on_a_rerun(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """Classifier-blocked, switch still off: a re-run is blocked by request_read, so no job
    is enqueued and no classify call is made; the blocked proposal is superseded."""
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store, b"%PDF classifier blocked rerun")
    proposal = run(ws, proposals.request_read, doc.id)
    assert [c["proposal_id"] for c in enqueued] == [proposal.id]
    classify_id = run(ws, _ai_call, doc.id, "classify")
    tenant_letter = PURCHASE.model_copy(
        update={"type": DocumentType.LETTER, "subtype": None, "contains_tenant_data": True}
    )
    job(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=tenant_letter,
        classify_call_id=classify_id,
        values=None,
        extract_call_id=None,
        blocked_tenant_data=True,
    )

    again = run(ws, proposals.rerun_proposal, doc.id)

    assert again.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert again.contains_tenant_data is True
    assert [c["proposal_id"] for c in enqueued] == [proposal.id]  # nothing new enqueued
    assert [p.id for p in _undecided(read, ws, doc.id)] == [again.id]


def test_a_reupload_that_says_tenant_data_rereads_the_pending_proposal_with_the_flag(
    run, read, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store)
    first = run(ws, proposals.proposal_for_upload, doc.id, created=True, contains_tenant_data=False)
    again = run(ws, proposals.proposal_for_upload, doc.id, created=False, contains_tenant_data=True)
    assert again.id != first.id
    assert again.contains_tenant_data is True
    assert again.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert read(ws, proposals.get_proposal, first.id).status is ProposalStatus.SUPERSEDED
    assert [c["proposal_id"] for c in enqueued] == [first.id]


def test_the_stored_flag_blocks_the_read_when_the_setting_was_turned_off(
    run, read, job, dbname, fresh_workspaces, blob_store, enqueued
):
    """Queued while the workspace allowed tenant documents, read after it stopped: the
    service still blocks on the row's flag though the classifier saw no tenant data."""
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store, b"%PDF turned off")
    with _admin(dbname) as conn:
        conn.execute("UPDATE workspace SET ai_tenant_documents = true WHERE id = %s", (ws,))
    queued = run(ws, proposals.request_read, doc.id, contains_tenant_data=True)
    with _admin(dbname) as conn:
        conn.execute("UPDATE workspace SET ai_tenant_documents = false WHERE id = %s", (ws,))
    stored = job(
        ws,
        proposals.record_read_result,
        queued.id,
        classification=PURCHASE,
        classify_call_id=run(ws, _ai_call, doc.id, "classify"),
        values=PURCHASE_VALUES,
        extract_call_id=run(ws, _ai_call, doc.id, "extract"),
    )
    assert stored.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert stored.extraction_id is None


def test_a_reupload_without_the_flag_after_a_block_stays_blocked(
    run, read, fresh_workspaces, blob_store, enqueued
):
    """Once said, a document stays a tenant document (D16): the same bytes uploaded again
    without the flag after the first proposal was blocked, with the setting still off, get
    the blocked proposal back (never two undecided ones, D42), still flagged, and nothing is
    enqueued. (Until step 2 this created a second blocked proposal.)"""
    ws, _ = fresh_workspaces
    doc = _upload(run, ws, blob_store, b"%PDF sticky flag")
    first = run(ws, proposals.proposal_for_upload, doc.id, created=True, contains_tenant_data=True)
    assert first.status is ProposalStatus.BLOCKED_TENANT_DATA
    again = run(
        ws, proposals.proposal_for_upload, doc.id, created=False, contains_tenant_data=False
    )
    assert again.id == first.id
    assert again.status is ProposalStatus.BLOCKED_TENANT_DATA
    assert again.contains_tenant_data is True
    assert read(ws, proposals.get_proposal, again.id).contains_tenant_data is True
    assert enqueued == []


# --- Kautionsnachweis (D42 part 2) -----------------------------------------------------------

DEPOSIT = ClassifyOutput(
    type=DocumentType.LETTER,
    subtype=DocumentSubtype.DEPOSIT_RECORD,
    contains_tenant_data=True,
    language="de",
    confidence="high",
)
DEPOSIT_VALUES = {
    "title": _v("Kautionsnachweis"),
    "document_date": _v("2026-07-03"),
    "amount": _v("3450.00"),
    "received_on": _v("2026-07-02"),
}


def _let_flat(run, dbname, ws):
    """The flat let since 1 Jul 2026 with an agreed Kaution of 3,450 €, and tenant documents
    allowed for the workspace (the gate would block a Kautionsnachweis otherwise)."""
    with _admin(dbname) as conn:
        conn.execute("UPDATE workspace SET ai_tenant_documents = true WHERE id = %s", (ws,))
    prop = run(ws, create_property, FLAT)
    unit = run(
        ws,
        create_unit,
        UnitCreate(property_id=prop.id, label="WE 3", floor_area_m2=Decimal("68.40")),
    )
    tenant = run(ws, create_tenant, TenantCreate(full_name="Lena Beispiel"), iban_key=TEST_IBAN_KEY)
    lease = TenancyCreate(
        unit_id=unit.id,
        parties=[TenancyParty(tenant_id=tenant.id, role=TenancyRole.PRIMARY)],
        start_date=date(2026, 7, 1),
        rent_model=RentModel.FIXED,
        deposit_amount=Decimal("3450.00"),
        deposit_form=DepositForm.CASH_ACCOUNT,
    )
    return run(ws, create_tenancy, lease, initial_rent=None, iban_key=TEST_IBAN_KEY)


def test_a_deposit_record_applies_as_a_kaution_receipt_on_its_tenancy(
    run, read, job, dbname, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    tenancy = _let_flat(run, dbname, ws)
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, DEPOSIT, DEPOSIT_VALUES, b"%PDF kaution"
    )
    assert ready.status is ProposalStatus.READY
    records = {r.key: r for r in ready.records}
    assert set(records) == {"typing", "deposit_received", "link:tenancy"}
    assert records["deposit_received"].mode == "create"
    assert records["deposit_received"].fields["tenancy_id"].value == str(tenancy.id)

    result = run(ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)

    applied = {a.key: a for a in result.applied}
    assert applied["deposit_received"].entity_id == tenancy.id
    [receipt] = read(ws, deposit_receipts, tenancy.id)
    assert receipt.type is EventType.TENANCY_DEPOSIT_RECEIVED
    assert receipt.payload == {"amount": "3450.00"}
    assert receipt.effective_date == date(2026, 7, 2)
    assert (LinkTarget.TENANCY, tenancy.id) in [
        (lk.target_type, lk.target_id) for lk in read(ws, list_links, doc.id)
    ]

    # The same payment read from a second copy is already recorded: unchanged, and applying
    # it adds nothing, even when the record is sent instead of skipped (as the screen does).
    _, again, _ = _read_ready(
        run, job, ws, blob_store, enqueued, DEPOSIT, DEPOSIT_VALUES, b"%PDF kaution copy"
    )
    second = {r.key: r for r in again.records}["deposit_received"]
    assert (second.mode, second.target_id) == ("unchanged", tenancy.id)
    run(ws, proposals.apply_proposal, again.id, _accept_all(again), iban_key=TEST_IBAN_KEY)
    assert len(read(ws, deposit_receipts, tenancy.id)) == 1


# --- An apply that names an erased tenant (D42 (3), review F1) ------------------------------


@pytest.mark.parametrize("mode", ["unchanged", "patch"], ids=["unchanged", "empty-patch"])
def test_an_apply_keeping_an_erased_tenant_conflicts(
    run, read, fresh_workspaces, blob_store, enqueued, mode
):
    """A tenant record kept as it is (unchanged, or a patch with no field sent) still locks
    and checks the tenant row: after erase_tenant the apply is a ConflictError and the
    proposal stays ready, so no applied proposal can name the tenant outside the scrub."""
    ws, _ = fresh_workspaces
    tenant = run(
        ws, create_tenant, TenantCreate(full_name="Bewerberin Beispiel"), iban_key=TEST_IBAN_KEY
    )
    doc = _upload(run, ws, blob_store, b"%PDF tenant letter " + mode.encode())
    fields = {"full_name": _v("Bewerberin Beispiel")} if mode == "patch" else {}
    record = {
        "key": "tenant:1",
        "entity_type": "tenant",
        "mode": mode,
        "target_id": str(tenant.id),
        "fields": {k: v.model_dump(mode="json") for k, v in fields.items()},
    }

    def ready_proposal(ctx):
        return ctx.conn.execute(
            insert(proposal_table)
            .values(
                workspace_id=ctx.workspace_id,
                document_id=doc.id,
                status="ready",
                fields=[record],
            )
            .returning(proposal_table.c.id)
        ).scalar_one()

    proposal_id = run(ws, ready_proposal)
    run(ws, erase_tenant, tenant.id)  # an applicant: no tenancy, no retention hold

    body = ProposalApplyBody(records=[ProposalApplyRecord(key="tenant:1", values={})])
    with pytest.raises(ConflictError):
        run(ws, proposals.apply_proposal, proposal_id, body, iban_key=TEST_IBAN_KEY)
    assert read(ws, proposals.get_proposal, proposal_id).status is ProposalStatus.READY
