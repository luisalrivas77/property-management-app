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
from hausbuch.services.documents import attach_payment_proof, get_document, list_links
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
