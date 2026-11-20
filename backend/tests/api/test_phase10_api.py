"""GET /api/household-budget and GET /api/accounts/{id}/month (docs/phases/phase-10.md §6, D269,
D274; phase 10 step 1) over HTTP: status codes, the error codes, the read shape and workspace
isolation. The arithmetic is the phase 10 rule fixtures and tests/db/test_services_budget.py,
tests/db/test_services_account_month.py."""

from datetime import datetime
from uuid import uuid4

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

from hausbuch.schemas.health import month_of
from hausbuch.services.documents import LOCAL_TIME_ZONE

pytestmark = pytest.mark.db

BUDGET = "/api/household-budget"
SCENARIOS = "/api/household-scenarios"
ACCOUNT = {
    "label": "Mietkonto",
    "bank": "Sparkasse",
    "iban": "DE50100000000000000001",
    "opening_balance": "120.50",
    "opening_balance_on": "2026-03-31",
}


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so a row's created_by (FK to app_user) is real."""
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


def test_the_budget_reads_the_health_window_and_the_scenario(client_a, client_b, sessions):
    got = ok(client_a.get(BUDGET))
    assert got["window"] == ok(client_a.get("/api/health"))["window"]
    assert got["scenario_id"] is None
    assert got["targets"] == {
        "needs_max": None,
        "wants_max": None,
        "saving_min": None,
        "housing_max": None,
    }
    assert [r["key"] for r in got["references"]] != []  # the reference points (D275)
    assert all(line["mark"] is None for line in got["lines"])  # no target, no mark

    levers = {
        "budget_targets": {"needs_max": "50.0", "saving_min": "20"},
        "budget_class_overrides": {"hh_restaurant": "need"},
    }
    row = create(client_a, SCENARIOS, {"name": "Basis", "is_default": True, "levers": levers})
    assert row["levers"]["budget_targets"]["needs_max"] == "50.0"  # decimals stay strings
    for params in ({}, {"scenario_id": row["id"]}):
        got = ok(client_a.get(BUDGET, params=params))
        assert got["scenario_id"] == row["id"]
        assert got["targets"]["needs_max"] == "50.0" and got["targets"]["wants_max"] is None

    for client, scenario in ((client_a, str(uuid4())), (client_b, row["id"])):
        response = client.get(BUDGET, params={"scenario_id": scenario})
        assert (response.status_code, response.json()["entity_type"]) == (
            404,
            "household_scenario",
        )


def test_a_class_override_that_cannot_move_is_refused(client_a, sessions):
    for code in ("hh_car_purchase", "uncategorized", "hh_salary"):
        body = {"name": "X", "levers": {"budget_class_overrides": {code: "want"}}}
        response = client_a.post(SCENARIOS, json=body)
        assert (response.status_code, response.json()["codes"]) == (
            422,
            ["household_scenario.budget_class_invalid"],
        ), code
    for targets in ({"needs_max": "100.1"}, {"wants_max": "-1"}, {"saving_min": "33.33"}):
        body = {"name": "X", "levers": {"budget_targets": targets}}
        response = client_a.post(SCENARIOS, json=body)
        assert response.status_code == 422 and response.json()["error"] == "validation", targets
    assert ok(client_a.get(SCENARIOS)) == []


def test_the_account_month(client_a, client_b):
    account = create(client_a, "/api/accounts", ACCOUNT)
    path = f"/api/accounts/{account['id']}/month"
    current = month_of(datetime.now(LOCAL_TIME_ZONE).date())
    got = ok(client_a.get(path, params={"month": current}))
    assert got["account_id"] == account["id"] and got["month"] == current and got["running"]
    # No statement yet: the balance stays the opening balance, as of its own day.
    assert (got["closing_balance"], got["closing_as_of"]) == ("120.50", "2026-03-31")
    assert (got["incoming"], got["outgoing"], got["lines"], got["open_lines"]) == (
        "0.00",
        "0.00",
        0,
        0,
    )
    before = ok(client_a.get(path, params={"month": "2026-03"}))
    assert before["opening_balance"] is None and before["closing_balance"] == "120.50"
    assert not before["running"]

    future = client_a.get(path, params={"month": "2099-01"})
    assert (future.status_code, future.json()["codes"]) == (422, ["account.month_in_future"])
    for month in ("2026-13", "2026-1", "März"):
        assert client_a.get(path, params={"month": month}).status_code == 422
    assert client_a.get(path).status_code == 422  # the month is required

    other = client_b.get(path, params={"month": current})  # row-level security: not found
    assert (other.status_code, other.json()["entity_type"]) == (404, "account")
    unknown = client_a.get(f"/api/accounts/{uuid4()}/month", params={"month": current})
    assert unknown.status_code == 404
