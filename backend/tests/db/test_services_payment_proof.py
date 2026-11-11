"""A bill's proof of payment (docs/phases/phase-3.md D143), against the real database: the
upload from the to-do types and links the proof (attach_payment_proof), the reading job
skips the classifier for it and never imports it as a statement, the draft reads the proofs
linked to a bill and raises payment_proof.missing until one matches, and a proof paid in
another year moves the bill's private_cost rows there (withdraw + create, append-only).

Synthetic data only (World's flat, an invented caretaker firm, owners A and B); the model
answers are synthetic (hausbuch.ai.fake), never API output."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_jobs_document_read import _pdf, _settings
from db.test_services_proposals import _accept_all, _ai_call, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from sqlalchemy import func, select

from hausbuch.ai.fake import FakeModelClient, cited_response
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import (
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    ExtractionKind,
    LinkTarget,
    ProposalStatus,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.tax import TaxInputCreate, TaxInputKind, WarningCode
from hausbuch.services import proposals
from hausbuch.services.documents import (
    NotAPaymentProofError,
    SelfLinkError,
    attach_payment_proof,
    current_extraction,
    list_links,
    type_document,
    upload_document,
)
from hausbuch.services.errors import NotFoundError
from hausbuch.services.tax import anlage_v_draft
from hausbuch.services.tax_inputs import create_tax_input, list_tax_inputs

pytestmark = pytest.mark.db

NOW = datetime(2027, 2, 1, 12, 0, tzinfo=UTC)
TODAY = date(2027, 2, 1)
PROOF = ClassifyOutput(
    type=DocumentType.OTHER,
    subtype=DocumentSubtype.PAYMENT_PROOF,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _upload(run, ws, store, data: bytes):
    return run(
        ws,
        upload_document,
        store,
        data,
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename="beleg.pdf",
    )


def _bill(run, world, store, data=b"%PDF Rechnung Hausmeisterdienst", amounts=("357.00",)):
    """A bill paid from a private account: its private_cost rows of 2026."""
    bill = _upload(run, world.ws, store, data)
    rows = [
        run(
            world.ws,
            create_tax_input,
            world.property_id,
            TaxInputCreate.model_validate(
                {
                    "property_id": str(world.property_id),
                    "kind": "private_cost",
                    "tax_year": 2026,
                    "document_id": str(bill.id),
                    "value": {
                        "cost_class": "other",
                        "label": "Rechnung Hausmeisterdienst",
                        "amount": amount,
                    },
                }
            ),
        )
        for amount in amounts
    ]
    return bill, rows


def _proof_warnings(read, world, year=2026):
    draft = read(world.ws, anlage_v_draft, world.property_id, tax_year=year, today=TODAY)
    return [w for w in draft.warnings if w.code is WarningCode.PAYMENT_PROOF_MISSING]


def _proof_values(amount="-476.00", paid_on="2027-01-08") -> dict:
    return {
        "title": _v("Überweisung Hausmeisterdienst"),
        "document_date": _v(paid_on),
        "paid_on": _v(paid_on),
        "amount": _v(amount),
        "payee": _v("Hausmeisterdienst Muster GmbH"),
        "purpose": _v("Rechnung 2026-118"),
    }


def _read_proof(run, job, ws, proof, values):
    """The job's hand-over for a pre-typed proof: no classify call (D143)."""
    proposal = run(ws, proposals.request_read, proof.id)
    return job(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=PROOF,
        classify_call_id=None,
        values=values,
        extract_call_id=run(ws, _ai_call, proof.id, "extract"),
    )


# --- the upload from the to-do ----------------------------------------------------------


def test_a_proof_is_typed_and_linked_to_its_bill_and_property(world, run, read, blob_store):
    ws = world.ws
    bill, _ = _bill(run, world, blob_store)
    proof = _upload(run, ws, blob_store, b"%PDF Ueberweisung")
    typed = run(ws, attach_payment_proof, proof.id, bill.id)
    assert (typed.typing.type, typed.typing.subtype) == (
        DocumentType.OTHER,
        DocumentSubtype.PAYMENT_PROOF,
    )
    links = {(link.target_type, link.target_id) for link in read(ws, list_links, proof.id)}
    assert links == {(LinkTarget.DOCUMENT, bill.id), (LinkTarget.PROPERTY, world.property_id)}

    # The same file again, from the same to-do: nothing new, nothing refused.
    run(ws, attach_payment_proof, proof.id, bill.id)
    assert len(read(ws, list_links, proof.id)) == 2


def test_the_upload_refuses_the_bill_itself_an_unknown_bill_and_another_document(
    world, run, blob_store
):
    ws = world.ws
    bill, _ = _bill(run, world, blob_store)
    with pytest.raises(SelfLinkError):
        run(ws, attach_payment_proof, bill.id, bill.id)
    proof = _upload(run, ws, blob_store, b"%PDF Ueberweisung")
    with pytest.raises(NotFoundError):
        run(ws, attach_payment_proof, proof.id, world.unit_id)  # not a document
    contract = _upload(run, ws, blob_store, b"%PDF Vertrag")
    run(ws, type_document, contract.id, DocumentTyping(type=DocumentType.CONTRACT, subtype=None))
    with pytest.raises(NotAPaymentProofError):
        run(ws, attach_payment_proof, contract.id, bill.id)


# --- the draft --------------------------------------------------------------------------


def test_the_draft_asks_for_a_proof_until_one_matches(world, run, read, job, blob_store):
    ws = world.ws
    bill, _ = _bill(run, world, blob_store, amounts=("357.00", "119.00"))
    [missing] = _proof_warnings(read, world)
    assert missing.params == {
        "document": str(bill.id),
        "source": "private_cost",
        "label": "Rechnung Hausmeisterdienst",
        "amount": "476.00",
    }

    # A proof whose amount is not read yet: the user saw it, the to-do goes.
    proof = _upload(run, ws, blob_store, b"%PDF Ueberweisung 1")
    run(ws, attach_payment_proof, proof.id, bill.id)
    assert _proof_warnings(read, world) == []

    # Read as 400.00: the to-do comes back with the proof's amount.
    _read_proof(run, job, ws, proof, _proof_values("400.00", "2026-12-20"))
    [mismatch] = _proof_warnings(read, world)
    assert mismatch.params["proof_amount"] == "400.00"

    # A second proof for the rest: the sum matches.
    rest = _upload(run, ws, blob_store, b"%PDF Ueberweisung 2")
    run(ws, attach_payment_proof, rest.id, bill.id)
    _read_proof(run, job, ws, rest, _proof_values("-76.00", "2026-12-21"))
    assert _proof_warnings(read, world) == []


# --- the year move ----------------------------------------------------------------------


def test_a_proof_paid_in_the_next_year_moves_the_rows_there(world, run, read, job, blob_store):
    ws, prop = world.ws, world.property_id
    bill, rows = _bill(run, world, blob_store, amounts=("357.00", "119.00"))
    proof = _upload(run, ws, blob_store, b"%PDF Ueberweisung 2027")
    run(ws, attach_payment_proof, proof.id, bill.id)
    ready = _read_proof(run, job, ws, proof, _proof_values())
    assert ready.status is ProposalStatus.READY and ready.kind is ExtractionKind.PAYMENT_PROOF
    moves = {r.key: r for r in ready.records if r.key.startswith("private_cost_year:")}
    assert {r.target_id for r in moves.values()} == {row.id for row in rows}
    # No document.classified: there was no classify call.
    events = world.query(
        lambda conn: conn.execute(
            select(event_table.c.type).where(event_table.c.entity_id == proof.id)
        ).all()
    )
    assert EventType.DOCUMENT_CLASSIFIED.value not in {e.type for e in events}

    run(ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)
    current = read(ws, list_tax_inputs, prop, kind=TaxInputKind.PRIVATE_COST)
    assert sorted((r.tax_year, r.value.amount, r.document_id) for r in current) == [
        (2027, Decimal("119.00"), bill.id),
        (2027, Decimal("357.00"), bill.id),
    ]
    history = read(ws, list_tax_inputs, prop, kind=TaxInputKind.PRIVATE_COST, include_history=True)
    withdrawn = [r for r in history if r.withdrawn]
    assert {r.supersedes_id for r in withdrawn} == {row.id for row in rows}
    assert all(r.tax_year == 2026 for r in withdrawn)
    assert _proof_warnings(read, world, 2026) == []  # nothing of the bill counts in 2026


def test_a_mismatched_proof_moves_nothing(world, run, read, job, blob_store):
    ws, prop = world.ws, world.property_id
    bill, _ = _bill(run, world, blob_store, amounts=("357.00", "119.00"))
    proof = _upload(run, ws, blob_store, b"%PDF Ueberweisung falsch")
    run(ws, attach_payment_proof, proof.id, bill.id)
    ready = _read_proof(run, job, ws, proof, _proof_values("400.00"))
    assert [r.key for r in ready.records] == ["typing"]
    assert any("passt nicht" in note for note in ready.records[0].notes)
    run(ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)
    current = read(ws, list_tax_inputs, prop, kind=TaxInputKind.PRIVATE_COST)
    assert {r.tax_year for r in current} == {2026}


# --- the job ----------------------------------------------------------------------------


def test_the_job_reads_a_proof_without_the_classifier_and_never_imports_it(
    world, run, read, blob_store, app_engine, tmp_path, monkeypatch
):
    """The job sends one request, the extraction: a classify call would have taken the only
    scripted answer and failed. The payer's name and account number are scrubbed from the
    quotes; no statement import is created."""
    monkeypatch.setattr("hausbuch.jobs.enqueue.enqueue_document_read", lambda *a, **k: None)
    ws = world.ws
    bill, _ = _bill(run, world, blob_store)
    proof = _upload(run, ws, blob_store, _pdf(1, "proof"))
    run(ws, attach_payment_proof, proof.id, bill.id)
    proposal = run(ws, proposals.request_read, proof.id)
    answer = cited_response(
        {
            "paid_on": ("08.01.2027", 1, "Ausgeführt am 08.01.2027"),
            "amount": ("-357,00 EUR", 1, "Betrag -357,00 EUR"),
            "payee": (
                "Hausmeisterdienst Muster GmbH",
                1,
                "Auftraggeber: Erika Beispiel  Empfänger: Hausmeisterdienst Muster GmbH",
            ),
            "purpose": ("Rechnung 2026-118", 1, "Kontonummer 1234567890 Rechnung 2026-118"),
        }
    )
    deps = JobDeps(
        engine=app_engine,
        settings=_settings(tmp_path),
        blob_store=blob_store,
        model_client=FakeModelClient([answer]),
        clock=lambda: NOW,
    )
    run_document_read(
        deps, workspace_id=ws, document_id=proof.id, proposal_id=proposal.id, attempt=0
    )

    jobs = world.query(
        lambda conn: conn.execute(
            select(ai_call_table.c.job).where(ai_call_table.c.document_id == proof.id)
        ).all()
    )
    assert [j.job for j in jobs] == ["extract"]
    stored = read(ws, current_extraction, proof.id)
    assert stored.kind is ExtractionKind.PAYMENT_PROOF
    assert stored.fields.amount.value == "-357.00"
    quotes = " ".join(
        getattr(stored.fields, name).source.quote for name in ("payee", "purpose", "amount")
    )
    assert "Erika" not in quotes and "1234567890" not in quotes
    assert "Hausmeisterdienst Muster GmbH" in quotes
    imports = world.query(
        lambda conn: conn.execute(
            select(func.count())
            .select_from(import_table)
            .where(import_table.c.document_id == proof.id)
        ).scalar_one()
    )
    assert imports == 0
    assert _proof_warnings(read, world) == []  # 357.00 proves the 357.00 bill
