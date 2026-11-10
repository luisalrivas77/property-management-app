"""The `grundsteuer_schedule` tax input over POST /api/properties/{id}/tax-inputs (phase 5
D126, §9, §10): recorded and superseded like every tax input, and refused with
tax_input.grundsteuer_schedule_invalid when its instalments do not fit. Synthetic data."""

import pytest
from api_support import create, new_property, ok, upload
from test_tax_inputs import sessions as sessions  # noqa: F401 (users exist: recorded_by FK)

pytestmark = pytest.mark.db

QUARTERS = [
    {"due_on": "2027-02-15", "amount": "100.00"},
    {"due_on": "2027-05-15", "amount": "100.00"},
    {"due_on": "2027-08-15", "amount": "100.00"},
    {"due_on": "2027-11-15", "amount": "100.00"},
]


def _path(property_id: str) -> str:
    return f"/api/properties/{property_id}/tax-inputs"


def _body(pid: str, doc: str, **value) -> dict:
    return {
        "property_id": pid,
        "kind": "grundsteuer_schedule",
        "tax_year": 2027,
        "document_id": doc,
        "value": {"annual_amount": "400.00", "instalments": QUARTERS, "rhythm": "quarterly"}
        | value,
    }


def test_a_schedule_is_recorded_and_superseded(client_a):
    pid = new_property(client_a)["id"]
    doc = ok(upload(client_a, b"%PDF-1.7 Grundsteuerbescheid", filename="gst.pdf"), 201)["id"]
    first = create(client_a, _path(pid), _body(pid, doc))
    assert first["value"]["instalments"] == QUARTERS
    assert first["value"]["payment_mode"] is None
    # A second answer for the same first year must supersede the first.
    response = client_a.post(_path(pid), json=_body(pid, doc, payment_mode="sepa_mandate"))
    assert (response.status_code, response.json()["code"]) == (422, "tax_input.not_current")
    second = create(
        client_a,
        _path(pid),
        _body(pid, doc, payment_mode="sepa_mandate") | {"supersedes_id": first["id"]},
    )
    listed = ok(client_a.get(_path(pid), params={"kind": "grundsteuer_schedule"}))
    assert listed == [second]
    assert second["value"]["payment_mode"] == "sepa_mandate"


@pytest.mark.parametrize(
    "wrong",
    [
        {"annual_amount": "400.05"},  # 5 cents off the instalments' sum
        {"instalments": [*QUARTERS[:3], {"due_on": "2028-11-15", "amount": "100.00"}]},
        {"instalments": list(reversed(QUARTERS))},
        {"instalments": [{"due_on": "2026-02-15", "amount": "400.00"}]},  # not the row's year
    ],
)
def test_an_invalid_schedule_is_refused(client_a, wrong):
    pid = new_property(client_a)["id"]
    doc = ok(upload(client_a, b"%PDF-1.7 Grundsteuerbescheid", filename="gst.pdf"), 201)["id"]
    response = client_a.post(_path(pid), json=_body(pid, doc, **wrong))
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation"
    assert "tax_input.grundsteuer_schedule_invalid" in str(body["detail"])
    assert ok(client_a.get(_path(pid))) == []


@pytest.mark.parametrize(
    "wrong", [{"payment_mode": "lastschrift"}, {"rhythm": "monthly"}, {"instalments": []}]
)
def test_mode_and_rhythm_are_checked(client_a, wrong):
    pid = new_property(client_a)["id"]
    doc = ok(upload(client_a, b"%PDF-1.7 Grundsteuerbescheid", filename="gst.pdf"), 201)["id"]
    response = client_a.post(_path(pid), json=_body(pid, doc, **wrong))
    assert (response.status_code, response.json()["error"]) == (422, "validation")


def test_a_schedule_needs_its_bescheid(client_a):
    pid = new_property(client_a)["id"]
    body = _body(pid, "x")
    del body["document_id"]
    response = client_a.post(_path(pid), json=body)
    assert (response.status_code, response.json()["error"]) == (422, "validation")
