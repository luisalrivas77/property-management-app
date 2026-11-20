"""Dokumente by section and the search over HTTP (phase 10 D282): `Document.section` from what a
document is and what relies on it, `GET /documents?section=&q=`."""

import pytest
from api_support import create, new_property, ok, upload

pytestmark = pytest.mark.db


def _typed(client, data: bytes, filename: str, typing: dict, fields: dict | None = None) -> dict:
    document = ok(upload(client, data, filename=filename), 201)
    body: dict = {"typing": typing}
    if fields is not None:
        body |= {"fields": fields, "shown_version": 0}
    return ok(client.put(f"/api/documents/{document['id']}/typing", json=body))


def _sections(client, **params) -> dict[str, str]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return {d["id"]: d["section"] for d in ok(client.get(f"/api/documents?{query}"))}


def test_sections_from_typing_and_links(client_a):
    untyped = ok(upload(client_a, b"%PDF-1.7 a", filename="scan.pdf"), 201)
    assert untyped["section"] == "other"

    payslip = _typed(client_a, b"%PDF-1.7 b", "lohn.pdf", {"type": "gehaltsabrechnung"})
    statement = _typed(client_a, b"%PDF-1.7 c", "konto.pdf", {"type": "statement"})
    depot = _typed(
        client_a, b"%PDF-1.7 d", "depot.pdf", {"type": "statement", "subtype": "depot_statement"}
    )
    lease = _typed(client_a, b"%PDF-1.7 e", "mv.pdf", {"type": "contract", "subtype": "lease"})
    invoice = _typed(client_a, b"%PDF-1.7 f", "rechnung.pdf", {"type": "invoice"})
    assert (payslip["section"], statement["section"], depot["section"], lease["section"]) == (
        "tax",
        "accounts",
        "investments",
        "property",
    )
    assert invoice["section"] == "other"  # a plain invoice: its links decide

    # A Bescheid that is a Nebenkosten row's Beleg (a Grunderwerbsteuerbescheid) is Immobilie
    # even when it is also linked to an owner, which alone would make it Steuern.
    bescheid = _typed(client_a, b"%PDF-1.7 g", "grest.pdf", {"type": "bescheid"})
    owner = create(client_a, "/api/owners", {"full_name": "Person A"})
    create(
        client_a,
        f"/api/documents/{bescheid['id']}/links",
        {"target_type": "owner", "target_id": owner["id"]},
    )
    assert _sections(client_a)[bescheid["id"]] == "tax"
    prop = new_property(client_a)
    create(
        client_a,
        "/api/acquisition-costs",
        {
            "property_id": prop["id"],
            "kind": "real_estate_transfer_tax",
            "amount": "11374.00",
            "document_id": bescheid["id"],
        },
    )
    sections = _sections(client_a)
    assert sections[bescheid["id"]] == "property"
    assert ok(client_a.get(f"/api/documents/{bescheid['id']}"))["document"]["section"] == "property"

    # The invoice moves to Immobilie once it is linked to the property.
    create(
        client_a,
        f"/api/documents/{invoice['id']}/links",
        {"target_type": "property", "target_id": prop["id"]},
    )
    assert _sections(client_a)[invoice["id"]] == "property"

    assert set(_sections(client_a, section="tax")) == {payslip["id"]}
    assert set(_sections(client_a, section="other")) == {untyped["id"]}
    assert set(_sections(client_a, section="investments")) == {depot["id"]}
    response = client_a.get("/api/documents?section=nope")
    assert response.status_code == 422


def test_search_over_title_issuer_amount_and_labels(client_a):
    handover = _typed(
        client_a,
        b"%PDF-1.7 h",
        "scan_0042.pdf",
        {"type": "contract", "subtype": "handover_protocol", "title": "Übergabeprotokoll WE 3"},
    )
    bill = _typed(
        client_a,
        b"%PDF-1.7 i",
        "rechnung.pdf",
        {"type": "invoice", "title": "Schlussrechnung", "document_date": "2026-03-14"},
        {"amount_total": "1234.50", "counterparty": "Malerbetrieb Müller"},
    )

    def found(q: str, **params) -> set[str]:
        return set(_sections(client_a, q=q, **params))

    assert found("uebergabe") == {handover["id"]}
    assert found("ÜBERGABE") == {handover["id"]}
    assert found("handover") == {handover["id"]}  # the English label
    assert found("scan_0042") == {handover["id"]}  # the file name
    assert found("mueller") == {bill["id"]}  # the issuer
    assert found("1.234,50") == {bill["id"]}  # the amount
    assert found("14.03.2026") == {bill["id"]}  # the date
    assert found("rechnung") == {bill["id"]}
    assert found("rechnung", section="property") == set()
    assert found("nichts") == set()
