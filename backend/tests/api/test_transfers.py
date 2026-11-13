"""The transfer and Haushalt routes (docs/phases/phase-6.md §12, D162): status codes, the
read shapes and workspace isolation. The service behaviour is
tests/db/test_services_transfers.py. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from api_support import ok
from db.db_support import TEST_IBAN_KEY, _admin
from db.matching_support import ACCOUNT_IBAN, World
from db.test_services_private_matching import NOW, PRIVATE_IBAN, Private
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.services import allocations, matching

pytestmark = pytest.mark.db

C = AllocationCategory
SAVINGS_IBAN = "DE73100000000000000072"  # Bundesbank test bank code 10000000


@pytest.fixture
def world(app_engine, auth_a) -> World:
    return World(app_engine, auth_a.workspace_id, now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


@pytest.fixture
def user_a(auth_a, dbname):
    """client_a's user as a real app_user (tests/api/test_accounts.py's pattern)."""
    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )


def _book(world: World, tx, amount: str, category: C) -> None:
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
    )


def _savings(world: World) -> Private:
    """A Tagesgeld account of the household, with its own reconciled import."""
    savings = Private.__new__(Private)
    savings.world = world
    with workspace_transaction(world.engine, world.ws) as conn:
        savings.account_id = world._account(conn, SAVINGS_IBAN, owner=True)
        conn.execute(
            update(account_table)
            .where(account_table.c.id == savings.account_id)
            .values(kind="bank_savings")
        )
    savings.import_id = savings.add_import(world.now)
    return savings


def test_a_link_by_hand_its_replacement_and_isolation(client_a, client_b, user_a, world, private):
    leg = private.line(date(2026, 10, 5), "-200.00", iban=ACCOUNT_IBAN, purpose="Rücklage")
    world.cmd(matching.match_line, leg, iban_key=TEST_IBAN_KEY, ai_available=False)
    late = world.line(date(2026, 10, 20), "200.00", name="Konto B")
    other = world.line(date(2026, 10, 21), "200.00", name="Konto B")

    assert ok(client_a.get("/api/transfers", params={"month": "2026-10"})) == []
    [card] = ok(client_a.get("/api/transfers/open"))
    assert (card["code"], card["leg"]["id"]) == ("transfer.unmatched_leg", str(leg))
    assert {c["id"] for c in card["candidates"]} == {str(late), str(other)}
    assert "iban" not in str(card).lower()
    # Zu prüfen shows the same card, and no property-cost card for this line.
    inbox = ok(client_a.get("/api/inbox"))
    cards = [(i["kind"], i["transaction_id"]) for i in inbox if i["kind"].startswith("transfer")]
    assert cards == [("transfer_unmatched", str(leg))]
    assert not [i for i in inbox if i["kind"].startswith("property_cost")]

    body = {"kind": "bank", "out_transaction_id": str(leg), "in_transaction_id": str(late)}
    link = ok(client_a.post("/api/transfers", json=body), 201)
    assert (link["matched_by"], link["superseded_by"]) == ("user", None)
    again = client_a.post("/api/transfers", json=body)  # the same pair again: no conflict
    assert again.status_code in (200, 201) and again.json()["id"] == link["id"]
    [view] = ok(client_a.get("/api/transfers", params={"month": "2026-10"}))
    assert (view["out_leg"]["id"], view["in_leg"]["amount"]) == (str(leg), "200.00")
    assert ok(client_a.get("/api/transfers/open")) == []
    assert not [i for i in ok(client_a.get("/api/inbox")) if i["kind"].startswith("transfer")]

    # Another workspace sees none of it and cannot touch it.
    assert ok(client_b.get("/api/transfers", params={"month": "2026-10"})) == []
    assert ok(client_b.get("/api/transfers/open")) == []
    replacement = {"replacement": body | {"in_transaction_id": str(other)}}
    assert client_b.post(f"/api/transfers/{link['id']}/unlink", json=replacement).status_code == 404

    new = ok(client_a.post(f"/api/transfers/{link['id']}/unlink", json=replacement), 201)
    assert new["in_transaction_id"] == str(other)
    assert client_a.post(f"/api/transfers/{link['id']}/unlink", json=replacement).status_code == 409
    [view] = ok(client_a.get("/api/transfers", params={"month": "2026-10"}))
    assert view["id"] == new["id"]


def test_invalid_bodies_and_months(client_a, world, private):
    a = private.line(date(2026, 10, 5), "-50.00")
    b = private.line(date(2026, 10, 5), "50.00")
    same_account = {"kind": "bank", "out_transaction_id": str(a), "in_transaction_id": str(b)}
    response = client_a.post("/api/transfers", json=same_account)
    assert response.status_code == 422
    assert response.json()["code"] == "transfer.legs_invalid"
    assert client_a.post("/api/transfers", json={"kind": "bank"}).status_code == 422
    for month in ("2026-13", "2026-1", "Oktober"):
        assert client_a.get("/api/transfers", params={"month": month}).status_code == 422
        assert client_a.get("/api/household/cashflow", params={"month": month}).status_code == 422


def test_the_haushalt_month(client_a, client_b, world, private):
    savings = _savings(world)
    _book(
        world,
        private.line(date(2026, 10, 1), "3200.00", name="Arbeitgeber A"),
        "3200.00",
        C.HH_SALARY,
    )
    _book(
        world, private.line(date(2026, 10, 4), "-450.00", name="Markt"), "-450.00", C.HH_GROCERIES
    )
    private.line(date(2026, 10, 9), "-19.99", name="Unbekannt")  # waits in Zu prüfen
    out = private.line(date(2026, 10, 12), "-500.00", iban=SAVINGS_IBAN, purpose="Sparen")
    in_ = savings.line(date(2026, 10, 13), "500.00", iban=PRIVATE_IBAN, purpose="Sparen")
    for tx in (out, in_):
        world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)
    private.line(date(2026, 11, 2), "-70.00", name="Markt")  # another month

    got = ok(client_a.get("/api/household/cashflow", params={"month": "2026-10"}))
    assert got == {
        "month": "2026-10",
        "income": "3200.00",
        "spending_by_group": {"groceries": "450.00"},
        "saved": "500.00",
        "surplus": "2750.00",
        "savings_rate": "0.8594",
        "income_by_category": {"hh_salary": "3200.00"},
        "open_lines": 1,
    }
    other = ok(client_b.get("/api/household/cashflow", params={"month": "2026-10"}))
    assert (other["income"], other["open_lines"], other["savings_rate"]) == ("0.00", 0, None)
