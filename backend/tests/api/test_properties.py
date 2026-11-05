"""Properties over HTTP (§4.2): the property, its corrections, ownership, Nebenkosten,
building/land splits, and the units and loans read from the property."""

from uuid import uuid4

import pytest
from api_support import create, loan_body, new_property, new_unit, ok, property_body

pytestmark = pytest.mark.db


def test_round_trip_and_money_as_strings(client_a):
    prop = new_property(client_a)
    assert prop["purchase_price"] == "289000.00"
    assert isinstance(prop["purchase_price"], str)
    assert prop["address"]["postal_code"] == "10115"
    assert ok(client_a.get(f"/api/properties/{prop['id']}")) == prop
    assert ok(client_a.get("/api/properties")) == [prop]

    patched = ok(
        client_a.patch(f"/api/properties/{prop['id']}", json={"label": "Lindenstraße 12, WE 3"})
    )
    assert patched["label"] == "Lindenstraße 12, WE 3"
    assert patched["notarized_on"] == prop["notarized_on"]


def test_a_float_price_is_refused(client_a):
    response = client_a.post("/api/properties", json=property_body(purchase_price=289000.0))
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "purchase_price"]


def test_milestone_dates_change_only_through_corrections(client_a):
    prop = new_property(client_a)
    response = client_a.patch(f"/api/properties/{prop['id']}", json={"purchase_date": "2024-06-01"})
    assert response.status_code == 422  # not a PropertyPatch field (extra="forbid")

    corrected = ok(
        client_a.post(
            f"/api/properties/{prop['id']}/corrections/purchase-date",
            json={"new_date": "2024-05-02"},
        )
    )
    assert corrected["purchase_date"] == "2024-05-02"
    corrected = ok(
        client_a.post(
            f"/api/properties/{prop['id']}/corrections/notarized-on",
            json={"new_date": "2024-03-14", "purchase_price": "290000.00"},
        )
    )
    assert (corrected["notarized_on"], corrected["purchase_price"]) == ("2024-03-14", "290000.00")

    trail = ok(client_a.get(f"/api/properties/{prop['id']}/events"))
    possession = [e for e in trail if e["type"] == "property.possession_transferred"]
    assert [e["effective_date"] for e in possession] == ["2024-05-01", "2024-05-02"]
    assert possession[1]["corrects_event_id"] == possession[0]["id"]


def test_a_correction_before_the_notarization_is_a_validation_error(client_a):
    """The service revalidates the whole property: a pydantic ValidationError -> 422."""
    prop = new_property(client_a)
    response = client_a.post(
        f"/api/properties/{prop['id']}/corrections/purchase-date",
        json={"new_date": "2024-01-01"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation"
    assert body["detail"][0]["msg"].endswith("purchase_date must not be before notarized_on")


def test_ownership(client_a):
    prop = new_property(client_a)
    luis = create(client_a, "/api/owners", {"full_name": "Luis"})
    spouse = create(client_a, "/api/owners", {"full_name": "Spouse"})
    path = f"/api/properties/{prop['id']}/ownership"

    response = client_a.get(path)
    assert (response.status_code, response.json()["entity_type"]) == (404, "property_ownership")

    half = {"numerator": 1, "denominator": 2}
    shares = [{"owner_id": luis["id"], "share": half}, {"owner_id": spouse["id"], "share": half}]
    ownership = ok(client_a.put(path, json={"shares": shares, "effective_date": "2024-05-01"}))
    # PUT answers in the request's order, GET in owner-id order (the services' behaviour).
    by_owner = sorted(ownership["shares"], key=lambda s: s["owner_id"])
    assert ok(client_a.get(path)) == ownership | {"shares": by_owner}
    assert {s["owner_id"] for s in ownership["shares"]} == {luis["id"], spouse["id"]}

    third = {"numerator": 1, "denominator": 3}
    bad = [{"owner_id": luis["id"], "share": half}, {"owner_id": spouse["id"], "share": third}]
    response = client_a.put(path, json={"shares": bad})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "rule_violation"
    assert body["codes"] == ["ownership.shares_must_sum_to_one"]

    twice = [{"owner_id": luis["id"], "share": half}, {"owner_id": luis["id"], "share": half}]
    response = client_a.put(path, json={"shares": twice})
    assert (response.status_code, response.json()["error"]) == (422, "invalid")
    assert response.json()["message"] == "each owner may hold only one share of a property"

    unknown = [{"owner_id": str(uuid4()), "share": {"numerator": 1, "denominator": 1}}]
    response = client_a.put(path, json={"shares": unknown})
    assert (response.status_code, response.json()["entity_type"]) == (404, "owner")
    assert ok(client_a.get(path)) == ownership | {"shares": by_owner}  # nothing changed


def test_acquisition_costs_and_price_splits(client_a):
    prop = new_property(client_a)
    cost = create(
        client_a,
        "/api/acquisition-costs",
        {"property_id": prop["id"], "kind": "notary", "amount": "3120.50", "paid_on": "2024-04-02"},
    )
    assert cost["amount"] == "3120.50"
    assert ok(client_a.get(f"/api/properties/{prop['id']}/acquisition-costs")) == [cost]

    split = create(
        client_a,
        "/api/price-splits",
        {"property_id": prop["id"], "source": "purchase_contract", "building_share": "0.78"},
    )
    assert split["building_share"] == "0.780000"
    assert ok(client_a.get(f"/api/properties/{prop['id']}/price-splits")) == [split]
    selected = ok(client_a.post(f"/api/properties/{prop['id']}/price-splits/{split['id']}/select"))
    assert selected["selected_price_split_id"] == split["id"]

    other = new_property(client_a, label="Haus")
    response = client_a.post(f"/api/properties/{other['id']}/price-splits/{split['id']}/select")
    assert (response.status_code, response.json()["entity_type"]) == (404, "price_split")

    response = client_a.post(
        "/api/price-splits",
        json={"property_id": prop["id"], "source": "appraisal", "building_share": "0.7"},
    )
    assert response.status_code == 422  # an appraisal needs its Gutachten

    response = client_a.post(
        "/api/acquisition-costs",
        json={"property_id": str(uuid4()), "kind": "notary", "amount": "1.00"},
    )
    assert (response.status_code, response.json()["entity_type"]) == (404, "property")


def test_units_and_loans_are_read_from_the_property(client_a):
    prop = new_property(client_a)
    unit = new_unit(client_a, prop["id"])
    loan = create(client_a, "/api/loans", loan_body(prop["id"]))
    assert ok(client_a.get(f"/api/properties/{prop['id']}/units")) == [unit]
    assert ok(client_a.get(f"/api/properties/{prop['id']}/loans")) == [loan]
    for sub in ("units", "loans", "acquisition-costs", "price-splits", "events", "history"):
        response = client_a.get(f"/api/properties/{uuid4()}/{sub}")
        assert (response.status_code, response.json()["entity_type"]) == (404, "property"), sub


def test_unit_label_conflict_and_patch(client_a):
    prop = new_property(client_a)
    unit = new_unit(client_a, prop["id"], rooms="2.5", mea={"numerator": 85, "denominator": 1000})
    assert (unit["rooms"], unit["floor_area_m2"]) == ("2.5", "68.40")
    assert ok(client_a.get(f"/api/units/{unit['id']}")) == unit

    response = client_a.post(
        "/api/units", json={"property_id": prop["id"], "label": "WE 3", "floor_area_m2": "10.00"}
    )
    assert response.status_code == 409
    body = response.json()
    assert (body["error"], body["constraint"]) == ("conflict", "unit_property_id_label_key")

    patched = ok(client_a.patch(f"/api/units/{unit['id']}", json={"rooms": None, "label": "WE 3a"}))
    assert (patched["rooms"], patched["label"], patched["mea"]) == (None, "WE 3a", unit["mea"])
    assert ok(client_a.get(f"/api/units/{unit['id']}/tenancies")) == []
    response = client_a.get(f"/api/units/{uuid4()}/tenancies")
    assert (response.status_code, response.json()["entity_type"]) == (404, "unit")
