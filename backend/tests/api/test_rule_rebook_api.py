"""„Diese auch auf <Kategorie> umbuchen“ through the API (docs/phases/phase-6.md D160, 14 Nov
2026): GET /transactions/{id}/allocations names the payee's earlier confirmed lines by
category (name_history), and PUT with rebook_earlier re-books them, learns the name rule and
says how many it re-booked; without the flag the old rule.conflicts 422 stands. The behaviour
is covered in tests/db/test_services_private_matching.py. Invented names only."""

import hashlib
import uuid
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
import test_allocation_routes
from api_support import TOKEN_A, ok
from sqlalchemy import insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import transaction as transaction_table

pytestmark = pytest.mark.db

# The fixtures of the inbox route tests: users that exist, and the imported sample statement.
sessions = test_allocation_routes.sessions
lines = test_allocation_routes.lines

NAME = "Lieferdienst Beispiel"


@pytest.fixture
def account(client_a, lines, sessions) -> dict:
    """The sample statement's account switched to private, and its reconciled import."""
    account_id = lines[0]["account_id"]
    ok(client_a.patch(f"/api/accounts/{account_id}", json={"purpose": "private"}))
    return {"id": UUID(account_id), "import_id": UUID(lines[0]["import_id"])}


def _line(app_engine, ws: UUID, account: dict, day: int, amount: str) -> str:
    """A card payment to the delivery service: a name and no IBAN."""
    with workspace_transaction(app_engine, ws) as conn:
        return str(
            conn.execute(
                insert(transaction_table)
                .values(
                    workspace_id=ws,
                    account_id=account["id"],
                    import_id=account["import_id"],
                    line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                    booking_date=date(2026, 10, day),
                    value_date=date(2026, 10, day),
                    amount=Decimal(amount),
                    counterparty_name=NAME,
                    purpose="Kartenzahlung",
                )
                .returning(transaction_table.c.id)
            ).scalar_one()
        )


def _body(tx: str, amount: str, category: str, **extra) -> dict:
    return {
        "allocation_set": {
            "transaction_id": tx,
            "allocations": [{"amount": amount, "category": category}],
            "method": "user",
            "confidence": "1.000",
        },
        **extra,
    }


def test_the_earlier_lines_are_named_and_re_booked_with_the_flag(
    client_a, app_engine, sessions, account
):
    ws = sessions[TOKEN_A].workspace_id
    earlier = [_line(app_engine, ws, account, day, f"-1{day}.90") for day in (1, 2, 3)]
    for day, tx in zip((1, 2, 3), earlier, strict=True):
        url = f"/api/transactions/{tx}/allocations"
        ok(client_a.put(url, json=_body(tx, f"-1{day}.90", "hh_leisure_travel", learn_rule=False)))
    now = _line(app_engine, ws, account, 20, "-27.50")
    url = f"/api/transactions/{now}/allocations"

    view = ok(client_a.get(url))
    assert view["can_learn_rule"] is True
    assert view["name_history"] == [{"category": "hh_leisure_travel", "count": 3}]
    assert view["rebooked"] == 0

    refused = ok(client_a.put(url, json=_body(now, "-27.50", "hh_dining", learn_rule=True)), 422)
    assert refused["code"] == "rule.conflicts"
    assert ok(client_a.get(url))["allocations"] == []  # nothing written

    done = ok(
        client_a.put(
            url, json=_body(now, "-27.50", "hh_dining", learn_rule=True, rebook_earlier=True)
        )
    )
    assert done["rebooked"] == 3
    assert done["rule"]["category"] == "hh_dining"
    assert done["name_history"] == [{"category": "hh_dining", "count": 3}]
    for tx in earlier:
        rows = ok(client_a.get(f"/api/transactions/{tx}/allocations"))["allocations"]
        assert [(r["category"], r["status"], r["method"]) for r in rows] == [
            ("hh_dining", "confirmed", "user")
        ]
        assert rows[0]["confirmed_by"] is not None  # the signed-in person
        events = ok(client_a.get("/api/events", params={"entity_id": tx}))
        assert [e["type"] for e in events] == ["transaction.allocated", "transaction.reallocated"]
