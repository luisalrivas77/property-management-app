"""POST /api/household-goal/path/adopt over HTTP on the day a path is adopted
(docs/phases/phase-10.md §7.13 (d) 7; D316): the adopt answer carries the goal exactly as the
next GET /household-goal answers it. The world is tests/db/test_goal_path.py's (rows INVENTED,
round values); the command's arithmetic is tested there."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from api_support import ok

# tests/db's helpers import one another bare, which resolves only while tests/db is on
# sys.path (as tests/api/test_plan_page_api.py does); appended, so nothing here is shadowed.
sys.path.append(str(Path(__file__).parents[1] / "db"))

from db.db_support import _admin
from db.matching_support import World
from db.test_goal_path import _goal_world
from db.test_services_health_snapshots import NOW, TODAY

from hausbuch.api import commands
from hausbuch.api.routes import household_plan

pytestmark = pytest.mark.db

ADOPT = "/api/household-goal/path/adopt"
GOAL = "/api/household-goal"


class _Frozen(datetime):
    """The command's clock at the world's NOW, so the adopt and the reads share one day."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206 - datetime's own signature
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture
def person(auth_a, dbname):
    """client_a's user as a row, since the adopt writes a scenario created by it."""
    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )


@pytest.fixture
def world(app_engine, fresh_workspaces, monkeypatch, person) -> World:
    monkeypatch.setattr(household_plan, "_today", lambda: TODAY)
    monkeypatch.setattr(commands, "datetime", _Frozen)
    assert _Frozen.now(UTC).date() == TODAY
    return World(app_engine, fresh_workspaces[0], now=NOW)


def test_the_adopt_answer_carries_the_goal_the_next_get_answers(client_a, world):
    _goal_world(world)
    before = ok(client_a.get(GOAL))
    assert before["goal_path"]["adoptable"] and before["goal_path"]["adopted"] is None
    response = client_a.post(ADOPT)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["levers"]["goal_path"]["adopted_on"] == TODAY.isoformat()
    assert body["levers"]["goal_choice"]["investment"] == "path"
    assert body["goal"] == ok(client_a.get(GOAL))
    assert body["goal"]["goal_path"]["adopted"] is not None
    # Adopting again with nothing moved writes nothing and still answers with the goal.
    again = client_a.post(ADOPT)
    assert again.status_code == 201, again.text
    assert again.json()["id"] == body["id"]
    assert again.json()["goal"] == body["goal"]
