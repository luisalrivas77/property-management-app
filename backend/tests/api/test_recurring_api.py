"""GET /api/household/recurring, POST /api/household/one-off-marks and POST
/api/household/recurring-marks (docs/phases/phase-10.md §7.9 (b), (c), §7.12 (b); D296, D297,
D307) over HTTP: status codes, the read shapes, validation and workspace isolation. GET
/api/household/one-off-candidates went with D307. What a mark changes is
tests/db/test_services_recurring.py; the arithmetic is the phase 10 fixtures."""

from uuid import uuid4

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, ok
from db.db_support import _admin

pytestmark = pytest.mark.db

RECURRING = "/api/household/recurring"
CANDIDATES = "/api/household/one-off-candidates"
ONE_OFF = "/api/household/one-off-marks"
SERIES = "/api/household/recurring-marks"


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


def test_the_reads_of_an_empty_household(client_a, sessions):
    got = ok(client_a.get(RECURRING))
    assert got["history_start"] == "2026-01-01" and got["as_of"]
    assert (got["count"], got["series"], got["hidden"]) == (0, [], [])
    assert got["yearly_total"] == "0.00"
    assert client_a.get(CANDIDATES).status_code == 404  # removed by D307


def test_a_series_answer_is_stored(client_a, client_b, sessions):
    body = {"key": "STREAMDIENST MUSTER", "interval": "monthly", "answer": "hidden"}
    response = client_a.post(SERIES, json=body)
    assert response.status_code == 201
    got = response.json()
    assert got["answer"] == "hidden" and got["id"] and got["created_at"]
    for wrong in (
        body | {"answer": "one_off"},
        body | {"interval": "weekly"},
        body | {"key": ""},
        {"key": "X", "interval": "monthly"},
    ):
        assert client_a.post(SERIES, json=wrong).status_code == 422, wrong
    assert ok(client_b.get(RECURRING))["hidden"] == []


def test_a_payment_mark_needs_a_spending_payment_of_the_workspace(client_a, sessions):
    response = client_a.post(ONE_OFF, json={"allocation_id": str(uuid4()), "answer": "one_off"})
    assert (response.status_code, response.json()["entity_type"]) == (404, "allocation")
    for wrong in (
        {"allocation_id": str(uuid4()), "answer": "hidden"},
        {"allocation_id": "nope", "answer": "one_off"},
        {"answer": "one_off"},
    ):
        assert client_a.post(ONE_OFF, json=wrong).status_code == 422, wrong
