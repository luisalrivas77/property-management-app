"""/api/income-inputs and /api/tax-rate (phase-4.md §8, D82, D83) over HTTP: create,
supersede, list with history, the error codes, the tax rate of a year with its source, and
workspace isolation. Synthetic people and amounts only."""

from decimal import Decimal
from uuid import uuid4

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, new_property, ok, upload
from db.db_support import _admin

pytestmark = pytest.mark.db

PATH = "/api/income-inputs"


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


def _owner(client, name: str, **extra) -> dict:
    return create(client, "/api/owners", {"full_name": name} | extra)


def _gross(owner_id: str, amount: str, **extra) -> dict:
    return {
        "owner_id": owner_id,
        "kind": "expected_gross_pay",
        "tax_year": 2026,
        "value": {"amount": amount},
    } | extra


def _rate(client, **params) -> dict:
    return ok(client.get("/api/tax-rate", params={"tax_year": 2026} | params))


def test_income_is_recorded_corrected_and_listed(client_a):
    owner = _owner(client_a, "Person A")
    first = create(client_a, PATH, _gross(owner["id"], "60000.00"))
    assert first["value"] == {"amount": "60000.00"}
    assert (first["supersedes_id"], first["withdrawn"]) == (None, False)
    assert first["recorded_by"] is not None
    second = create(client_a, PATH, _gross(owner["id"], "62000.00", supersedes_id=first["id"]))
    assert ok(client_a.get(PATH)) == [second]
    history = ok(client_a.get(PATH, params={"include_history": "true"}))
    assert [r["id"] for r in history] == [first["id"], second["id"]]
    assert ok(client_a.get(PATH, params={"tax_year": 2027})) == []
    assert ok(client_a.get(PATH, params={"owner_id": owner["id"]})) == [second]

    events = ok(client_a.get(f"/api/events?entity_id={owner['id']}"))
    recorded = [e for e in events if e["type"] == "owner.income_input_recorded"]
    assert [e["payload"]["income_input_id"] for e in recorded] == [first["id"], second["id"]]


def test_a_bescheid_needs_its_document_and_is_household_level(client_a):
    body = {"kind": "bescheid_zve", "tax_year": 2026, "value": {"amount": "80000.00"}}
    response = client_a.post(PATH, json=body)  # no document: the schema's scope check
    assert (response.status_code, response.json()["error"]) == (422, "validation")
    response = client_a.post(PATH, json=body | {"document_id": str(uuid4())})
    assert (response.status_code, response.json()["entity_type"]) == (404, "document")
    doc = ok(upload(client_a, b"%PDF-1.7 Einkommensteuerbescheid 2026"), 201)
    row = create(client_a, PATH, body | {"document_id": doc["id"]})
    assert (row["owner_id"], row["document_id"]) == (None, doc["id"])
    owner = _owner(client_a, "Person A")
    response = client_a.post(PATH, json=body | {"document_id": doc["id"], "owner_id": owner["id"]})
    assert (response.status_code, response.json()["error"]) == (422, "validation")


def test_the_error_codes(client_a):
    owner = _owner(client_a, "Person A")
    row = create(client_a, PATH, _gross(owner["id"], "60000.00"))
    response = client_a.post(PATH, json=_gross(owner["id"], "61000.00"))
    assert (response.status_code, response.json()["code"]) == (422, "income_input.not_current")
    other = _owner(client_a, "Person B")
    response = client_a.post(PATH, json=_gross(other["id"], "1.00", supersedes_id=row["id"]))
    assert (response.status_code, response.json()["code"]) == (422, "income_input.scope_invalid")
    response = client_a.post(PATH, json=_gross(str(uuid4()), "1.00"))
    assert (response.status_code, response.json()["entity_type"]) == (404, "owner")
    response = client_a.post(PATH, json=_gross(owner["id"], 60000))  # a JSON number
    assert response.status_code == 422
    withdrawn = create(
        client_a, PATH, _gross(owner["id"], "60000.00", supersedes_id=row["id"], withdrawn=True)
    )
    assert withdrawn["withdrawn"] is True
    assert ok(client_a.get(PATH)) == []


def test_the_tax_rate_follows_the_source(client_a):
    """§10.3's household (marginal_rate.yaml household_81993, override_rate)."""
    church = {"church_tax": True, "tax_state": "BY"}
    a = _owner(client_a, "Person A", **church)
    b = _owner(client_a, "Person B", **church)
    missing = _rate(client_a, result="-7948.66")
    assert (missing["missing"], missing["zve"], missing["effect"]) == (True, None, None)

    create(client_a, PATH, _gross(a["id"], "60000.00"))
    create(client_a, PATH, _gross(b["id"], "45000.00"))
    estimate = _rate(client_a, result="-7948.66")
    assert estimate["missing"] is False
    assert (estimate["zve"]["amount"], estimate["zve"]["source"]) == ("81993.00", "estimate")
    effect = estimate["effect"]
    assert (effect["effect"], effect["effective_rate"], effect["slope"]) == (
        "-2687.04",
        "33.8",
        None,
    )
    assert estimate["tariff"] == {"tax_year": 2026, "version": "2026.1", "reused_from": None}
    assert sorted(o["deductions"] for o in estimate["owner_estimates"]) == [
        "10041.00",
        "12966.00",
    ]
    zero = _rate(client_a, result="0.00")
    assert (zero["effect"]["effective_rate"], zero["effect"]["slope"]) == (None, "34.6")

    create(
        client_a,
        PATH,
        {"kind": "marginal_rate_override", "tax_year": 2026, "value": {"rate": "0.4200"}},
    )
    override = _rate(client_a, result="-7948.66")
    assert (override["effect"]["source"], override["effect"]["effect"]) == ("override", "-3338.44")

    for bad in ("1.234", "abc", "1e5"):
        response = client_a.get("/api/tax-rate", params={"tax_year": 2026, "result": bad})
        assert response.status_code == 422, bad
    response = client_a.get("/api/tax-rate", params={"tax_year": 2024, "result": "0.00"})
    assert (response.status_code, response.json()["code"]) == (422, "tariff.no_file")


def test_a_carried_bescheid_takes_the_property(client_a):
    doc = ok(upload(client_a, b"%PDF-1.7 Einkommensteuerbescheid"), 201)
    body = {"kind": "bescheid_zve", "tax_year": 2026, "value": {"amount": "80000.00"}}
    create(client_a, PATH, body | {"document_id": doc["id"]})
    response = client_a.get("/api/tax-rate", params={"tax_year": 2027, "result": "-100.00"})
    assert (response.status_code, response.json()["code"]) == (
        422,
        "income.assessed_result_needed",
    )
    prop = new_property(client_a)
    draft = ok(client_a.get(f"/api/properties/{prop['id']}/anlage-v", params={"tax_year": 2026}))
    assessed = Decimal(draft["totals"]["result"])  # the 2026 result the Bescheid contains
    got = _rate(client_a, tax_year=2027, result="-100.00", property_id=prop["id"])
    assert (got["zve"]["source"], got["zve"]["carried_forward"]) == ("bescheid", True)
    assert Decimal(got["zve"]["amount"]) == max(Decimal("80000.00") - assessed, Decimal(0))


def test_another_workspace_cannot_read_or_write(client_a, client_b):
    owner = _owner(client_a, "Person A")
    create(client_a, PATH, _gross(owner["id"], "60000.00"))
    assert ok(client_b.get(PATH, params={"include_history": "true"})) == []
    response = client_b.post(PATH, json=_gross(owner["id"], "1.00"))
    assert response.status_code == 404
    assert _rate(client_b, result="-100.00")["missing"] is True
