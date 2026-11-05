"""Workspace isolation through the API (§3.1, §10.1): the workspace comes from the session
only, so another household's ids answer 404 on every read and every write, and its lists
are empty."""

import pytest
from api_support import create, flat, loan_body, ok, upload

pytestmark = pytest.mark.db


@pytest.fixture
def entities_of_a(client_a) -> dict[str, str]:
    setup = flat(client_a)
    owner = create(client_a, "/api/owners", {"full_name": "Luis"})
    whole = {"owner_id": owner["id"], "share": {"numerator": 1, "denominator": 1}}
    ok(
        client_a.put(
            f"/api/properties/{setup['property']['id']}/ownership", json={"shares": [whole]}
        )
    )
    loan = create(client_a, "/api/loans", loan_body(setup["property"]["id"]))
    document = ok(upload(client_a), 201)
    split = create(
        client_a,
        "/api/price-splits",
        {
            "property_id": setup["property"]["id"],
            "source": "purchase_contract",
            "building_share": "0.8",
        },
    )
    row = create(
        client_a,
        "/api/rent-schedule",
        {
            "tenancy_id": setup["tenancy"]["id"],
            "effective_from": "2024-07-01",
            "cold_rent": "950.00",
            "operating_costs_prepayment": "220.00",
            "reason": "initial",
        },
    )
    create(
        client_a,
        f"/api/documents/{document['id']}/links",
        {"target_type": "property", "target_id": setup["property"]["id"]},
    )
    return {
        "owner": owner["id"],
        "property": setup["property"]["id"],
        "unit": setup["unit"]["id"],
        "tenant": setup["tenant"]["id"],
        "tenancy": setup["tenancy"]["id"],
        "loan": loan["id"],
        "document": document["id"],
        "split": split["id"],
        "row": row["id"],
    }


READS = [
    "/api/owners/{owner}",
    "/api/properties/{property}",
    "/api/properties/{property}/ownership",
    "/api/properties/{property}/acquisition-costs",
    "/api/properties/{property}/price-splits",
    "/api/properties/{property}/units",
    "/api/properties/{property}/loans",
    "/api/properties/{property}/history",
    "/api/properties/{property}/events",
    "/api/units/{unit}",
    "/api/units/{unit}/tenancies",
    "/api/tenants/{tenant}",
    "/api/tenants/{tenant}/documents",
    "/api/tenancies/{tenancy}",
    "/api/tenancies/{tenancy}/rent-schedule",
    "/api/tenancies/{tenancy}/rent?at=2025-01-01",
    "/api/tenancies/{tenancy}/documents",
    "/api/loans/{loan}",
    "/api/loans/{loan}/schedule",
    "/api/loans/{loan}/documents",
    "/api/documents/{document}",
    "/api/documents/{document}/links",
    "/api/documents/{document}/original",
    "/api/documents/{document}/retention",
]

WRITES = [
    ("PATCH", "/api/owners/{owner}", {"full_name": "x"}),
    ("PATCH", "/api/properties/{property}", {"label": "x"}),
    (
        "PUT",
        "/api/properties/{property}/ownership",
        {"shares": [{"owner_id": "{owner}", "share": {"numerator": 1, "denominator": 1}}]},
    ),
    ("POST", "/api/properties/{property}/corrections/purchase-date", {"new_date": "2024-05-02"}),
    ("POST", "/api/properties/{property}/corrections/notarized-on", {"new_date": "2024-03-14"}),
    ("POST", "/api/properties/{property}/price-splits/{split}/select", None),
    (
        "POST",
        "/api/acquisition-costs",
        {"property_id": "{property}", "kind": "notary", "amount": "1.00"},
    ),
    ("POST", "/api/units", {"property_id": "{property}", "label": "WE 9", "floor_area_m2": "1.00"}),
    ("PATCH", "/api/units/{unit}", {"label": "x"}),
    ("PATCH", "/api/tenants/{tenant}", {"phone": "1"}),
    ("POST", "/api/tenants/{tenant}/erase", {}),
    ("POST", "/api/tenancies/{tenancy}/corrections/move-in", {"new_date": "2024-07-02"}),
    (
        "POST",
        "/api/tenancies/{tenancy}/deposit-received",
        {"amount": "1.00", "received_on": "2024-07-01"},
    ),
    ("POST", "/api/tenancies/{tenancy}/end", {"end_date": "2026-01-01"}),
    ("POST", "/api/rent-schedule/{row}/corrections", {"cold_rent": "1.00"}),
    ("PATCH", "/api/loans/{loan}", {"lender": "x"}),
    ("POST", "/api/loans", {"$loan_body": "{property}"}),
    ("PUT", "/api/documents/{document}/typing", {"typing": {"type": "other"}}),
    (
        "POST",
        "/api/documents/{document}/links",
        {"target_type": "property", "target_id": "{property}"},
    ),
    ("DELETE", "/api/documents/{document}/links/property/{property}", None),
]


def _fill(value, ids):
    if isinstance(value, str):
        return value.format(**ids)
    if isinstance(value, dict):
        if "$loan_body" in value:
            return loan_body(_fill(value["$loan_body"], ids))
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


def test_reads_of_another_workspace_are_404(entities_of_a, client_a, client_b):
    for template in READS:
        path = template.format(**entities_of_a)
        assert client_a.get(path).status_code == 200, path
        response = client_b.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"] == "not_found"


def test_writes_to_another_workspace_are_404_and_change_nothing(entities_of_a, client_a, client_b):
    before = ok(client_a.get("/api/events"))
    for method, template, body in WRITES:
        path = template.format(**entities_of_a)
        response = client_b.request(method, path, json=_fill(body, entities_of_a))
        assert response.status_code == 404, (method, path, response.text)
    assert ok(client_a.get("/api/events")) == before
    assert ok(client_b.get("/api/events")) == []


def test_lists_are_per_workspace(entities_of_a, client_b):
    for path in ("/api/owners", "/api/properties", "/api/tenants", "/api/documents", "/api/events"):
        assert ok(client_b.get(path)) == [], path
    assert ok(client_b.get(f"/api/events?property_id={entities_of_a['property']}")) == []


def test_uploading_the_same_bytes_in_another_workspace_is_a_new_document(entities_of_a, client_b):
    document = ok(upload(client_b), 201)
    assert document["id"] != entities_of_a["document"]
