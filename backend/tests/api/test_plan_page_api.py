"""GET /api/household-goal/plan (phase 10 §7.11 (h), D306, „Euer Plan“) over HTTP: both views,
the five switches, that the shared view never carries a label the household did not choose to
show (the property's, the reserve's accounts unless `accounts`), English texts and workspace
isolation. The service on the database is tests/db/test_plan_page.py; the cards' arithmetic
tests/fixtures/phase10/plan_page.yaml."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from api_support import ok

# tests/db's helpers import one another bare (test_services_card_import), which resolves only
# while tests/db is on sys.path, as when tests/db is collected; appended, so nothing here is
# shadowed by it.
sys.path.append(str(Path(__file__).parents[1] / "db"))

from db.db_support import _admin
from db.matching_support import World
from db.test_plan_page import _feasible
from db.test_services_health_snapshots import NOW, TODAY

from hausbuch.api.routes import household_plan

pytestmark = pytest.mark.db

PLAN = "/api/household-goal/plan"
FLAT = "Beispielweg 7, Wohnung 3"  # invented
GIRO = "Girokonto Muster"  # invented


@pytest.fixture
def world(app_engine, fresh_workspaces, monkeypatch) -> World:
    monkeypatch.setattr(household_plan, "_today", lambda: TODAY)
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def planned(world, dbname) -> World:
    _feasible(world)
    with _admin(dbname) as conn:
        conn.execute("UPDATE property SET label = %s WHERE id = %s", (FLAT, world.property_id))
        conn.execute(
            "UPDATE account SET label = %s WHERE workspace_id = %s AND kind = 'bank_current'"
            " AND purpose = 'private'",
            (GIRO, world.ws),
        )
    return world


def test_without_a_goal(client_a, world):
    got = ok(client_a.get(PLAN))
    assert (got["view"], got["word"], got["goal"], got["todos"]) == ("own", "none", None, [])
    assert "switches" not in got
    shared = ok(client_a.get(PLAN, params={"view": "share"}))
    assert shared["word"] == "none" and "todos" not in shared
    assert shared["switches"] == {
        "round_amounts": True,
        "accounts": False,
        "wants": False,
        "risks": True,
        "assumptions": True,
    }


def test_own_view_has_everything(client_a, client_b, planned):
    got = ok(client_a.get(PLAN))
    assert got["word"] == "feasible"
    assert [p["label"] for p in got["assumptions"]["properties"]] == [FLAT]
    assert any(GIRO in a for a in got["heute"]["reserve_accounts"])
    assert [t["code"] for t in got["risks"]["todos"]] == ["living_area_missing"]
    assert got["plan"]["wants"], "the balanced route cuts the restaurant"
    assert isinstance(got["goal"]["target"], str)  # money is a string
    assert isinstance(got["heute"]["above_target"], str)  # GET /household-plan's „Anlegbar jetzt“
    assert ok(client_b.get(PLAN))["word"] == "none"


def test_share_view_by_default_leaves_the_labels_out(client_a, planned):
    response = client_a.get(PLAN, params={"view": "share"})
    got = ok(response)
    assert FLAT not in response.text and GIRO not in response.text
    assert "todos" not in got and "todos" not in got["risks"]
    assert "reserve_accounts" not in got["heute"] and "wants" not in got["plan"]
    assert "label" not in got["assumptions"]["properties"][0]
    own = ok(client_a.get(PLAN))
    assert got["heute"]["net_worth"].endswith("0000.00")  # to 10,000 €
    assert got["heute"]["net_worth"] != own["heute"]["net_worth"]


def test_each_switch(client_a, planned):
    own = ok(client_a.get(PLAN))

    def share(**switches):
        params = {"view": "share"} | {k: str(v).lower() for k, v in switches.items()}
        response = client_a.get(PLAN, params=params)
        assert FLAT not in response.text
        return response, ok(response)

    response, got = share(accounts=True)
    assert GIRO in response.text and got["heute"]["reserve_accounts"]
    _, got = share(wants=True)
    assert [w["code"] for w in got["plan"]["wants"]] == [w["code"] for w in own["plan"]["wants"]]
    _, got = share(risks=False)
    assert "risks" not in got
    _, got = share(assumptions=False)
    assert "assumptions" not in got
    _, got = share(round_amounts=False)
    assert got["heute"]["net_worth"] == own["heute"]["net_worth"]
    assert got["switches"]["round_amounts"] is False


def test_english(client_a, planned):
    got = ok(client_a.get(PLAN, headers={"Accept-Language": "en"}))
    [todo] = got["risks"]["todos"]
    assert todo["action"] == "Enter living area"
    assert todo["text"].startswith("No living area is entered for the flat")
    shared = ok(client_a.get(PLAN, params={"view": "share"}, headers={"Accept-Language": "en"}))
    assert "todos" not in shared["risks"] and "reserve_accounts" not in shared["heute"]


def test_a_kept_answer_is_kept_per_view_and_switch(client_a, planned, api_app):
    """The route is a kept answer (§7.4): its key holds the query, so a kept own view never
    answers a shared request and a kept shared view never another switch's."""
    store = api_app.state.kept_answers
    store.clock = lambda: datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    own = client_a.get(PLAN)
    shared = client_a.get(PLAN, params={"view": "share"})
    with_accounts = client_a.get(PLAN, params={"view": "share", "accounts": "true"})
    assert store.counters.hits == 0
    assert FLAT in own.text and GIRO in own.text
    assert FLAT not in shared.text and GIRO not in shared.text
    assert FLAT not in with_accounts.text and GIRO in with_accounts.text
    again = client_a.get(PLAN, params={"view": "share"})
    assert store.counters.hits == 1 and again.content == shared.content
