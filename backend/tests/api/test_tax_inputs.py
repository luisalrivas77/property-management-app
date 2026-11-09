"""/api/properties/{id}/tax-inputs (phase-3.md §10, D58) and the tenancy's D67/D72 fields
over HTTP: create, supersede, list with history, the error codes, workspace isolation."""

from uuid import uuid4

import pytest
from api_support import (
    TOKEN_A,
    TOKEN_B,
    auth_for,
    create,
    flat,
    loan_body,
    new_property,
    new_unit,
    ok,
    upload,
)
from db.db_support import _admin
from test_tax import SAMPLE_IBAN, ZIP, _confirm

pytestmark = pytest.mark.db


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so a row's recorded_by (FK to app_user) is real."""
    a, b = fresh_workspaces
    auths = {TOKEN_A: auth_for(a), TOKEN_B: auth_for(b)}
    with _admin(dbname) as conn:
        for auth in auths.values():
            conn.execute(
                "INSERT INTO app_user (id, workspace_id, email, display_name)"
                " VALUES (%s, %s, %s, 'Test')",
                (auth.user_id, auth.workspace_id, f"{auth.user_id}@example.test"),
            )
    return auths


MIETSPIEGEL = {"valid_from": "2025-08-01", "cold_rent_per_m2": "12.80", "source": "mietspiegel"}


def _path(property_id: str) -> str:
    return f"/api/properties/{property_id}/tax-inputs"


def test_a_market_rent_is_recorded_corrected_and_listed(client_a):
    pid = flat(client_a)["property"]["id"]
    first = create(
        client_a, _path(pid), {"property_id": pid, "kind": "market_rent", "value": MIETSPIEGEL}
    )
    assert first["value"] == MIETSPIEGEL
    assert (first["unit_id"], first["supersedes_id"], first["withdrawn"]) == (None, None, False)
    second = create(
        client_a,
        _path(pid),
        {
            "property_id": pid,
            "kind": "market_rent",
            "value": MIETSPIEGEL | {"cold_rent_per_m2": "11.90"},
            "supersedes_id": first["id"],
            "note": "untere Spanne",
        },
    )
    assert ok(client_a.get(_path(pid))) == [second]
    history = ok(client_a.get(_path(pid), params={"include_history": "true"}))
    assert [r["id"] for r in history] == [first["id"], second["id"]]
    assert ok(client_a.get(_path(pid), params={"kind": "spread_82b"})) == []

    events = ok(client_a.get(f"/api/events?entity_id={pid}"))
    recorded = [e for e in events if e["type"] == "property.tax_input_recorded"]
    assert [e["payload"]["tax_input_id"] for e in recorded] == [first["id"], second["id"]]
    assert "untere Spanne" not in str(recorded)


def test_the_error_codes(client_a):
    setup = flat(client_a)
    pid = setup["property"]["id"]
    body = {"property_id": pid, "kind": "pre_handover_income", "value": {"treatment": "rent"}}
    row = create(client_a, _path(pid), body)

    # Another answer to the same question: supersede it instead.
    response = client_a.post(_path(pid), json=body)
    assert (response.status_code, response.json()["code"]) == (422, "tax_input.not_current")
    # The path and the body name different properties.
    other = new_property(client_a, label="Zweite")
    response = client_a.post(_path(other["id"]), json=body)
    assert (response.status_code, response.json()["code"]) == (422, "tax_input.scope_invalid")
    # An ETW's market rent is the flat's, not a unit's.
    response = client_a.post(
        _path(pid),
        json={
            "property_id": pid,
            "kind": "market_rent",
            "unit_id": setup["unit"]["id"],
            "value": MIETSPIEGEL,
        },
    )
    assert (response.status_code, response.json()["code"]) == (422, "tax_input.scope_invalid")
    # The schema's own scope check: a value of another kind, a withdrawal without supersedes.
    for wrong in (
        body | {"value": {"years": 3}},
        body | {"withdrawn": True},
        body | {"tax_year": 2026},
    ):
        response = client_a.post(_path(pid), json=wrong)
        assert response.status_code == 422, wrong
        assert response.json()["error"] == "validation"
    # A withdrawal of the current row.
    withdrawn = create(client_a, _path(pid), body | {"supersedes_id": row["id"], "withdrawn": True})
    assert withdrawn["withdrawn"] is True
    assert ok(client_a.get(_path(pid))) == []
    response = client_a.get(_path(str(uuid4())))
    assert (response.status_code, response.json()["entity_type"]) == (404, "property")


def test_an_extension_needs_a_renovation_elected_herstellung(client_a):
    """D78 over HTTP: an Erweiterung excluded from the 15 % test is a 422
    tax_input.extension_needs_herstellung on a repair, and on a renovation the herstellung
    election cannot be given up while the exclusion is current."""
    pid = flat(client_a)["property"]["id"]
    account = create(
        client_a,
        "/api/accounts",
        {
            "label": "Mietkonto",
            "bank": "Sparkasse",
            "iban": SAMPLE_IBAN,
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
        },
    )
    imported = client_a.post(
        f"/api/accounts/{account['id']}/imports",
        files={"file": ("umsaetze.zip", ZIP, "application/zip")},
    )
    assert ok(imported, 201)["status"] == "reconciled"
    lines = ok(client_a.get(f"/api/accounts/{account['id']}/transactions"))
    repair_line = next(x for x in lines if x["amount"] == "-360.18")
    other_line = next(x for x in lines if x["amount"] == "-724.70")
    repair = _confirm(client_a, repair_line, "repairs", pid)
    renovation = _confirm(client_a, other_line, "renovation", pid)

    def body(kind: str, allocation: str, value: dict, **extra) -> dict:
        return {
            "property_id": pid,
            "kind": kind,
            "allocation_id": allocation,
            "value": value,
        } | extra

    code = "tax_input.extension_needs_herstellung"
    extension = {"reason": "extension"}
    response = client_a.post(_path(pid), json=body("exclude_from_15pct", repair, extension))
    assert (response.status_code, response.json()["code"]) == (422, code)
    create(
        client_a,
        _path(pid),
        body("exclude_from_15pct", repair, {"reason": "annual_maintenance"}),
    )

    response = client_a.post(_path(pid), json=body("exclude_from_15pct", renovation, extension))
    assert (response.status_code, response.json()["code"]) == (422, code)
    herstellung = create(
        client_a,
        _path(pid),
        body("renovation_treatment", renovation, {"treatment": "herstellung"}),
    )
    create(client_a, _path(pid), body("exclude_from_15pct", renovation, extension))
    response = client_a.post(
        _path(pid),
        json=body(
            "renovation_treatment",
            renovation,
            {"treatment": "erhaltung"},
            supersedes_id=herstellung["id"],
        ),
    )
    assert (response.status_code, response.json()["code"]) == (422, code)


def test_a_certificate_figure_needs_its_document_and_loan(client_a):
    pid = flat(client_a)["property"]["id"]
    loan = create(client_a, "/api/loans", loan_body(pid))
    doc = ok(upload(client_a, b"%PDF-1.7 Zinsbescheinigung 2026"), 201)
    body = {
        "property_id": pid,
        "kind": "interest_certified",
        "tax_year": 2026,
        "loan_id": loan["id"],
        "document_id": doc["id"],
        "value": {"amount": "8123.45"},
    }
    row = create(client_a, _path(pid), body)
    assert (row["tax_year"], row["value"]) == (2026, {"amount": "8123.45"})
    assert ok(client_a.get(_path(pid), params={"tax_year": 2025})) == []
    assert ok(client_a.get(_path(pid), params={"tax_year": 2026})) == [row]
    response = client_a.post(_path(pid), json=body | {"document_id": None})
    assert response.status_code == 422


def test_another_workspace_cannot_read_or_write(client_a, client_b):
    pid = flat(client_a)["property"]["id"]
    create(client_a, _path(pid), {"property_id": pid, "kind": "market_rent", "value": MIETSPIEGEL})
    response = client_b.get(_path(pid))
    assert (response.status_code, response.json()["entity_type"]) == (404, "property")
    response = client_b.post(
        _path(pid), json={"property_id": pid, "kind": "market_rent", "value": MIETSPIEGEL}
    )
    assert response.status_code == 404
    own = new_property(client_b)
    new_unit(client_b, own["id"])
    assert ok(client_b.get(_path(own["id"]), params={"include_history": "true"})) == []


def test_the_tenancy_takes_its_due_day_and_running_cost_terms(client_a):
    tenancy = flat(client_a)["tenancy"]
    assert (tenancy["rent_due_day"], tenancy["operating_costs_terms"]) == (3, "prepayment")
    path = f"/api/tenancies/{tenancy['id']}"
    patched = ok(
        client_a.patch(path, json={"rent_due_day": 1, "operating_costs_terms": "included"})
    )
    assert (patched["rent_due_day"], patched["operating_costs_terms"]) == (1, "included")
    for wrong in ({"rent_due_day": 29}, {"rent_due_day": None}, {"operating_costs_terms": "warm"}):
        assert client_a.patch(path, json=wrong).status_code == 422, wrong
