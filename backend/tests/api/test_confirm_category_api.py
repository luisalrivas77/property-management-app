"""POST /api/inbox/confirm-category, „Alle bestätigen“ (docs/phases/phase-6.md D159 amended
13 Nov 2026), through the API: the listed private lines are confirmed in one request with an
event each, a line that changed is skipped, a rental line and a refused category are coded
422s, and another workspace's lines are 404. The behaviour is covered in
tests/db/test_services_confirm_category.py. The model's proposals are written by the service
(no worker runs here)."""

from decimal import Decimal
from uuid import UUID

import pytest
import test_allocation_routes
from api_support import TOKEN_A, ok

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.allocation import AllocationDraft, AllocationMethod, AllocationSet
from hausbuch.schemas.enums import ActorType
from hausbuch.services import allocations
from hausbuch.services.context import Actor, new_command

pytestmark = pytest.mark.db

# The fixtures of the inbox route tests: users that exist, and the imported sample statement.
sessions = test_allocation_routes.sessions
lines = test_allocation_routes.lines

URL = "/api/inbox/confirm-category"
MODEL = Actor(ActorType.AI, "claude-test")


def _propose(app_engine, ws: UUID, line: dict, category: str) -> None:
    found = AllocationSet(
        transaction_id=line["id"],
        method=AllocationMethod.LLM,
        confidence=Decimal("0.700"),
        reasoning="Wahrscheinlich ein Restaurant.",
        allocations=[AllocationDraft(amount=Decimal(line["amount"]), category=category)],
    )
    with workspace_transaction(app_engine, ws) as conn:
        allocations.record_set(new_command(conn, ws, MODEL), found, confirm=False, auto=False)


@pytest.fixture
def private_lines(client_a, lines, sessions):
    """The imported lines on an account switched to private (no line booked yet)."""
    account_id = lines[0]["account_id"]
    ok(client_a.patch(f"/api/accounts/{account_id}", json={"purpose": "private"}))
    return [line for line in lines if line["amount"].startswith("-")]


def _ws(sessions) -> UUID:
    return sessions[TOKEN_A].workspace_id


def test_the_listed_lines_are_confirmed_and_a_changed_one_is_skipped(
    client_a,
    app_engine,
    sessions,
    private_lines,
):
    first, second, *rest = private_lines
    assert rest, "the sample statement has at least three debits"
    for line in (first, second):
        _propose(app_engine, _ws(sessions), line, "hh_dining")
    _propose(app_engine, _ws(sessions), rest[0], "hh_dining")

    items = {i["transaction_id"]: i for i in ok(client_a.get("/api/inbox"))}
    assert (items[first["id"]]["category"], items[first["id"]]["bulk_confirmable"]) == (
        "hh_dining",
        True,
    )
    _propose(app_engine, _ws(sessions), second, "hh_groceries")  # changed after the list

    body = {"category": "hh_dining", "transaction_ids": [first["id"], second["id"]]}
    result = ok(client_a.post(URL, json=body))
    assert result == {
        "confirmed": 1,
        "skipped": [{"transaction_id": second["id"], "reason": "category_changed"}],
    }
    view = ok(client_a.get(f"/api/transactions/{first['id']}/allocations"))
    assert [(a["category"], a["status"]) for a in view["allocations"]] == [
        ("hh_dining", "confirmed")
    ]
    events = ok(client_a.get("/api/events", params={"entity_id": first["id"]}))
    assert [(e["type"], e["actor_type"]) for e in events] == [("transaction.allocated", "user")]
    unlisted = ok(client_a.get(f"/api/transactions/{rest[0]['id']}/allocations"))
    assert unlisted["allocations"][0]["status"] == "proposed"


def test_refusals_are_coded_422s_and_confirm_nothing(
    client_a,
    app_engine,
    sessions,
    lines,
):
    rental = next(line for line in lines if line["amount"].startswith("-"))
    _propose(app_engine, _ws(sessions), rental, "other_expense")
    body = {"category": "hh_dining", "transaction_ids": [rental["id"]]}
    assert ok(client_a.post(URL, json=body), 422)["code"] == "inbox.line_not_private"
    body = {"category": "hh_donation", "transaction_ids": [rental["id"]]}
    assert ok(client_a.post(URL, json=body), 422)["code"] == "inbox.category_not_bulk"
    empty = client_a.post(URL, json={"category": "hh_dining", "transaction_ids": []})
    assert empty.status_code == 422
    view = ok(client_a.get(f"/api/transactions/{rental['id']}/allocations"))
    assert view["allocations"][0]["status"] == "proposed"


def test_another_workspace_gets_404(
    client_a,
    client_b,
    app_engine,
    sessions,
    private_lines,
):
    line = private_lines[0]
    _propose(app_engine, _ws(sessions), line, "hh_dining")
    body = {"category": "hh_dining", "transaction_ids": [line["id"]]}
    assert client_b.post(URL, json=body).status_code == 404
    view = ok(client_a.get(f"/api/transactions/{line['id']}/allocations"))
    assert view["allocations"][0]["status"] == "proposed"
