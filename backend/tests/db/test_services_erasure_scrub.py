"""The erasure scrub inside erase_tenant (docs/phases/phase-2.md D18, D19, D32, D42 (3)),
against Postgres as hausbuch_app under RLS.

One landlord world: Lena's tenancy and tenant record come from an applied Mietvertrag
proposal; Kautionsnachweise linked only to her tenancy (one proposal rejected, one ready); a
letter linked to her as a tenant with a read still queued; one whose read the privacy gate
blocked; ai_call rows with output on all of them and a categorize call on a bank line from
her IBAN; learned rules holding her IBAN hash (one on, one already off) and one scoped to her
tenancy. Controls: Otto, another tenant in the same workspace, with his own document, bank
line, call and rule; and a second workspace with a rule and a call on Lena's IBAN hash (the
key is per deployment, so the same IBAN hashes alike there), which RLS must keep out.
"""

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import OTHER_TENANT_IBAN, TENANT_IBAN, World
from sqlalchemy import insert, select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import tenant as tenant_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import AI_UNPARSABLE
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.allocation import (
    AllocationCategory,
    MatchingRuleCreate,
    RuleMatcher,
    SplitMethod,
)
from hausbuch.schemas.enums import (
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    LinkTarget,
    ProposalStatus,
    RetentionHold,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import UnitCreate
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import proposals
from hausbuch.services.documents import (
    current_extraction,
    link_document,
    unlink_document,
    upload_document,
)
from hausbuch.services.errors import RetentionHoldError
from hausbuch.services.events import list_events
from hausbuch.services.iban import protect_iban
from hausbuch.services.rules_learned import create_rule, disable_rule
from hausbuch.services.tenancies import end_tenancy
from hausbuch.services.tenants import erase_tenant
from hausbuch.services.units import create_unit
from hausbuch.services.workspace_settings import update_settings

ERASE_AT = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
LENA_HMAC = protect_iban(TENANT_IBAN, TEST_IBAN_KEY).hmac.hex()
OTTO_HMAC = protect_iban(OTHER_TENANT_IBAN, TEST_IBAN_KEY).hmac.hex()


def _v(value) -> ExtractedValue:
    return ExtractedValue(
        value=value, source=SourceRef(page=1, quote="…"), checks=["cited"], confidence="high"
    )


LEASE = ClassifyOutput(
    type=DocumentType.CONTRACT,
    subtype=DocumentSubtype.LEASE,
    contains_tenant_data=True,
    language="de",
    confidence="high",
)
LEASE_VALUES = {
    "title": _v("Mietvertrag Lindenstraße 12 WE 3"),
    "document_date": _v("2022-12-01"),
    "street": _v("Lindenstraße"),
    "house_number": _v("12"),
    "postal_code": _v("10115"),
    "city": _v("Berlin"),
    "unit_label": _v("WE 3"),
    "tenant_1_full_name": _v("Lena Beispiel"),
    "tenant_1_iban": _v(TENANT_IBAN),
    "start_date": _v("2023-01-01"),
    "signed_on": _v("2022-12-01"),
    "deposit_amount": _v("3450.00"),
    "deposit_clause": _v("Barkaution"),
    "cold_rent": _v("1150.00"),
    "operating_costs_prepayment": _v("250.00"),
}
DEPOSIT = ClassifyOutput(
    type=DocumentType.LETTER,
    subtype=DocumentSubtype.DEPOSIT_RECORD,
    contains_tenant_data=True,
    language="de",
    confidence="high",
)
DEPOSIT_VALUES = {
    "title": _v("Kautionsnachweis"),
    "document_date": _v("2023-01-03"),
    "amount": _v("3450.00"),
    "received_on": _v("2023-01-02"),
}


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    """The document_read job is not run here: its enqueue seam records nothing."""
    monkeypatch.setattr(enqueue, "enqueue_document_read", lambda conn, **_: None)


def _call(ctx, job: str, *, document_id=None, transaction_id=None) -> uuid.UUID:
    """An ai_call row as the worker writes it, with an output that quotes the tenant."""
    return ctx.conn.execute(
        insert(ai_call_table)
        .values(
            workspace_id=ctx.workspace_id,
            job=job,
            document_id=document_id,
            transaction_id=transaction_id,
            model="claude-test-model",
            prompt_version="test-1",
            input_sha256=b"\x01" * 32,
            output={"quote": "Mieter: Lena Beispiel"},
            raw_text="Mieter: Lena Beispiel",
        )
        .returning(ai_call_table.c.id)
    ).scalar_one()


def _upload(run, ws, store, data: bytes):
    return run(
        ws,
        upload_document,
        store,
        data,
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename="scan.pdf",
    ).id


def _read(run, ws, doc, classification, values, *, blocked=False):
    """request_read plus the job's result, with a classify (and extract) call: the proposal."""
    proposal = run(ws, proposals.request_read, doc)
    classify = run(ws, _call, "classify", document_id=doc)
    extract = None if values is None else run(ws, _call, "extract", document_id=doc)
    return run(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=classification,
        classify_call_id=classify,
        values=values,
        extract_call_id=extract,
        blocked_tenant_data=blocked,
    )


def _accept_all(proposal) -> ProposalApplyBody:
    return ProposalApplyBody(
        records=[
            ProposalApplyRecord(
                key=r.key, values={f: v.value for f, v in r.fields.items() if v.value is not None}
            )
            for r in proposal.records
        ]
    )


def _rule(run, ws, matcher: RuleMatcher, **rest) -> uuid.UUID:
    return run(ws, create_rule, MatchingRuleCreate(matcher=matcher, **rest)).id


@dataclass
class Seeded:
    lena: uuid.UUID
    lena_tenancy: uuid.UUID
    lease_doc: uuid.UUID
    lease_proposal: uuid.UUID
    rejected: uuid.UUID
    ready: uuid.UUID
    queued: uuid.UUID
    blocked: uuid.UUID
    lena_line_call: uuid.UUID
    in_scope_calls: list[uuid.UUID]
    rule_hmac: uuid.UUID
    rule_hmac_off: uuid.UUID
    rule_tenancy: uuid.UUID
    otto_doc: uuid.UUID
    otto_calls: list[uuid.UUID]
    otto_rule: uuid.UUID
    b_call: uuid.UUID
    b_rule: uuid.UUID


def _seed(run, app_engine, store, a, b, *, moved_out: bool) -> Seeded:
    world = World(app_engine, a)
    run(a, update_settings, ai_tenant_documents=True)
    we4 = run(a, create_unit, UnitCreate(property_id=world.property_id, label="WE 4")).id
    otto_tenancy = world.tenancy(
        name="Otto Kontrolle", iban=OTHER_TENANT_IBAN, start=date(2024, 1, 1), unit_id=we4
    )

    # Lena: tenant, tenancy and first rent from her Mietvertrag, applied as proposed.
    lease_doc = _upload(run, a, store, b"%PDF Mietvertrag Lena")
    lease = _read(run, a, lease_doc, LEASE, LEASE_VALUES)
    assert lease.status is ProposalStatus.READY
    applied = run(a, proposals.apply_proposal, lease.id, _accept_all(lease), iban_key=TEST_IBAN_KEY)
    entities = {x.key: x.entity_id for x in applied.applied}
    lena, tenancy = entities["tenant:1"], entities["tenancy"]
    # Only the applied proposal names her now: the scope must find the lease through it.
    run(a, unlink_document, lease_doc, LinkTarget.TENANCY, tenancy)
    if moved_out:
        run(a, end_tenancy, tenancy, date(2025, 12, 31))

    # Kautionsnachweise linked only to her tenancy: one rejected, one ready.
    rejected_doc = _upload(run, a, store, b"%PDF Kaution 1")
    run(a, link_document, rejected_doc, LinkTarget.TENANCY, tenancy)
    rejected = _read(run, a, rejected_doc, DEPOSIT, DEPOSIT_VALUES)
    run(a, proposals.reject_proposal, rejected.id)
    ready_doc = _upload(run, a, store, b"%PDF Kaution 2")
    run(a, link_document, ready_doc, LinkTarget.TENANCY, tenancy)
    ready = _read(run, a, ready_doc, DEPOSIT, DEPOSIT_VALUES)
    assert ready.status is ProposalStatus.READY
    # A letter linked to her as a tenant, its read still queued; one the gate blocked.
    queued_doc = _upload(run, a, store, b"%PDF Brief Lena")
    run(a, link_document, queued_doc, LinkTarget.TENANT, lena)
    queued = run(a, proposals.request_read, queued_doc)
    blocked_doc = _upload(run, a, store, b"%PDF Brief Lena 2")
    run(a, link_document, blocked_doc, LinkTarget.TENANT, lena)
    blocked = _read(run, a, blocked_doc, DEPOSIT, None, blocked=True)
    assert blocked.status is ProposalStatus.BLOCKED_TENANT_DATA

    # A bank line from her IBAN with a categorize call, and her learned rules.
    lena_line = world.line(date(2025, 1, 2), "1400.00", iban=TENANT_IBAN, purpose="Miete")
    lena_line_call = run(a, _call, "match", transaction_id=lena_line)
    rule_hmac = _rule(
        run,
        a,
        RuleMatcher(counterparty_iban_hmac=LENA_HMAC, amount_sign="credit"),
        split_method=SplitMethod.SINGLE_CATEGORY,
        category=AllocationCategory.RENT_COLD,
    )
    rule_hmac_off = _rule(
        run,
        a,
        RuleMatcher(counterparty_iban_hmac=LENA_HMAC, amount_sign="debit"),
        split_method=SplitMethod.SINGLE_CATEGORY,
        category=AllocationCategory.DEPOSIT_OUT,
    )
    run(a, disable_rule, rule_hmac_off)
    rule_tenancy = _rule(
        run,
        a,
        RuleMatcher(purpose_pattern="miete we 3"),
        split_method=SplitMethod.BY_RENT_SCHEDULE,
        tenancy_id=tenancy,
    )

    # Controls: Otto in the same workspace, and Lena's IBAN hash in workspace b.
    otto_doc = _upload(run, a, store, b"%PDF Mietvertrag Otto")
    run(a, link_document, otto_doc, LinkTarget.TENANCY, otto_tenancy)
    otto_line = world.line(date(2025, 1, 3), "1280.00", iban=OTHER_TENANT_IBAN)
    otto_calls = [
        run(a, _call, "classify", document_id=otto_doc),
        run(a, _call, "match", transaction_id=otto_line),
    ]
    otto_rule = _rule(
        run,
        a,
        RuleMatcher(counterparty_iban_hmac=OTTO_HMAC, amount_sign="credit"),
        split_method=SplitMethod.BY_RENT_SCHEDULE,
        tenancy_id=otto_tenancy,
    )
    world_b = World(app_engine, b)
    b_line = world_b.line(date(2025, 1, 2), "1400.00", iban=TENANT_IBAN)
    b_call = run(b, _call, "match", transaction_id=b_line)
    b_rule = _rule(
        run,
        b,
        RuleMatcher(counterparty_iban_hmac=LENA_HMAC, amount_sign="credit"),
        split_method=SplitMethod.SINGLE_CATEGORY,
        category=AllocationCategory.RENT_COLD,
    )

    in_scope_calls = _call_ids(app_engine, a, [lease_doc, rejected_doc, ready_doc, blocked_doc])
    assert len(in_scope_calls) == 7  # classify + extract x3, classify of the blocked one
    return Seeded(
        lena=lena,
        lena_tenancy=tenancy,
        lease_doc=lease_doc,
        lease_proposal=lease.id,
        rejected=rejected.id,
        ready=ready.id,
        queued=queued.id,
        blocked=blocked.id,
        lena_line_call=lena_line_call,
        in_scope_calls=in_scope_calls,
        rule_hmac=rule_hmac,
        rule_hmac_off=rule_hmac_off,
        rule_tenancy=rule_tenancy,
        otto_doc=otto_doc,
        otto_calls=otto_calls,
        otto_rule=otto_rule,
        b_call=b_call,
        b_rule=b_rule,
    )


def _call_ids(engine, ws, document_ids) -> list[uuid.UUID]:
    with workspace_transaction(engine, ws) as conn:
        return list(
            conn.execute(
                select(ai_call_table.c.id).where(ai_call_table.c.document_id.in_(document_ids))
            ).scalars()
        )


def _proposals(engine, ws) -> dict:
    p = proposal_table
    with workspace_transaction(engine, ws) as conn:
        rows = conn.execute(
            select(p.c.id, p.c.status, p.c.error_code, p.c.fields, p.c.decisions, p.c.scrubbed_at)
        ).all()
    return {r.id: r for r in rows}


def _calls(engine, ws) -> dict:
    a = ai_call_table
    with workspace_transaction(engine, ws) as conn:
        rows = conn.execute(select(a.c.id, a.c.output, a.c.raw_text)).all()
    return {r.id: (r.output, r.raw_text) for r in rows}


def _rules(engine, ws) -> dict:
    r = rule_table
    with workspace_transaction(engine, ws) as conn:
        rows = conn.execute(select(r.c.id, r.c.enabled, r.c.matcher)).all()
    return {row.id: (row.enabled, row.matcher) for row in rows}


def _state(engine, ws) -> tuple:
    return _proposals(engine, ws), _calls(engine, ws), _rules(engine, ws)


def test_erasure_scrubs_proposals_calls_and_rules_and_nothing_else(
    run, read, app_engine, fresh_workspaces, blob_store
):
    a, b = fresh_workspaces
    s = _seed(run, app_engine, blob_store, a, b, moved_out=True)
    before_events = read(a, list_events)
    b_before = _state(app_engine, b)

    erased = run(a, erase_tenant, s.lena, on_request=True, now=ERASE_AT)
    assert erased.full_name is None

    # tenant.erased carries the counts: the 5 proposals of the 5 documents in scope, the 8
    # calls with output (7 of those documents, 1 on her bank line), the 2 rules still on.
    event = read(a, list_events, entity_id=s.lena)[-1]
    assert event.type is EventType.TENANT_ERASED
    assert event.payload == {
        "basis": "data_subject_request",
        "proposals_scrubbed": 5,
        "ai_calls_scrubbed": 8,
        "rules_disabled": 2,
    }
    new_events = read(a, list_events)[len(before_events) :]
    assert Counter(e.type for e in new_events) == {
        EventType.PROPOSAL_SUPERSEDED: 3,  # ready, queued, blocked
        EventType.RULE_DISABLED: 2,
        EventType.TENANT_ERASED: 1,
    }
    superseded = {e.entity_id for e in new_events if e.type is EventType.PROPOSAL_SUPERSEDED}
    assert superseded == {s.ready, s.queued, s.blocked}
    for e in new_events:
        assert "Lena" not in str(e.payload) and TENANT_IBAN not in str(e.payload)

    # Proposals: every one of those documents scrubbed; the undecided ones superseded first.
    rows = _proposals(app_engine, a)
    expected_status = {
        s.lease_proposal: "applied",
        s.rejected: "rejected",
        s.ready: "superseded",
        s.queued: "superseded",
        s.blocked: "superseded",
    }
    for proposal_id, status in expected_status.items():
        row = rows[proposal_id]
        assert (row.status, row.fields, row.decisions) == (status, None, None), proposal_id
        assert row.scrubbed_at == ERASE_AT and row.error_code is None
    # The eval record survives: proposal.applied keeps its per-field decisions (D41).
    [applied] = [
        e
        for e in read(a, list_events, entity_id=s.lease_proposal)
        if e.type is EventType.PROPOSAL_APPLIED
    ]
    tenant_record = next(r for r in applied.payload["records"] if r["key"] == "tenant:1")
    assert tenant_record["decisions"]["full_name"] == "accepted"
    assert read(a, proposals.get_proposal, s.lease_proposal).records == []
    # The document's extraction is not touched: it follows the document's retention (§3.5).
    assert read(a, current_extraction, s.lease_doc) is not None

    # ai_call: the in-scope rows nulled, the controls untouched.
    calls = _calls(app_engine, a)
    for call_id in [*s.in_scope_calls, s.lena_line_call]:
        assert calls[call_id] == (None, None)
    for call_id in s.otto_calls:
        assert calls[call_id] != (None, None)

    # Rules: hers off (the one already off stays off, with no second event); Otto's stays on.
    rules = _rules(app_engine, a)
    assert [rules[r][0] for r in (s.rule_hmac, s.rule_hmac_off, s.rule_tenancy)] == [False] * 3
    assert rules[s.otto_rule][0] is True

    # The tenant row's hash is gone, the other workspace is untouched (RLS).
    with workspace_transaction(app_engine, a) as conn:
        hmac = conn.execute(
            select(tenant_table.c.iban_hmac).where(tenant_table.c.id == s.lena)
        ).scalar_one()
    assert hmac is None
    assert _state(app_engine, b) == b_before
    assert _rules(app_engine, b)[s.b_rule][0] is True
    assert _calls(app_engine, b)[s.b_call] != (None, None)

    # A read still in flight cannot write the scrubbed proposal back: its result and its
    # failure are no-ops on a superseded proposal.
    late = run(
        a,
        proposals.record_read_result,
        s.queued,
        classification=DEPOSIT,
        classify_call_id=s.in_scope_calls[0],
        values=None,
        extract_call_id=None,
    )
    assert late.status is ProposalStatus.SUPERSEDED and late.records == []
    failed = run(
        a,
        proposals.record_read_failure,
        s.queued,
        error_code=AI_UNPARSABLE,
        ai_call_id=None,
        attempts=1,
    )
    assert failed.status is ProposalStatus.SUPERSEDED
    row = _proposals(app_engine, a)[s.queued]
    assert (row.fields, row.decisions, row.error_code) == (None, None, None)

    # Erasing again is a no-op: no second event, nothing to scrub.
    count = len(read(a, list_events))
    run(a, erase_tenant, s.lena, on_request=True, now=ERASE_AT)
    assert len(read(a, list_events)) == count


def test_an_erasure_refused_by_a_retention_hold_scrubs_nothing(
    run, read, app_engine, fresh_workspaces, blob_store
):
    a, b = fresh_workspaces
    s = _seed(run, app_engine, blob_store, a, b, moved_out=False)  # her tenancy still runs
    before = (_state(app_engine, a), _state(app_engine, b))
    events = len(read(a, list_events))

    with pytest.raises(RetentionHoldError) as raised:
        run(a, erase_tenant, s.lena, on_request=True, now=ERASE_AT)

    assert raised.value.result.hold is RetentionHold.TENANCY_ACTIVE
    assert (_state(app_engine, a), _state(app_engine, b)) == before
    assert len(read(a, list_events)) == events
