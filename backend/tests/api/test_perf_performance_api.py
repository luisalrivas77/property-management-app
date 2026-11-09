"""/api/properties/{id}/performance/actuals and /projection (phase-4.md §8, D85–D87) over HTTP:
decimal strings, the lever query parameters, the default scenario, the error codes and
workspace isolation. The route reads the real clock, so years are counted from today."""

from datetime import datetime
from uuid import uuid4

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, loan_body, new_property, ok
from db.db_support import _admin

from hausbuch.services.documents import LOCAL_TIME_ZONE

pytestmark = pytest.mark.db

THIS_YEAR = datetime.now(LOCAL_TIME_ZONE).year


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so a scenario's recorded_by (FK to app_user) is real."""
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


def _flat(client) -> str:
    return new_property(
        client,
        notarized_on="2026-03-10",
        purchase_date="2026-07-01",
        purchase_price="300000.00",
    )["id"]


def _code(response) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def test_actuals(client_a):
    pid = _flat(client_a)
    got = ok(client_a.get(f"/api/properties/{pid}/performance/actuals"))
    assert got["property_id"] == pid
    assert [y["year"] for y in got["years"]] == list(range(2026, THIS_YEAR + 1))
    assert got["years"][0]["rent_in"] == "0.00"  # money stays a decimal string


def test_projection_levers_scenarios_and_overrides(client_a):
    pid = _flat(client_a)
    create(
        client_a,
        "/api/loans",
        loan_body(pid, disbursed_on="2026-06-15", first_payment_on="2026-07-30"),
    )
    path = f"/api/properties/{pid}/performance/projection"
    sale_year = max(2036, THIS_YEAR + 1)  # the first tax-free 31 Dec after a 2026 contract

    got = ok(client_a.get(path))
    assert got["scenario"]["sale_year"] == sale_year and got["scenario_id"] is None
    assert [y["year"] for y in got["years"]] == list(range(THIS_YEAR + 1, sale_year + 1))
    assert got["sale"]["initial_equity"] == "60000.00"  # 300,000 − 240,000

    scenario = create(
        client_a,
        f"/api/properties/{pid}/scenarios",
        {
            "property_id": pid,
            "name": "Basis",
            "is_default": True,
            "levers": {"sale_year": THIS_YEAR + 3, "rent_growth": "0.03"},
        },
    )
    got = ok(client_a.get(path))
    assert got["scenario_id"] == scenario["id"]
    assert got["years"][-1]["year"] == THIS_YEAR + 3

    got = ok(
        client_a.get(
            path, params={"sale_year": THIS_YEAR + 2, "value_growth": "0.01", "vacancy_months": "1"}
        )
    )
    assert got["scenario"]["sale_year"] == THIS_YEAR + 2
    assert (got["scenario"]["value_growth"], got["scenario"]["rent_growth"]) == ("0.01", "0.03")
    assert got["scenario"]["vacancy_months"] == "1"
    listed = ok(client_a.get(f"/api/properties/{pid}/scenarios"))
    assert [s["id"] for s in listed] == [scenario["id"]]  # the override wrote nothing

    response = client_a.get(path, params={"sale_year": THIS_YEAR})
    assert _code(response) == (422, "scenario.sale_year_before_today")
    response = client_a.get(path, params={"value_growth": "1.5"})
    assert response.status_code == 422 and response.json()["error"] == "validation"
    response = client_a.get(path, params={"scenario_id": str(uuid4())})
    assert response.status_code == 404


def test_a_purchase_before_the_first_rule_set(client_a):
    pid = new_property(client_a)["id"]  # bought in 2024
    response = client_a.get(f"/api/properties/{pid}/performance/actuals")
    assert _code(response) == (422, "projection.no_rule_set")


def test_another_workspace_sees_nothing(client_a, client_b):
    pid = _flat(client_a)
    for kind in ("actuals", "projection"):
        response = client_b.get(f"/api/properties/{pid}/performance/{kind}")
        assert (response.status_code, response.json()["entity_type"]) == (404, "property")
    response = client_a.get(f"/api/properties/{uuid4()}/performance/actuals")
    assert response.status_code == 404
