"""GET /api/properties/{id}/checklist (spec amendment 3): the document checklist over HTTP,
its years, its errors, and that it carries no tenant data (D5)."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from api_support import create, flat, ok, upload

pytestmark = pytest.mark.db


@pytest.fixture
def let_flat(client_a) -> dict:
    setup = flat(client_a)
    contract = ok(upload(client_a, b"%PDF-1.7 Kaufvertrag checklist"), 201)
    ok(
        client_a.put(
            f"/api/documents/{contract['id']}/typing",
            json={
                "typing": {
                    "type": "contract",
                    "subtype": "purchase_contract",
                    "title": "Kaufvertrag Lindenstraße",
                }
            },
        )
    )
    create(
        client_a,
        f"/api/documents/{contract['id']}/links",
        {"target_type": "property", "target_id": setup["property"]["id"]},
    )
    return setup | {"contract": contract}


def _checklist(client, property_id, **params):
    return client.get(f"/api/properties/{property_id}/checklist", params=params)


def test_present_and_missing_items_for_a_year(client_a, let_flat):
    pid = let_flat["property"]["id"]
    body = ok(_checklist(client_a, pid, tax_year=2025))
    assert body["property_id"] == pid
    assert body["tax_year"] == 2025
    assert body["available_years"][:2] == [2024, 2025]
    by_code = {}
    for item in body["items"]:
        by_code.setdefault(item["code"], item)
    contract = by_code["purchase_contract"]
    assert contract["status"] == "present"
    assert contract["scope"] == {
        "type": "property",
        "id": pid,
        "lender": None,
        "unit_label": None,
        "tenancy_start": None,
        "cost_kind": None,
        "cost_amount": None,
    }
    [ref] = contract["documents"]
    assert ref["id"] == let_flat["contract"]["id"]
    assert ref["subtype"] == "purchase_contract"
    assert set(ref) == {"id", "type", "subtype", "tax_year", "document_date", "received_at"}
    lease = by_code["lease"]
    assert lease["status"] == "missing"
    assert lease["documents"] == []
    assert lease["scope"]["id"] == let_flat["tenancy"]["id"]
    assert lease["scope"]["unit_label"] == "WE 3"
    assert lease["scope"]["tenancy_start"] == "2024-07-01"
    assert by_code["price_split"]["status"] == "missing"


def test_no_tenant_data_and_no_titles(client_a, let_flat):
    """D5: neither the tenant's name nor any document title reaches the checklist."""
    response = _checklist(client_a, let_flat["property"]["id"], tax_year=2025)
    ok(response)
    assert "Anna Beispiel" not in response.text
    assert let_flat["tenant"]["id"] not in response.text
    assert "Kaufvertrag Lindenstraße" not in response.text


def test_the_year_defaults_to_the_current_year(client_a, let_flat):
    this_year = datetime.now(ZoneInfo("Europe/Berlin")).year
    body = ok(_checklist(client_a, let_flat["property"]["id"]))
    assert body["tax_year"] == this_year
    assert body["available_years"] == list(range(2024, this_year + 1))


def test_a_year_outside_the_available_years_is_422(client_a, let_flat):
    response = _checklist(client_a, let_flat["property"]["id"], tax_year=2023)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid"
    # Outside the TaxYear range: refused by the query's own validation.
    response = _checklist(client_a, let_flat["property"]["id"], tax_year=1999)
    assert response.status_code == 422
    assert response.json()["error"] == "validation"


def test_an_unknown_property_is_404(client_a):
    response = _checklist(client_a, str(uuid4()))
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
