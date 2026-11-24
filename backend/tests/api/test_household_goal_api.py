"""GET /api/household-goal (docs/phases/phase-10.md §7.9 (d)) over HTTP: the read shape, a goal
saved through POST /api/household-scenarios, its error code, workspace isolation and the kept
answer (§7.4). The words on a projected household are tests/db/test_household_goal.py; the
arithmetic is tests/fixtures/phase10/goal.yaml."""

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

pytestmark = pytest.mark.db

GOAL = "/api/household-goal"
SCENARIOS = "/api/household-scenarios"
# Far enough after any day these tests run on; the app proposes no due month (D289 (1)).
DUE = "2099-12"


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


def _goal(**kw) -> dict:
    goal = {"kind": "net_worth", "target": "250000.00", "due_month": DUE, "set_on": "2026-11-24"}
    return goal | kw


def test_without_a_goal_the_word_is_none(client_a, sessions):
    got = ok(client_a.get(GOAL))
    assert (got["word"], got["goal"], got["scenarios"], got["todos"]) == ("none", None, [], [])
    create(client_a, SCENARIOS, {"name": "Basis", "is_default": True})
    assert ok(client_a.get(GOAL))["word"] == "none"


def test_a_goal_is_saved_on_the_default_scenario_and_read(client_a, client_b, sessions):
    """Without an account every ended month is closed, but no month-end is stored, so the
    goal has no word: `issue` no_projection with the projection's to-do „Monatsabschluss
    festhalten“; the goal's figures stay strings; another workspace sees no goal."""
    row = create(
        client_a,
        SCENARIOS,
        {"name": "Basis", "is_default": True, "levers": {"goal": _goal()}},
    )
    assert row["levers"]["goal"] == _goal()
    assert "goal_choice" not in row["levers"]  # excluded while None
    got = ok(client_a.get(GOAL))
    assert (got["word"], got["issue"]) == (None, "no_projection")
    assert got["goal"] == _goal()
    assert [t["code"] for t in got["todos"]] == ["month_end_missing"]
    assert ok(client_b.get(GOAL))["word"] == "none"


def test_the_goals_checks(client_a, sessions):
    """A due month not after the current one is the service's refusal; a target of 0 and a
    reserve goal beyond 24 months or not in half months are the schema's."""
    response = client_a.post(
        SCENARIOS, json={"name": "Basis", "levers": {"goal": _goal(due_month="2020-01")}}
    )
    assert (response.status_code, response.json()["codes"]) == (
        422,
        ["household_scenario.goal_invalid"],
    )
    for goal in (
        _goal(target="0.00"),
        _goal(kind="reserve_months", target="25"),
        _goal(kind="reserve_months", target="6.3"),
        _goal(due_month="2099-13"),
    ):
        response = client_a.post(SCENARIOS, json={"name": "X", "levers": {"goal": goal}})
        assert response.status_code == 422 and response.json()["error"] == "validation", goal


def test_the_goal_is_a_kept_answer_and_a_save_is_seen_at_once(api_app, client_a, sessions):
    kept = api_app.state.kept_answers
    first = client_a.get(GOAL)
    assert first.status_code == 200 and kept.counters.stored >= 1
    hits = kept.counters.hits
    again = client_a.get(GOAL)
    assert kept.counters.hits == hits + 1 and again.content == first.content
    create(
        client_a,
        SCENARIOS,
        {"name": "Basis", "is_default": True, "levers": {"goal": _goal()}},
    )
    assert ok(client_a.get(GOAL))["goal"] == _goal()
