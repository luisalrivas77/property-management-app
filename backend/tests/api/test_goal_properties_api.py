"""GET /api/household-goal/properties (docs/phases/phase-10.md §7.9 (e) items 1, 2) over HTTP:
the read shape without a goal, for a goal that is not a net worth and for one the walk cannot
answer yet, workspace isolation and the kept answer (§7.4). The cards on a household are
tests/db/test_goal_properties.py; the arithmetic is tests/fixtures/phase10/goal_property.yaml."""

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

pytestmark = pytest.mark.db

PROPERTIES = "/api/household-goal/properties"
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


def test_the_properties_read(api_app, client_a, client_b, sessions):
    assert ok(client_a.get(PROPERTIES)) == {"shown": False, "reason": "no_goal", "properties": []}
    row = create(
        client_a,
        SCENARIOS,
        {"name": "Basis", "is_default": True, "levers": {"goal": GOAL | {"kind": "saved_sum"}}},
    )
    assert ok(client_a.get(PROPERTIES))["reason"] == "not_net_worth"
    create(
        client_a,
        SCENARIOS,
        {"name": "Basis", "is_default": True, "supersedes_id": row["id"], "levers": {"goal": GOAL}},
    )
    kept = api_app.state.kept_answers
    hits = kept.counters.hits
    got = ok(client_a.get(PROPERTIES))
    # No closed month: the goal has an issue, so no card.
    assert got == {"shown": False, "reason": "goal_issue", "properties": []}
    again = client_a.get(PROPERTIES)
    assert kept.counters.hits == hits + 1 and again.json() == got
    assert ok(client_b.get(PROPERTIES))["reason"] == "no_goal"
