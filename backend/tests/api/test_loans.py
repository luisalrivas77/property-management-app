"""Loans over HTTP (§4.6): the schedule, and the amortisation check on every write."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from api_support import GOOD_IBAN, TEST_IBAN_KEY, create, loan_body, new_property, ok

from hausbuch.api.commands import Commands
from hausbuch.schemas.loan import LoanCreate
from hausbuch.services.loans import create_loan

pytestmark = pytest.mark.db


def test_round_trip_and_schedule(client_a):
    prop = new_property(client_a)
    loan = create(client_a, "/api/loans", loan_body(prop["id"], account_iban=GOOD_IBAN))
    assert loan["account_iban_masked"] == "DE89 **** 3000"
    assert loan["terms"]["principal"] == "240000.00"
    assert loan["terms"]["nominal_rate"] == "0.036500"
    assert ok(client_a.get(f"/api/loans/{loan['id']}")) == loan

    schedule = ok(client_a.get(f"/api/loans/{loan['id']}/schedule"))
    first = schedule["entries"][0]
    assert (first["number"], first["due_on"], first["opening_balance"]) == (
        1,
        "2024-05-30",
        "240000.00",
    )
    assert all(isinstance(first[k], str) for k in ("interest", "principal", "payment"))
    assert schedule["years"][0]["year"] == 2024

    patched = ok(client_a.patch(f"/api/loans/{loan['id']}", json={"follow_up_rate": "0.04"}))
    assert patched["terms"]["follow_up_rate"] == "0.040000"


def test_signed_on_is_the_history_date_of_the_contract(client_a):
    prop = new_property(client_a)
    loan = create(client_a, "/api/loans", loan_body(prop["id"]) | {"signed_on": "2024-03-01"})
    created, disbursed = ok(client_a.get(f"/api/events?entity_id={loan['id']}"))
    assert (created["type"], created["effective_date"]) == ("loan.created", "2024-03-01")
    assert (disbursed["type"], disbursed["effective_date"]) == ("loan.disbursed", "2024-04-30")

    response = client_a.post("/api/loans", json=loan_body(prop["id"]) | {"signed_on": "2024-05-01"})
    assert (response.status_code, response.json()["error"]) == (422, "invalid")


def test_a_payment_that_never_amortises_is_refused_on_create(client_a):
    prop = new_property(client_a)
    response = client_a.post("/api/loans", json=loan_body(prop["id"], monthly_payment="500.00"))
    assert response.status_code == 422
    body = response.json()
    assert (body["error"], body["code"], body["at_number"]) == (
        "rule_violation",
        "loan.payment_does_not_cover_interest",
        1,
    )
    assert ok(client_a.get(f"/api/properties/{prop['id']}/loans")) == []


def test_a_patch_that_never_amortises_is_rolled_back(client_a):
    """The check runs inside the command's transaction, after update_loan wrote the row and
    its loan.updated event: both are rolled back."""
    prop = new_property(client_a)
    loan = create(client_a, "/api/loans", loan_body(prop["id"]))
    response = client_a.patch(f"/api/loans/{loan['id']}", json={"monthly_payment": "500.00"})
    assert response.status_code == 422
    assert response.json()["code"] == "loan.payment_does_not_cover_interest"
    assert ok(client_a.get(f"/api/loans/{loan['id']}")) == loan
    types = [e["type"] for e in ok(client_a.get(f"/api/events?entity_id={loan['id']}"))]
    assert types == ["loan.created", "loan.disbursed"]


def test_the_disbursement_is_corrected_and_history_shows_it(client_a):
    prop = new_property(client_a)
    loan = create(client_a, "/api/loans", loan_body(prop["id"]))
    path = f"/api/loans/{loan['id']}/corrections/disbursement"

    corrected = ok(client_a.post(path, json={"new_date": "2024-04-29", "principal": "235000.00"}))
    assert (corrected["terms"]["disbursed_on"], corrected["terms"]["principal"]) == (
        "2024-04-29",
        "235000.00",
    )
    schedule = ok(client_a.get(f"/api/loans/{loan['id']}/schedule"))
    assert schedule["entries"][0]["opening_balance"] == "235000.00"
    [payout] = [
        h
        for h in ok(client_a.get(f"/api/properties/{prop['id']}/history"))
        if h["type"] == "loan.disbursed"
    ]
    assert (payout["effective_date"], payout["corrected"], payout["original_effective_date"]) == (
        "2024-04-29",
        True,
        "2024-04-30",
    )
    assert payout["payload"] == {"amount": "235000.00"}

    assert ok(client_a.post(path, json={"principal": "235000.00"})) == corrected  # no-op
    for body in ({}, {"new_date": None, "principal": None}, {"principal": 235000}):
        response = client_a.post(path, json=body)
        assert response.status_code == 422, body
    response = client_a.post(f"/api/loans/{uuid4()}/corrections/disbursement", json=body)
    assert response.status_code == 422  # the body is checked first
    response = client_a.post(
        f"/api/loans/{uuid4()}/corrections/disbursement", json={"new_date": "2024-04-29"}
    )
    assert (response.status_code, response.json()["entity_type"]) == (404, "loan")


def test_a_disbursement_correction_that_never_amortises_is_rolled_back(client_a):
    prop = new_property(client_a)
    loan = create(client_a, "/api/loans", loan_body(prop["id"]))
    response = client_a.post(
        f"/api/loans/{loan['id']}/corrections/disbursement", json={"principal": "500000.00"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "loan.payment_does_not_cover_interest"
    assert ok(client_a.get(f"/api/loans/{loan['id']}")) == loan
    types = [e["type"] for e in ok(client_a.get(f"/api/events?entity_id={loan['id']}"))]
    assert types == ["loan.created", "loan.disbursed"]


def test_a_stored_loan_that_never_amortises_answers_409(client_a, app_engine, auth_a):
    """Data from before the check (written through the service directly)."""
    prop = new_property(client_a)
    stored = Commands(app_engine, auth_a, uuid4()).run(
        create_loan,
        LoanCreate(
            property_id=prop["id"],
            lender="Altbank",
            principal=Decimal("240000.00"),
            nominal_rate=Decimal("0.0365"),
            monthly_payment=Decimal("500.00"),
            disbursed_on=date(2024, 4, 30),
            first_payment_on=date(2024, 5, 30),
        ),
        iban_key=TEST_IBAN_KEY,
    )
    response = client_a.get(f"/api/loans/{stored.id}/schedule")
    assert response.status_code == 409
    body = response.json()
    assert (body["error"], body["code"], body["at_number"]) == (
        "rule_violation",
        "loan.payment_does_not_cover_interest",
        1,
    )


def test_unknown_loans_are_404(client_a):
    missing = uuid4()
    for sub in ("", "/schedule", "/documents"):
        response = client_a.get(f"/api/loans/{missing}{sub}")
        assert (response.status_code, response.json()["entity_type"]) == (404, "loan"), sub
    response = client_a.post("/api/loans", json=loan_body(str(missing)))
    assert (response.status_code, response.json()["entity_type"]) == (404, "property")
