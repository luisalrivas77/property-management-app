"""GET /api/household-goal/tracking (docs/phases/phase-10.md §7.9 (e)) over HTTP: the read
shape without a goal and without a followed return, a followed return saved through POST
/api/household-scenarios, the refusal of an unknown one, workspace isolation and the kept
answer (§7.4). The figures on a household are tests/db/test_goal_tracking.py; the arithmetic
is tests/fixtures/phase10/goal_invest.yaml."""

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

pytestmark = pytest.mark.db

TRACKING = "/api/household-goal/tracking"
SCENARIOS = "/api/household-scenarios"
GOAL = {"kind": "net_worth", "target": "250000.00", "due_month": "2099-12", "set_on": "2026-11-24"}


@pytest.fixture
def sessions(fresh_workspaces, dbname):
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


def _choice(route: str) -> dict:
    return {"investment": route, "investment_chosen_on": "2026-11-26"}


def test_the_tracking_read_and_a_followed_return(api_app, client_a, client_b, sessions):
    first = ok(client_a.get(TRACKING))
    assert (first["shown"], first["reason"], first["short"]) == (False, "no_goal", [])
    assert first["word"] is None and first["paid_in"] is None
    row = create(
        client_a, SCENARIOS, {"name": "Basis", "is_default": True, "levers": {"goal": GOAL}}
    )
    assert ok(client_a.get(TRACKING))["reason"] == "not_following"

    refused = client_a.post(
        SCENARIOS,
        json={
            "name": "Basis",
            "is_default": True,
            "supersedes_id": row["id"],
            "levers": {"goal": GOAL, "goal_choice": _choice("product_x")},
        },
    )
    assert (refused.status_code, refused.json()["codes"]) == (
        422,
        ["household_scenario.goal_invalid"],
    )

    kept = api_app.state.kept_answers
    hits = kept.counters.hits
    create(
        client_a,
        SCENARIOS,
        {
            "name": "Basis",
            "is_default": True,
            "supersedes_id": row["id"],
            "levers": {"goal": GOAL, "goal_choice": _choice("more_return")},
        },
    )
    got = ok(client_a.get(TRACKING))
    # No closed month: the goal has an issue, so the card shows without a word.
    assert (got["shown"], got["route"], got["reason"]) == (True, "more_return", "goal_issue")
    assert got["word"] is None
    again = client_a.get(TRACKING)
    assert kept.counters.hits == hits + 1 and again.json() == got
    assert ok(client_b.get(TRACKING))["reason"] == "no_goal"
