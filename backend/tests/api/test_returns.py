"""POST /returns and the „Rücküberweisung erkannt“ card over HTTP (docs/phases/phase-6.md,
D162 amended (7)): status codes, the inbox shape and workspace isolation. The service
behaviour is tests/db/test_services_returns.py. Invented names, IBANs and amounts only."""

from datetime import date

import pytest
from api_support import ok
from db.db_support import _admin
from db.matching_support import World
from db.test_services_private_matching import NOW, Private

pytestmark = pytest.mark.db

FRIEND_IBAN = "DE46100000000000000073"  # a third party's account, not the household's
RECALL = "RUECKUEBERWEISUNG Rueckgabe aufgrund eines Recalls (Rueckrufes) Umbuchung Juli"


@pytest.fixture
def world(app_engine, auth_a) -> World:
    return World(app_engine, auth_a.workspace_id, now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


@pytest.fixture
def user_a(auth_a, dbname):
    """client_a's user as a real app_user, for the confirmed answers' confirmed_by
    (tests/api/test_transfers.py's pattern)."""
    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )


def test_the_card_its_button_and_isolation(client_a, client_b, user_a, world, private):
    debit = private.line(date(2026, 7, 28), "-3200.00", iban=FRIEND_IBAN, purpose="Umbuchung Juli")
    credit = private.line(date(2026, 7, 30), "3200.00", iban=FRIEND_IBAN, purpose=RECALL)
    other = private.line(date(2026, 7, 29), "-100.00", iban=FRIEND_IBAN)

    [card] = [i for i in ok(client_a.get("/api/inbox")) if i["kind"] == "returned_payment"]
    assert card["transaction_id"] == str(credit)
    assert card["returned"]["leg"]["id"] == str(credit)
    assert [c["id"] for c in card["returned"]["candidates"]] == [str(debit)]
    assert "iban" not in str(card["returned"]).lower()

    body = {"out_transaction_id": str(debit), "in_transaction_id": str(credit)}
    # Another workspace sees none of it and cannot book it.
    assert not [i for i in ok(client_b.get("/api/inbox")) if i["kind"] == "returned_payment"]
    assert client_b.post("/api/returns", json=body).status_code == 404

    wrong = client_a.post("/api/returns", json=body | {"out_transaction_id": str(other)})
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "transfer.return_invalid"
    assert client_a.post("/api/returns", json={"out_transaction_id": str(debit)}).status_code == 422

    link = ok(client_a.post("/api/returns", json=body), 201)
    assert (link["out_transaction_id"], link["in_transaction_id"]) == (str(debit), str(credit))
    assert (link["matched_by"], link["kind"]) == ("user", "bank")
    again = client_a.post("/api/returns", json=body)  # the same pair again: its link
    assert again.status_code in (200, 201) and again.json()["id"] == link["id"]
    assert not [i for i in ok(client_a.get("/api/inbox")) if i["kind"] == "returned_payment"]

    lines = {
        t["id"]: t for t in ok(client_a.get(f"/api/accounts/{private.account_id}/transactions"))
    }
    assert lines[str(debit)]["returned_with"]["transaction_id"] == str(credit)
    assert lines[str(credit)]["returned_with"]["booking_date"] == "2026-07-28"
    assert lines[str(other)]["returned_with"] is None
