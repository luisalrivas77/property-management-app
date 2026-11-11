"""A payment proof uploaded anywhere finds its bill (docs/phases/phase-3.md D149), against
the real database: the reading of a proof linked to no bill offers the bills whose draft
still raises payment_proof.missing and pre-selects the one its amount pays; the apply links
the proof as the to-do's upload does and moves that bill's rows to the year paid, never a
bill the user did not pick; the bill's to-do offers a stored proof that fits it, and
POST /documents/{id}/payment-proof links it.

Synthetic data only (World's flat, invented firms, owners A and B); the model answers are
synthetic, never API output."""

from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_payment_proof import _bill, _proof_warnings, _upload
from db.test_services_proposals import _accept_all, _ai_call, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)

from hausbuch.rules.prefill import PROOF_LINK_KEY
from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, LinkTarget, ProposalStatus
from hausbuch.schemas.proposal import ProposalApplyBody
from hausbuch.schemas.tax import TaxInputKind
from hausbuch.services import proposals
from hausbuch.services.documents import (
    NotAPaymentProofError,
    attach_payment_proof,
    get_document,
    list_links,
)
from hausbuch.services.tax_inputs import list_tax_inputs

pytestmark = pytest.mark.db

CLASSIFIED = ClassifyOutput(
    type=DocumentType.OTHER,
    subtype=DocumentSubtype.PAYMENT_PROOF,
    contains_tenant_data=False,
    language="en",
    confidence="high",
)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _values(amount: str, paid_on: str = "2027-01-14") -> dict:
    return {
        "document_date": _v("2027-01-15"),
        "paid_on": _v(paid_on),
        "amount": _v(amount),
        "payee": _v("Malerbetrieb Beispiel GmbH"),
        "purpose": _v("Rechnung R-2026-311 Malerarbeiten"),
    }


def _read_classified(run, job, ws, proof, values):
    """The job's hand-over for a proof the classifier typed (D149): a classify call too."""
    proposal = run(ws, proposals.request_read, proof.id)
    return job(
        ws,
        proposals.record_read_result,
        proposal.id,
        classification=CLASSIFIED,
        classify_call_id=run(ws, _ai_call, proof.id, "classify"),
        values=values,
        extract_call_id=run(ws, _ai_call, proof.id, "extract"),
    )


def _typing_only(ready) -> ProposalApplyBody:
    """„keine dieser Rechnungen“: every record but the typing skipped."""
    full = _accept_all(ready)
    skipped = [r.model_copy(update={"skip": r.key != "typing"}) for r in full.records]
    return ProposalApplyBody(records=skipped)


def _two_bills(run, world, store):
    painter, [painter_row] = _bill(run, world, store, data=b"%PDF Maler", amounts=("595.00",))
    caretaker, _ = _bill(run, world, store, data=b"%PDF Hausmeister", amounts=("357.00",))
    return painter, painter_row, caretaker


def test_a_proof_uploaded_without_a_bill_proposes_and_links_the_bill_it_pays(
    world, run, read, job, blob_store
):
    ws, prop = world.ws, world.property_id
    painter, painter_row, caretaker = _two_bills(run, world, blob_store)
    proof = _upload(run, ws, blob_store, b"%PDF Transaction Summary")
    ready = _read_classified(run, job, ws, proof, _values("595.00"))
    assert ready.status is ProposalStatus.READY
    by_key = {r.key: r for r in ready.records}
    assert set(by_key) == {"typing", PROOF_LINK_KEY, "private_cost_year:1"}
    link = by_key[PROOF_LINK_KEY]
    assert link.fields["target_id"].value == str(painter.id)
    assert {o.value for o in link.options["target_id"]} == {str(painter.id), str(caretaker.id)}
    assert by_key["private_cost_year:1"].target_id == painter_row.id
    assert by_key["typing"].fields["tax_year"].value == 2027  # the year paid

    run(ws, proposals.apply_proposal, ready.id, _accept_all(ready), iban_key=TEST_IBAN_KEY)
    links = {(k.target_type, k.target_id) for k in read(ws, list_links, proof.id)}
    assert links == {(LinkTarget.DOCUMENT, painter.id), (LinkTarget.PROPERTY, prop)}
    assert read(ws, get_document, proof.id).typing.tax_year == 2027
    current = read(ws, list_tax_inputs, prop, kind=TaxInputKind.PRIVATE_COST)
    assert sorted((r.document_id == painter.id, r.tax_year) for r in current) == [
        (False, 2026),
        (True, 2027),
    ]
    # Only the caretaker's bill still waits for a proof.
    assert [w.params["document"] for w in _proof_warnings(read, world)] == [str(caretaker.id)]


def test_the_apply_links_only_an_offered_bill_and_moves_no_other_bills_rows(
    world, run, read, job, blob_store
):
    ws, prop = world.ws, world.property_id
    painter, _, caretaker = _two_bills(run, world, blob_store)
    proof = _upload(run, ws, blob_store, b"%PDF Transaction Summary")
    ready = _read_classified(run, job, ws, proof, _values("-595.00"))

    # A bill that was not offered (the proof's own id here) is refused.
    stray = _accept_all(ready, **{PROOF_LINK_KEY: {"target_id": str(world.property_id)}})
    with pytest.raises(ValueError, match="not offered"):
        run(ws, proposals.apply_proposal, ready.id, stray, iban_key=TEST_IBAN_KEY)

    # The other bill picked while the painter's row move is still sent: refused, so a row of
    # a bill the proof was not linked to never moves.
    other = _accept_all(ready, **{PROOF_LINK_KEY: {"target_id": str(caretaker.id)}})
    with pytest.raises(ValueError, match="not the one this proof is linked to"):
        run(ws, proposals.apply_proposal, ready.id, other, iban_key=TEST_IBAN_KEY)

    # „keine dieser Rechnungen“: the link and the move skipped, the typing saved alone.
    body = _typing_only(ready)
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)
    assert read(ws, list_links, proof.id) == []
    current = read(ws, list_tax_inputs, prop, kind=TaxInputKind.PRIVATE_COST)
    assert {r.tax_year for r in current} == {2026}


def test_the_bills_todo_offers_a_stored_proof_that_fits_and_links_it(
    world, run, read, job, blob_store
):
    ws = world.ws
    painter, _, caretaker = _two_bills(run, world, blob_store)
    proof = _upload(run, ws, blob_store, b"%PDF Transaction Summary")
    ready = _read_classified(run, job, ws, proof, _values("595.00"))
    body = _typing_only(ready)
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)

    warnings = {w.params["document"]: w.params for w in _proof_warnings(read, world)}
    assert warnings[str(painter.id)]["proof_offer"] == str(proof.id)
    assert warnings[str(painter.id)]["proof_offer_amount"] == "595.00"
    assert "proof_offer" not in warnings[str(caretaker.id)]  # 595.00 does not pay 357.00

    run(ws, attach_payment_proof, proof.id, painter.id)
    assert [w.params["document"] for w in _proof_warnings(read, world)] == [str(caretaker.id)]
    assert Decimal(warnings[str(painter.id)]["amount"]) == Decimal("595.00")


# --- Review F1: the year move after a link made later (D149) -----------------------------


def _current(read, ws, proof):
    """The proof's newest proposal (the one its page shows)."""
    return read(ws, proposals.list_document_proposals, proof.id)[0]


def _private_years(read, world, bill):
    rows = read(world.ws, list_tax_inputs, world.property_id, kind=TaxInputKind.PRIVATE_COST)
    return sorted(r.tax_year for r in rows if r.document_id == bill.id)


def test_a_stored_proof_linked_from_the_todo_proposes_the_year_paid(
    world, run, read, job, blob_store
):
    ws = world.ws
    painter, _, _ = _two_bills(run, world, blob_store)
    proof = _upload(run, ws, blob_store, b"%PDF Transaction Summary")
    ready = _read_classified(run, job, ws, proof, _values("595.00"))
    run(ws, proposals.apply_proposal, ready.id, _typing_only(ready), iban_key=TEST_IBAN_KEY)

    # „Vorhandenen Nachweis zuordnen“: linked, and the move waits as a suggestion.
    run(ws, proposals.attach_stored_proof, proof.id, painter.id)
    assert _private_years(read, world, painter) == [2026]  # nothing moved yet
    proposal = _current(read, ws, proof)
    assert proposal.status is ProposalStatus.READY
    assert [r.key for r in proposal.records] == ["private_cost_year:1"]
    (move,) = proposal.records
    assert move.fields["tax_year"].value == 2027
    assert move.notes[0].startswith("Zahlungsnachweis für:")
    assert proposal.extraction_id is not None  # rebuilt from the stored reading

    run(ws, proposals.apply_proposal, proposal.id, _accept_all(proposal), iban_key=TEST_IBAN_KEY)
    assert _private_years(read, world, painter) == [2027]


def test_a_bill_picked_among_two_that_fit_proposes_the_year_paid(world, run, read, job, blob_store):
    ws = world.ws
    first, _ = _bill(run, world, blob_store, data=b"%PDF Maler A", amounts=("595.00",))
    second, _ = _bill(run, world, blob_store, data=b"%PDF Maler B", amounts=("595.00",))
    proof = _upload(run, ws, blob_store, b"%PDF Transaction Summary")
    ready = _read_classified(run, job, ws, proof, _values("595.00"))
    # Two bills fit: none pre-selected, so no move was built.
    assert {r.key for r in ready.records} == {"typing", PROOF_LINK_KEY}

    body = _accept_all(ready, **{PROOF_LINK_KEY: {"target_id": str(second.id)}})
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)
    proposal = _current(read, ws, proof)
    assert proposal.status is ProposalStatus.READY
    (move,) = proposal.records
    assert move.key == "private_cost_year:1" and move.fields["tax_year"].value == 2027

    run(ws, proposals.apply_proposal, proposal.id, _accept_all(proposal), iban_key=TEST_IBAN_KEY)
    assert _private_years(read, world, second) == [2027]
    assert _private_years(read, world, first) == [2026]


def test_no_suggestion_when_the_years_agree_or_the_move_was_already_proposed(
    world, run, read, job, blob_store
):
    ws = world.ws
    painter, _, _ = _two_bills(run, world, blob_store)
    # Paid in the bill's own year: linking proposes nothing.
    proof = _upload(run, ws, blob_store, b"%PDF Transaction Summary 2026")
    ready = _read_classified(run, job, ws, proof, _values("595.00", paid_on="2026-12-01"))
    run(ws, proposals.apply_proposal, ready.id, _typing_only(ready), iban_key=TEST_IBAN_KEY)
    run(ws, proposals.attach_stored_proof, proof.id, painter.id)
    assert _current(read, ws, proof).status is ProposalStatus.APPLIED

    # The pre-selected bill picked with its move declined: not asked again.
    other = _upload(run, ws, blob_store, b"%PDF Transaction Summary 2027")
    painter2, _ = _bill(run, world, blob_store, data=b"%PDF Maler 2", amounts=("812.00",))
    ready = _read_classified(run, job, ws, other, _values("812.00"))
    assert "private_cost_year:1" in {r.key for r in ready.records}
    full = _accept_all(ready)
    declined = [r.model_copy(update={"skip": r.key == "private_cost_year:1"}) for r in full.records]
    run(
        ws,
        proposals.apply_proposal,
        ready.id,
        ProposalApplyBody(records=declined),
        iban_key=TEST_IBAN_KEY,
    )
    assert _current(read, ws, other).status is ProposalStatus.APPLIED
    assert _private_years(read, world, painter2) == [2026]


def test_a_move_sent_with_the_link_skipped_is_refused(world, run, read, job, blob_store):
    ws = world.ws
    _two_bills(run, world, blob_store)
    proof = _upload(run, ws, blob_store, b"%PDF Transaction Summary")
    ready = _read_classified(run, job, ws, proof, _values("595.00"))
    full = _accept_all(ready)
    body = ProposalApplyBody(
        records=[r.model_copy(update={"skip": r.key == PROOF_LINK_KEY}) for r in full.records]
    )
    with pytest.raises(ValueError, match="not the one this proof is linked to"):
        run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)


def test_an_untyped_document_is_not_a_stored_proof(world, run, read, blob_store):
    ws = world.ws
    painter, _, _ = _two_bills(run, world, blob_store)
    untyped = _upload(run, ws, blob_store, b"%PDF irgendein Dokument")
    with pytest.raises(NotAPaymentProofError):
        run(ws, proposals.attach_stored_proof, untyped.id, painter.id)
    assert read(ws, list_links, untyped.id) == []
