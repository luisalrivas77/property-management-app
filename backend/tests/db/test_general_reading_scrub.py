"""The general reading's scrub through the job and the apply (docs/phases/phase-2.md D131,
decided 10 Nov 2026; PR #135 review): a Vorauszahlungsbescheid, a tenant's Selbstauskunft and
an insurance policy, each value citing its whole page, leave no Steuernummer and no home
address in the extraction, the ai_call output and raw text, the proposal or the events. Only
the workspace's own property address stays, where the document prints it, and the tenant's
income (tenant data, scrubbed with the proposal after 90 days, D19). An address the model
reads that is no property's links nothing.

All names, numbers and addresses are invented."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.test_jobs_document_read import _allow_tenant_documents, _classify, _pdf, _run
from db.test_jobs_document_read import setup as setup  # noqa: F401 (a fixture)
from db.test_services_proposals_return_inputs import _apply, _everything_stored

from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.schemas.enums import PropertyKind, ProposalStatus
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.services import proposals
from hausbuch.services.properties import create_property

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

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

INSURANCE_PAGE = "\n".join(
    [
        "Wohngebäudeversicherung – Versicherungsschein",
        "Versicherungsnehmer: Jonas und Lea Beispielmann, Beispielallee 4, 34117 Kassel",
        "Versichertes Objekt: Lindenstraße 7, 10115 Berlin, WE 3",
        "Jahresbeitrag 240,00 EUR",
        "Musterstadt, den 02.01.2027",
    ]
)
# The model reads the Versicherungsnehmer's home as the property.
INSURANCE_ANSWER = {
    "title": ("Versicherungsschein Wohngebäude Beispielallee 4", 1, INSURANCE_PAGE),
    "document_date": ("02.01.2027", 1, INSURANCE_PAGE),
    "street": ("Beispielallee", 1, INSURANCE_PAGE),
    "house_number": ("4", 1, INSURANCE_PAGE),
    "postal_code": ("34117", 1, INSURANCE_PAGE),
}
INSURANCE_FORBIDDEN = ("Beispielallee", "34117", "Kassel")


def _flat(run, ws):
    """The workspace's flat, Lindenstraße 7, 10115 Berlin: the only address a reading keeps."""
    data = PropertyCreate(
        label="Wohnung Lindenstraße",
        kind=PropertyKind.ETW,
        address=Address(
            street="Lindenstraße", house_number="7", postal_code="10115", city="Berlin"
        ),
        build_year=1994,
        notarized_on=date(2026, 3, 10),
        purchase_date=date(2026, 5, 1),
        purchase_price=Decimal("289000.00"),
    )
    return run(ws, create_property, data, now=NOW)


def test_a_vorauszahlungsbescheid_stores_no_steuernummer_or_home_address(setup, run, read):
    client = FakeModelClient(
        [
            text_response(_classify("bescheid", "prepayment_notice")),
            cited_response(PREPAYMENT_ANSWER),
        ]
    )
    deps, ws, doc, proposal_id = setup(_pdf(1), client)
    _flat(run, ws)
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
    flat = _flat(run, ws)
    _allow_tenant_documents(app_engine, ws)
    _run(deps, ws, doc, proposal_id)
    ready = read(ws, proposals.get_proposal, proposal_id)
    assert ready.status is ProposalStatus.READY
    # The tenancy is linked by hand (review, F3); the flat by its address.
    links = {r.key: r for r in ready.records if r.key.startswith("link:")}
    assert set(links) == {"link:property"} and links["link:property"].target_id == flat.id
    _apply(run, ws, ready)

    stored = _everything_stored(read, ws)
    assert "Lindenstraße 7, 10115 Berlin" in stored  # the flat it links by
    assert "street: Lindenstraße" in stored  # the asked link line in the raw text
    assert "2.400,00 EUR" in stored  # tenant data under the switch (D19)
    for fragment in SELF_DISCLOSURE_FORBIDDEN:
        assert fragment not in stored, fragment


def test_a_home_address_read_as_the_insured_property_is_stored_nowhere(setup, run, read):
    client = FakeModelClient(
        [
            text_response(_classify("contract", "insurance_policy")),
            cited_response(INSURANCE_ANSWER),
        ]
    )
    deps, ws, doc, proposal_id = setup(_pdf(1), client)
    _flat(run, ws)
    _run(deps, ws, doc, proposal_id)
    ready = read(ws, proposals.get_proposal, proposal_id)
    assert ready.status is ProposalStatus.READY
    # The home is no property: nothing links, not even the workspace's only property.
    assert [r.key for r in ready.records] == ["typing"]
    assert any("kein Objekt gefunden" in note for note in ready.records[0].notes)
    _apply(run, ws, ready)

    stored = _everything_stored(read, ws)
    assert "Versicherungsschein" in stored and "240,00 EUR" in stored  # sanity
    assert "Lindenstraße 7, 10115 Berlin" in stored  # the flat, where the page prints it
    for fragment in INSURANCE_FORBIDDEN:
        assert fragment not in stored, fragment
