"""POST /api/household-goal/path/adopt (docs/phases/phase-10.md §7.11 (i) 5, D311) and the
path block of GET /api/household-goal over HTTP: no body is read, a goal without a path that
can be adopted is refused with household_scenario.path_invalid and nothing is written, and a
save through POST /api/household-scenarios cannot follow the path's return. A successful
adopt on a household with closed months is tests/db/test_goal_path.py (its world is dated
2027, after the clock this API runs on)."""

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

pytestmark = pytest.mark.db

ADOPT = "/api/household-goal/path/adopt"
GOAL_READ = "/api/household-goal"
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


def _refused(response) -> None:
    assert (response.status_code, response.json()["codes"]) == (
        422,
        ["household_scenario.path_invalid"],
    )


def test_adopt_without_a_path_is_refused_and_writes_nothing(client_a, client_b, sessions):
    _refused(client_a.post(ADOPT))
    row = create(
        client_a, SCENARIOS, {"name": "Basis", "is_default": True, "levers": {"goal": GOAL}}
    )
    # No closed month: the goal has an issue and no path.
    assert ok(client_a.get(GOAL_READ))["goal_path"] is None
    _refused(client_a.post(ADOPT, json={"monthly": "999.00"}))  # a body is ignored
    listed = ok(client_a.get(SCENARIOS))
    assert [r["id"] for r in listed] == [row["id"]]
    _refused(client_b.post(ADOPT))


def test_a_save_cannot_follow_the_paths_return(client_a, sessions):
    row = create(
        client_a, SCENARIOS, {"name": "Basis", "is_default": True, "levers": {"goal": GOAL}}
    )
    refused = client_a.post(
        SCENARIOS,
        json={
            "name": "Basis",
            "is_default": True,
            "supersedes_id": row["id"],
            "levers": {
                "goal": GOAL,
                "goal_choice": {"investment": "path", "investment_chosen_on": "2026-11-26"},
            },
        },
    )
    _refused(refused)
