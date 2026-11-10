"""The general reading's scrub through the job and the apply (docs/phases/phase-2.md D131,
decided 10 Nov 2026): a Vorauszahlungsbescheid and a tenant's Selbstauskunft, each value
citing its whole page, leave no Steuernummer and no home address in the extraction, the
ai_call output and raw text, the proposal or the events; the flat's address the Selbstauskunft
links by, and the tenant's income (tenant data, scrubbed with the proposal after 90 days,
D19), stay.

All names, numbers and addresses are invented."""

import pytest
from db.test_jobs_document_read import _allow_tenant_documents, _classify, _pdf, _run
from db.test_jobs_document_read import setup as setup  # noqa: F401 (a fixture)
from db.test_services_proposals_return_inputs import _apply, _everything_stored

from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.schemas.enums import ProposalStatus
from hausbuch.services import proposals

pytestmark = pytest.mark.db

PREPAYMENT_PAGE = "\n".join(
    [
        "Finanzamt Musterstadt",
        "Steuernummer 045/815/08154",
        "Datum 20.06.2027",
        "Herrn und Frau",
        "Jonas und Lea Beispielmann",
        "Beispielallee 4",
        "34117 Kassel",
        "Bescheid über Vorauszahlungen zur Einkommensteuer",
        "10.09.2027   Einkommensteuer   1.200,00 EUR",
    ]
)
PREPAYMENT_ANSWER = {
    "title": ("Vorauszahlungsbescheid Einkommensteuer", 1, PREPAYMENT_PAGE),
    "document_date": ("20.06.2027", 1, PREPAYMENT_PAGE),
    # volunteered: the request asks no address for a Vorauszahlungsbescheid
    "street": ("Beispielallee", 1, PREPAYMENT_PAGE),
    "house_number": ("4", 1, PREPAYMENT_PAGE),
    "postal_code": ("34117", 1, PREPAYMENT_PAGE),
}
PREPAYMENT_FORBIDDEN = ("045/815/08154", "08154", "Beispielallee", "34117", "Kassel")

SELF_DISCLOSURE_PAGE = "\n".join(
    [
        "Mieterselbstauskunft",
        "für die Wohnung Lindenstraße 7, 10115 Berlin, WE 3, Zimmer 1",
        "Mietinteressent: Tom Interessent",
        "Derzeitige Anschrift: Ahornweg 5, 50667 Köln",
        "Steuer-ID 12 345 678 903",
        "Monatliches Nettoeinkommen: 2.400,00 EUR",
        "Köln, den 10.07.2026",
    ]
)
SELF_DISCLOSURE_ANSWER = {
    "title": ("Mieterselbstauskunft", 1, SELF_DISCLOSURE_PAGE),
    "document_date": ("10.07.2026", 1, SELF_DISCLOSURE_PAGE),
    "street": ("Lindenstraße", 1, SELF_DISCLOSURE_PAGE),
    "house_number": ("7", 1, SELF_DISCLOSURE_PAGE),
    "postal_code": ("10115", 1, SELF_DISCLOSURE_PAGE),
}
SELF_DISCLOSURE_FORBIDDEN = ("Ahornweg", "50667", "345 678")


def test_a_vorauszahlungsbescheid_stores_no_steuernummer_or_home_address(setup, run, read):
    client = FakeModelClient(
        [
            text_response(_classify("bescheid", "prepayment_notice")),
            cited_response(PREPAYMENT_ANSWER),
        ]
    )
    deps, ws, doc, proposal_id = setup(_pdf(1), client)
    _run(deps, ws, doc, proposal_id)
    ready = read(ws, proposals.get_proposal, proposal_id)
    assert ready.status is ProposalStatus.READY
    _apply(run, ws, ready)

    stored = _everything_stored(read, ws)
    assert "Vorauszahlungsbescheid" in stored and "1.200,00 EUR" in stored  # sanity
    for fragment in PREPAYMENT_FORBIDDEN:
        assert fragment not in stored, fragment


def test_a_selbstauskunft_stores_no_home_address_and_keeps_the_flats(setup, run, read, app_engine):
    client = FakeModelClient(
        [
            text_response(_classify("other", "tenant_self_disclosure", tenant=True)),
            cited_response(SELF_DISCLOSURE_ANSWER),
        ]
    )
    deps, ws, doc, proposal_id = setup(_pdf(1), client, tenant_data=True)
    _allow_tenant_documents(app_engine, ws)
    _run(deps, ws, doc, proposal_id)
    ready = read(ws, proposals.get_proposal, proposal_id)
    assert ready.status is ProposalStatus.READY
    _apply(run, ws, ready)

    stored = _everything_stored(read, ws)
    assert "Lindenstraße 7, 10115 Berlin" in stored  # the flat it links by
    assert "street: Lindenstraße" in stored  # the asked link line in the raw text
    assert "2.400,00 EUR" in stored  # tenant data under the switch (D19)
    for fragment in SELF_DISCLOSURE_FORBIDDEN:
        assert fragment not in stored, fragment
