"""/api/properties/{id}/scenarios (phase-4.md §6, §10, D87) over HTTP: record, supersede, list
with history, the error codes, workspace isolation."""

from uuid import uuid4

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, loan_body, new_property, ok
from db.db_support import _admin

pytestmark = pytest.mark.db


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


def _path(property_id: str) -> str:
    return f"/api/properties/{property_id}/scenarios"


def _body(pid: str, **overrides) -> dict:
    levers = {"sale_year": 2036} | overrides.pop("levers", {})
    return {"property_id": pid, "name": "Basis", "levers": levers} | overrides


def _code(response) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def test_a_scenario_is_recorded_corrected_and_listed(client_a):
    pid = new_property(client_a)["id"]
    first = create(client_a, _path(pid), _body(pid, is_default=True))
    assert first["levers"]["sale_year"] == 2036 and first["is_default"]
    assert first["levers"]["value_growth"] == "0.02"  # money and rates stay decimal strings
    second = create(
        client_a,
        _path(pid),
        _body(pid, is_default=True, supersedes_id=first["id"], levers={"rent_growth": "0.015"}),
    )
    assert [s["id"] for s in ok(client_a.get(_path(pid)))] == [second["id"]]
    history = ok(client_a.get(_path(pid), params={"include_history": "true"}))
    assert [s["id"] for s in history] == [first["id"], second["id"]]


def test_the_error_codes(client_a):
    pid = new_property(client_a)["id"]
    other = new_property(client_a, label="Wohnung Ring")["id"]
    row = create(client_a, _path(pid), _body(pid, is_default=True))

    response = client_a.post(_path(pid), json=_body(pid, levers={"sale_year": 2026}))
    assert _code(response) == (422, "scenario.sale_year_before_today")
    response = client_a.post(_path(pid), json=_body(pid, is_default=True, name="Zweiter"))
    assert _code(response) == (422, "scenario.not_current")
    response = client_a.post(_path(other), json=_body(pid))
    assert _code(response) == (422, "scenario.scope_invalid")
    their_loan = create(client_a, "/api/loans", loan_body(other))
    response = client_a.post(
        _path(pid), json=_body(pid, levers={"loans": [{"loan_id": their_loan["id"]}]})
    )
    assert _code(response) == (422, "scenario.scope_invalid")

    create(client_a, _path(pid), _body(pid, supersedes_id=row["id"]))
    response = client_a.post(_path(pid), json=_body(pid, supersedes_id=row["id"]))
    assert _code(response) == (422, "scenario.not_current")

    response = client_a.post(_path(pid), json=_body(pid, levers={"value_growth": "1.5"}))
    assert response.status_code == 422 and response.json()["error"] == "validation"
    response = client_a.get(_path(str(uuid4())))
    assert (response.status_code, response.json()["entity_type"]) == (404, "property")


def test_another_workspace_sees_nothing(client_a, client_b):
    pid = new_property(client_a)["id"]
    create(client_a, _path(pid), _body(pid, is_default=True))
    assert client_b.get(_path(pid)).status_code == 404
    assert client_b.post(_path(pid), json=_body(pid)).status_code == 404
