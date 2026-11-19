"""/api/household-scenarios and /api/household-plan (docs/phases/phase-9.md §13, D248; phase 9
step 2) over HTTP: status codes, the error codes, the read shape and workspace isolation. The
service behaviour is tests/db/test_services_household_scenarios.py and
tests/db/test_household_plan.py; the arithmetic is the phase 9 rule fixtures."""

from uuid import uuid4

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

pytestmark = pytest.mark.db

PATH = "/api/household-scenarios"


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


def _codes(response) -> tuple[int, list[str]]:
    return response.status_code, response.json().get("codes")


def test_a_scenario_is_recorded_corrected_withdrawn_and_listed(client_a, sessions):
    first = create(client_a, PATH, {"name": "Basis", "is_default": True})
    assert first["created_by"] == str(sessions[TOKEN_A].user_id)
    assert first["levers"]["buffer_percent"] == "10"  # decimals stay strings
    assert first["levers"]["value_change"] == {"depot": None, "crypto": None}
    second = create(
        client_a,
        PATH,
        {
            "name": "Basis",
            "is_default": True,
            "supersedes_id": first["id"],
            "levers": {"value_change": {"depot": "0.03", "crypto": "-0.015"}},
        },
    )
    assert second["levers"]["value_change"] == {"depot": "0.03", "crypto": "-0.015"}
    assert [s["id"] for s in ok(client_a.get(PATH))] == [second["id"]]
    gone = ok(client_a.post(f"{PATH}/{second['id']}/withdraw"), 201)
    assert gone["withdrawn"] and gone["supersedes_id"] == second["id"]
    assert ok(client_a.get(PATH)) == []
    history = ok(client_a.get(PATH, params={"include_history": "true"}))
    assert [s["id"] for s in history] == [first["id"], second["id"], gone["id"]]


def test_the_error_codes(client_a, sessions):
    row = create(client_a, PATH, {"name": "Basis", "is_default": True})
    response = client_a.post(PATH, json={"name": "Zweiter", "is_default": True})
    assert _codes(response) == (422, ["household_scenario.not_current"])
    far = {"horizon_months": 12, "one_offs": [{"month": "2099-01", "amount": "100.00"}]}
    response = client_a.post(PATH, json={"name": "Weit", "levers": far})
    assert _codes(response) == (422, ["household_scenario.one_off_outside"])
    changed = {"name": "Basis", "supersedes_id": row["id"], "withdrawn": True}
    response = client_a.post(PATH, json=changed | {"levers": {"horizon_months": 24}})
    assert _codes(response) == (422, ["household_scenario.withdrawal_invalid"])
    unknown = {"reserve_accounts": {"added": [str(uuid4())]}}
    response = client_a.post(PATH, json={"name": "Konto", "levers": unknown})
    assert (response.status_code, response.json()["entity_type"]) == (404, "account")

    # The schema's own checks are validation errors: a rate outside ±0.50, a horizon too
    # short, a withdrawal without the row it withdraws, an override both set and dropped.
    for body in (
        {"name": "X", "levers": {"value_change": {"depot": "0.6"}}},
        {"name": "X", "levers": {"horizon_months": 6}},
        {"name": "X", "withdrawn": True},
        {
            "name": "X",
            "levers": {"plan_overrides": [{"plan_key": "a", "monthly": "1.00", "dropped": True}]},
        },
        {"name": ""},
    ):
        response = client_a.post(PATH, json=body)
        assert response.status_code == 422 and response.json()["error"] == "validation", body

    response = client_a.post(f"{PATH}/{uuid4()}/withdraw")
    assert (response.status_code, response.json()["entity_type"]) == (404, "household_scenario")


def test_another_workspace_sees_nothing(client_a, client_b, sessions):
    row = create(client_a, PATH, {"name": "Basis", "is_default": True})
    assert ok(client_b.get(PATH, params={"include_history": "true"})) == []
    assert client_b.post(f"{PATH}/{row['id']}/withdraw").status_code == 404
    response = client_b.post(PATH, json={"name": "Basis", "supersedes_id": row["id"]})
    assert response.status_code == 404
    create(client_b, PATH, {"name": "Basis", "is_default": True})  # B's own default


def test_the_plan_reads_and_refuses_an_unknown_scenario(client_a, client_b, sessions):
    """GET /household-plan on an empty workspace: no account leaves a month open, so every
    ended month counts as closed (as GET /health reads it) and the plan's figures are zeros,
    decimals as strings; without a saved scenario the projection is the to-do „Annahme
    eintragen“ (D248), with one the missing month-end. ?scenario= names a row, an unknown or
    another workspace's id is a 404."""
    got = ok(client_a.get("/api/household-plan"))
    assert got["scenario_id"] is None and got["plans"] == []
    assert got["levers"]["buffer_percent"] == "10"
    assert {r["amount"] for r in got["surplus"]["rows"]} == {"0.00"}
    assert [t["code"] for t in got["projection"]["todos"]] == ["rate_missing"]
    row = create(client_a, PATH, {"name": "Basis", "is_default": True})
    got = ok(client_a.get("/api/household-plan"))
    assert got["scenario_id"] == row["id"]
    assert [t["code"] for t in got["projection"]["todos"]] == ["month_end_missing"]
    named = ok(client_a.get("/api/household-plan", params={"scenario": row["id"]}))
    assert named["scenario_id"] == row["id"]
    for client, scenario in ((client_a, str(uuid4())), (client_b, row["id"])):
        response = client.get("/api/household-plan", params={"scenario": scenario})
        assert response.status_code == 404
        assert response.json()["entity_type"] == "household_scenario"


PREVIEW = "/api/household-plan/preview"
COMPARISON = "/api/household-plan/comparison"


def _changes(*changes) -> dict:
    return {"levers": {"spending_changes": list(changes)}}


def test_the_preview_and_the_comparison(client_a, sessions):
    """D263: POST /household-plan/preview answers the plan for typed levers and stores no
    scenario; it refuses as a save does (a percent of a code with no measured spending, a
    malformed change). GET /household-plan/comparison needs a month and has no rows without
    a scenario that names a code."""
    monthly = {"code": "hh_supermarket", "monthly": "250.00", "from_month": "2027-06"}
    got = ok(client_a.post(PREVIEW, json=_changes(monthly)))
    assert got["levers"]["spending_changes"][0]["monthly"] == "250.00"
    percent = {"code": "hh_childcare", "percent": "0.10", "from_month": "2027-06"}
    response = client_a.post(PREVIEW, json=_changes(percent))
    assert _codes(response) == (422, ["household_scenario.spending_invalid"])
    response = client_a.post(PATH, json={"name": "Basis"} | _changes(percent))
    assert _codes(response) == (422, ["household_scenario.spending_invalid"])
    for bad in (
        {"code": "hh_car_purchase", "monthly": "1.00", "from_month": "2027-06"},
        {"code": "hh_supermarket", "from_month": "2027-06"},
    ):
        response = client_a.post(PREVIEW, json=_changes(bad))
        assert response.status_code == 422 and response.json()["error"] == "validation", bad
    assert ok(client_a.get(PATH, params={"include_history": "true"})) == []

    got = ok(client_a.get(COMPARISON, params={"month": "2027-03"}))
    assert (got["month"], got["rows"], got["scenario_id"]) == ("2027-03", [], None)
    for params in ({"month": "2027-13"}, {"month": "März"}, {}):
        assert client_a.get(COMPARISON, params=params).status_code == 422


EXPLANATION = "/api/household-plan/explanation"


def test_the_explanation_reads_the_template_and_queues_nothing_without_consent(client_a, sessions):
    """Phase 9 step 4 (§8.6, D253): with no row stored and ai_private_accounts off, GET is the
    template rendered on read (no id, reason consent_off) and POST queues nothing (202); an
    unknown scenario is 404."""
    shown = ok(client_a.get(EXPLANATION))
    assert shown["id"] is None and shown["created_at"] is None
    assert (shown["status"], shown["reason"], shown["stale"]) == ("template", "consent_off", False)
    assert shown["mode"] == "own_use"
    queued = ok(client_a.post(EXPLANATION, json={}), 202)
    assert queued == {"scenario_id": None, "queued": False}
    unknown = client_a.get(EXPLANATION, params={"scenario": str(uuid4())})
    assert unknown.status_code == 404
    assert client_a.post(EXPLANATION, json={"scenario": str(uuid4())}).status_code == 404
