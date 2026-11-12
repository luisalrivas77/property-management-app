"""GET and POST /api/tenancies/{id}/flat-rate-letter (phase-4.md D155 (7)) over HTTP: the
figures of a Grundsteuer rise from two confirmed schedules, the letter as a downloaded PDF,
flat_rate.no_letter without a rise, and workspace isolation. Synthetic data."""

import io
from uuid import uuid4

import pytest
from api_support import create, new_property, new_tenancy, new_tenant, new_unit, ok, upload
from pypdf import PdfReader
from test_tax_inputs import sessions as sessions  # noqa: F401 (users exist: recorded_by FK)

pytestmark = pytest.mark.db


def _schedule(pid: str, doc: str, year: int, quarter: str) -> dict:
    return {
        "property_id": pid,
        "kind": "grundsteuer_schedule",
        "tax_year": year,
        "document_id": doc,
        "value": {
            "annual_amount": f"{4 * int(quarter)}.00",
            "instalments": [
                {"due_on": f"{year}-{m}-15", "amount": f"{quarter}.00"}
                for m in ("02", "05", "08", "11")
            ],
            "rhythm": "quarterly",
        },
    }


def _flat_rate(client, *, schedules: bool = True) -> str:
    pid = new_property(client)["id"]  # bought in 2024
    unit = new_unit(client, pid, label="Zimmer 1")
    tenant = new_tenant(client)
    tenancy = new_tenancy(client, unit["id"], tenant["id"], operating_costs_terms="flat_rate")
    create(
        client,
        "/api/rent-schedule",
        {
            "tenancy_id": tenancy["id"],
            "effective_from": "2024-07-01",
            "cold_rent": "400.00",
            "operating_costs_prepayment": "100.00",
            "reason": "initial",
        },
    )
    if schedules:
        doc = ok(upload(client, b"%PDF-1.7 Grundsteuerbescheid", filename="gst.pdf"), 201)["id"]
        path = f"/api/properties/{pid}/tax-inputs"
        create(client, path, _schedule(pid, doc, 2024, "120"))  # 40.00 a month
        create(client, path, _schedule(pid, doc, 2025, "186"))  # 62.00 a month: +22.00
    return tenancy["id"]


def test_the_figures_and_the_pdf(client_a):
    tenancy_id = _flat_rate(client_a)
    path = f"/api/tenancies/{tenancy_id}/flat-rate-letter"
    got = ok(client_a.get(path, params={"sent_on": "2030-01-15"}))
    assert (got["kind"], got["rise"], got["new_pauschale"], got["new_total"]) == (
        "raise",
        "22.00",
        "122.00",
        "522.00",
    )
    assert (got["sent_on"], got["starts_on"]) == ("2030-01-15", "2030-03-01")
    assert [r["category"] for r in got["reasons"]] == ["grundsteuer"]

    response = client_a.post(
        path,
        json={"sent_on": "2030-01-15", "sender_name": "Eigentümer A", "sender_address": ["X 1"]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["cache-control"] == "private, no-store"
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages)
    assert "122,00 €" in text and "01.03.2030" in text and "Anna Beispiel" in text


def test_no_letter_without_a_rise(client_a):
    tenancy_id = _flat_rate(client_a, schedules=False)
    response = client_a.get(f"/api/tenancies/{tenancy_id}/flat-rate-letter")
    assert (response.status_code, response.json()["code"]) == (422, "flat_rate.no_letter")


def test_another_workspace_sees_nothing(client_a, client_b):
    tenancy_id = _flat_rate(client_a)
    response = client_b.get(f"/api/tenancies/{tenancy_id}/flat-rate-letter")
    assert (response.status_code, response.json()["entity_type"]) == (404, "tenancy")
    response = client_b.post(
        f"/api/tenancies/{tenancy_id}/flat-rate-letter",
        json={"sent_on": "2030-01-15", "sender_name": "B"},
    )
    assert response.status_code == 404
    assert client_a.get(f"/api/tenancies/{uuid4()}/flat-rate-letter").status_code == 404
