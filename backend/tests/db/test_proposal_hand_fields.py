"""One pre-filled form (phase 2 D132) against Postgres: the typing record of a proposal
carries the document's hand-entry fields, and applying it saves them as the typing form
does (D37: a new extraction version only when a value changed, into the reading's own
field; refused when the fields changed since the proposal was built). A hand field another
record asks for instead (an invoice's Betrag in its Nebenkosten row) takes the value applied
there (J1), and a typing-only proposal's stale anchor is the version it was built from (F1).

The enqueue seam is monkeypatched and ai_call rows are inserted directly, as in
test_services_proposals.py.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_proposals import (
    FLAT,
    INVOICE,
    INVOICE_VALUES,
    LOAN,
    LOAN_VALUES,
    _accept_all,
)
from db.test_services_proposals_tax_inputs import ZINS, ZINS_VALUES
from db.test_services_proposals_tax_inputs import _loan as _create_loan
from sqlalchemy import insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import RuleError
from hausbuch.rules.extraction_parse import derive_fields
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.document import DocumentTyping, ManualDocumentFields
from hausbuch.schemas.enums import (
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    ExtractionKind,
    ExtractionSource,
)
from hausbuch.schemas.proposal import CarriedField, ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import matching, proposals
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import (
    EXTRACTION_STALE,
    current_extraction,
    hand_fields,
    type_document,
    upload_document,
)
from hausbuch.services.loans import list_loans
from hausbuch.services.properties import create_property, list_acquisition_costs
from hausbuch.services.tax_inputs import list_tax_inputs

JOB = Actor(ActorType.AI, "claude-test-model")
MANAGEMENT = ClassifyOutput(
    type=DocumentType.CONTRACT,
    subtype=DocumentSubtype.MANAGEMENT_CONTRACT,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
LETTER = ClassifyOutput(
    type=DocumentType.LETTER,
    subtype=None,
    contains_tenant_data=False,
    language="de",
    confidence="medium",
)


def _v(value, quote="…") -> ExtractedValue:
    return ExtractedValue(
        value=value, source=SourceRef(page=1, quote=quote), checks=["cited"], confidence="high"
    )


# A Verwaltervertrag whose fee is 30,00 € zzgl. MwSt.: the job derives Betrag 35,70 (PR #137).
MANAGEMENT_VALUES = derive_fields(
    ExtractionKind.MANAGEMENT_CONTRACT,
    {
        "title": _v("Mietverwaltungsvertrag"),
        "manager": _v("Muster Hausverwaltung GmbH"),
        "start_date": _v("2026-05-01"),
        "fee_net": _v("30.00", "Vergütung 30,00 € zzgl. MwSt."),
        "monthly_fee": _v("30,00 € zzgl. MwSt. je Einheit"),
    },
)


@pytest.fixture
def enqueued(monkeypatch):
    monkeypatch.setattr(enqueue, "enqueue_document_read", lambda conn, **_: None)


@pytest.fixture
def job(app_engine):
    def _job(ws, command, *args, **kwargs):
        with workspace_transaction(app_engine, ws) as conn:
            return command(new_command(conn, ws, JOB), *args, **kwargs)

    return _job


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


def _ready(run, job, ws, store, classification, values, data: bytes):
    """Upload, request and report a read: the document and its ready proposal. Without
    `values` only classification ran (a typing-only proposal)."""
    doc = run(
        ws,
        upload_document,
        store,
        data,
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename="dokument.pdf",
    )
    proposal = run(ws, proposals.request_read, doc.id)
    classify_id = run(ws, _ai_call, doc.id, "classify")
    extract_id = None if values is None else run(ws, _ai_call, doc.id, "extract")
    ready = job(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=classification,
        classify_call_id=classify_id,
        values=values,
        extract_call_id=extract_id,
    )
    return doc, ready


def _typing_body(ready, **changes) -> ProposalApplyBody:
    """The typing record as the review screen sends it: every field it carries, an empty
    one as null (a hand field the user cleared is detectable), with `changes` typed over."""
    (typing,) = [r for r in ready.records if r.key == "typing"]
    values = {name: value.value for name, value in typing.fields.items()} | changes
    return ProposalApplyBody(records=[ProposalApplyRecord(key="typing", values=values)])


def test_an_untouched_computed_management_fee_stores_no_new_version(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready = _ready(run, job, ws, blob_store, MANAGEMENT, MANAGEMENT_VALUES, b"%PDF mv 1")
    (typing,) = [r for r in ready.records if r.key == "typing"]
    amount = typing.fields["amount_total"]
    assert (amount.value, amount.source, amount.derived_from) == (
        "35.70",
        None,
        ["fee_net", "monthly_fee"],
    )
    assert typing.fields["counterparty"].value == "Muster Hausverwaltung GmbH"
    assert typing.fields["period_start"].value == "2026-05-01"
    assert typing.fields["reference"].value is None  # a box to type it in

    run(ws, proposals.apply_proposal, ready.id, _typing_body(ready), iban_key=TEST_IBAN_KEY)

    extraction = read(ws, current_extraction, doc.id)
    assert extraction.version == 1 and extraction.source is ExtractionSource.AI
    assert hand_fields(extraction).values.amount_total == Decimal("35.70")
    typed = read(ws, lambda conn: proposals.documents.get_document(conn, doc.id)).typing
    assert typed.subtype is DocumentSubtype.MANAGEMENT_CONTRACT


def test_a_fee_edited_in_the_card_is_saved_by_the_apply_as_a_new_version(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready = _ready(run, job, ws, blob_store, MANAGEMENT, MANAGEMENT_VALUES, b"%PDF mv 2")
    body = _typing_body(ready, amount_total="40.46", reference="MV-2026-7")
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    extraction = read(ws, current_extraction, doc.id)
    assert extraction.version == 2 and extraction.source is ExtractionSource.MANUAL
    assert extraction.kind is ExtractionKind.MANAGEMENT_CONTRACT  # D37: the reading's own kind
    values = hand_fields(extraction).values
    assert (values.amount_total, values.reference) == (Decimal("40.46"), "MV-2026-7")
    # The rest of the reading is as the model read it.
    assert extraction.fields.fee_net.value == "30.00"
    assert extraction.fields.manager.value == "Muster Hausverwaltung GmbH"


def test_a_hand_field_cleared_in_the_card_is_cleared(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready = _ready(run, job, ws, blob_store, MANAGEMENT, MANAGEMENT_VALUES, b"%PDF mv 3")
    run(
        ws,
        proposals.apply_proposal,
        ready.id,
        _typing_body(ready, counterparty=None),
        iban_key=TEST_IBAN_KEY,
    )
    extraction = read(ws, current_extraction, doc.id)
    assert extraction.version == 2
    assert hand_fields(extraction).values.counterparty is None


def test_an_apply_without_hand_fields_leaves_the_reading_as_it_is(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """A body that sends only the typing (an older screen, a direct API call) never blanks
    the reading: a hand field not sent stays as it is."""
    ws, _ = fresh_workspaces
    doc, ready = _ready(run, job, ws, blob_store, MANAGEMENT, MANAGEMENT_VALUES, b"%PDF mv 4")
    body = ProposalApplyBody(
        records=[
            ProposalApplyRecord(
                key="typing", values={"type": "contract", "subtype": "management_contract"}
            )
        ]
    )
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)
    extraction = read(ws, current_extraction, doc.id)
    assert extraction.version == 1
    assert hand_fields(extraction).values.amount_total == Decimal("35.70")


def test_an_apply_after_the_fields_changed_is_refused_as_stale(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    doc, ready = _ready(run, job, ws, blob_store, MANAGEMENT, MANAGEMENT_VALUES, b"%PDF mv 5")
    typing = DocumentTyping(type=DocumentType.CONTRACT)
    run(
        ws,
        type_document,
        doc.id,
        typing,
        ManualDocumentFields(amount_total=Decimal("35.70"), notes="von Hand"),
        shown_version=1,
    )
    with pytest.raises(RuleError) as stale:
        run(ws, proposals.apply_proposal, ready.id, _typing_body(ready), iban_key=TEST_IBAN_KEY)
    assert stale.value.code == EXTRACTION_STALE
    assert read(ws, proposals.get_proposal, ready.id).status.value == "ready"


def test_a_typing_only_proposal_carries_the_six_hand_fields_to_type(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """No reading of its own: the card offers what the typing form would (all six for a
    document without fields); nothing typed writes no version, a typed value a manual one."""
    ws, _ = fresh_workspaces
    doc, ready = _ready(run, job, ws, blob_store, LETTER, None, b"%PDF brief 1")
    (typing,) = [r for r in ready.records if r.key == "typing"]
    hand = {"amount_total", "period_start", "period_end", "counterparty", "reference", "notes"}
    assert hand <= set(typing.fields)
    assert all(typing.fields[name].value is None for name in hand)

    run(ws, proposals.apply_proposal, ready.id, _typing_body(ready), iban_key=TEST_IBAN_KEY)
    assert read(ws, current_extraction, doc.id) is None

    doc2, ready2 = _ready(run, job, ws, blob_store, LETTER, None, b"%PDF brief 2")
    body = _typing_body(ready2, amount_total="120.00", counterparty="Stadtwerke Musterstadt")
    run(ws, proposals.apply_proposal, ready2.id, body, iban_key=TEST_IBAN_KEY)
    extraction = read(ws, current_extraction, doc2.id)
    assert (extraction.version, extraction.kind) == (1, ExtractionKind.MANUAL)
    assert extraction.fields == ManualDocumentFields(
        amount_total=Decimal("120.00"), counterparty="Stadtwerke Musterstadt"
    )


# --- A hand field another record asks for (J1) ------------------------------------------

# The notary's 4.321,10 misread as 4312.10: the user corrects it in the Nebenkosten card.
MISREAD_INVOICE = INVOICE_VALUES | {"amount_total": _v("4312.10", "Summe 4.321,10 EUR")}


def _facts(read, ws, document_id):
    """The document as the bank-line document matcher reads it (amount and date)."""

    def facts(conn):
        evidence = matching.evidence_of(conn, document_id)
        assert evidence is not None
        return matching.document_facts(evidence, iban_key=TEST_IBAN_KEY, loans_by_property={})

    return read(ws, facts)


def test_an_amount_corrected_in_the_nebenkosten_card_reaches_the_reading(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT)
    doc, ready = _ready(run, job, ws, blob_store, INVOICE, MISREAD_INVOICE, b"%PDF j1 1")
    (typing,) = [r for r in ready.records if r.key == "typing"]
    assert "amount_total" not in typing.fields  # asked once, in the cost card
    assert typing.hand_from["amount_total"] == CarriedField(key="cost:notary", field="amount")
    assert _facts(read, ws, doc.id).payments[0].amount == Decimal("4312.10")

    body = _accept_all(ready, **{"cost:notary": {"amount": "4321.10"}})
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    [cost] = read(ws, list_acquisition_costs, prop.id)
    assert cost.amount == Decimal("4321.10")
    extraction = read(ws, current_extraction, doc.id)
    assert (extraction.version, extraction.kind) == (2, ExtractionKind.INVOICE)
    assert extraction.source is ExtractionSource.MANUAL
    assert hand_fields(extraction).values.amount_total == Decimal("4321.10")
    assert extraction.fields.reference.value == "UR 123/2024"  # the rest as read
    # The bank line's document match now finds the invoice by the amount actually paid.
    facts = _facts(read, ws, doc.id)
    assert [p.amount for p in facts.payments] == [Decimal("4321.10")]


def test_an_amount_left_as_read_in_the_nebenkosten_card_writes_no_new_version(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    run(ws, create_property, FLAT)
    doc, ready = _ready(run, job, ws, blob_store, INVOICE, INVOICE_VALUES, b"%PDF j1 2")
    run(ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)
    extraction = read(ws, current_extraction, doc.id)
    assert (extraction.version, extraction.source) == (1, ExtractionSource.AI)
    assert hand_fields(extraction).values.amount_total == Decimal("4321.10")


def test_a_skipped_nebenkosten_row_leaves_the_reading_as_read(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT)
    doc, ready = _ready(run, job, ws, blob_store, INVOICE, MISREAD_INVOICE, b"%PDF j1 3")
    records = [
        r.model_copy(update={"skip": r.key == "cost:notary"})
        for r in _accept_all(ready, **{"cost:notary": {"amount": "4321.10"}}).records
    ]
    body = ProposalApplyBody(records=records)
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    assert read(ws, list_acquisition_costs, prop.id) == []
    extraction = read(ws, current_extraction, doc.id)
    assert extraction.version == 1
    assert hand_fields(extraction).values.amount_total == Decimal("4312.10")


def test_a_lender_corrected_in_the_new_loans_card_reaches_the_reading(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT)
    doc, ready = _ready(run, job, ws, blob_store, LOAN, LOAN_VALUES, b"%PDF j1 4")
    (typing,) = [r for r in ready.records if r.key == "typing"]
    assert typing.hand_from == {
        "counterparty": CarriedField(key="loan", field="lender"),
        "reference": CarriedField(key="loan", field="contract_number"),
    }
    body = _accept_all(ready, loan={"lender": "Berliner Sparkasse"})
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    [loan] = read(ws, list_loans, prop.id)
    assert loan.lender == "Berliner Sparkasse"
    extraction = read(ws, current_extraction, doc.id)
    assert extraction.version == 2
    values = hand_fields(extraction).values
    assert (values.counterparty, values.reference) == ("Berliner Sparkasse", "4711 0815")


def test_interest_corrected_in_the_tax_input_card_reaches_the_reading(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """The tax input is applied after the typing: the value comes from the body."""
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    _create_loan(run, ws, prop)
    doc, ready = _ready(run, job, ws, blob_store, ZINS, ZINS_VALUES, b"%PDF j1 5")
    body = _accept_all(ready, **{"tax_input:interest_certified": {"value.amount": "8132.45"}})
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    [row] = read(ws, list_tax_inputs, prop)
    assert row.value.amount == Decimal("8132.45")
    extraction = read(ws, current_extraction, doc.id)
    assert extraction.version == 2
    assert hand_fields(extraction).values.amount_total == Decimal("8132.45")


# --- The stale anchor of a typing-only proposal (F1) -------------------------------------


def test_a_typing_only_proposal_pins_the_version_its_hand_fields_came_from(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """A typing whose clock is earlier than the proposal's but whose version lands after the
    proposal was built is still caught: the anchor is the version read, not a timestamp."""
    ws, _ = fresh_workspaces
    doc, ready = _ready(run, job, ws, blob_store, LETTER, None, b"%PDF brief f1")
    (typing,) = [r for r in ready.records if r.key == "typing"]
    assert typing.hand_version == 0
    run(
        ws,
        type_document,
        doc.id,
        DocumentTyping(type=DocumentType.LETTER),
        ManualDocumentFields(notes="von Hand"),
        shown_version=0,
        now=ready.updated_at - timedelta(seconds=1),
    )
    body = _typing_body(ready, amount_total="120.00")
    with pytest.raises(RuleError) as stale:
        run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)
    assert stale.value.code == EXTRACTION_STALE
