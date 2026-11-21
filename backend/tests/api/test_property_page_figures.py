"""The property pages' four served figures (phase 10, mockup 25): GET /properties/{id}/
purchase-summary (the Nebenkosten's Summe and the AfA line of the selected split) and GET
/loans/{id}/summary (Restschuld heute and the anfängliche Tilgung). Each figure is checked
against the source it reuses: the Anlage V draft's afa line, the cost rows the AfA basis sums,
the net worth's loan line on the same day, and a hand calculation. Invented values only."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from api_support import (
    TEST_IBAN_KEY,
    TOKEN_A,
    TOKEN_B,
    auth_for,
    create,
    flat,
    loan_body,
    new_property,
    new_unit,
    ok,
    upload,
)
from db.db_support import _admin

from hausbuch.api.commands import Commands
from hausbuch.rules.loan_schedule import initial_repayment_rate
from hausbuch.schemas.loan import LoanCreate, LoanTerms
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.loans import create_loan

pytestmark = pytest.mark.db


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so a tax input's recorded_by (FK to app_user) is real."""
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


def _summary(client, property_id: str) -> dict:
    return ok(client.get(f"/api/properties/{property_id}/purchase-summary"))


def _draft_afa(client, property_id: str, tax_year: int) -> dict:
    draft = ok(client.get(f"/api/properties/{property_id}/anlage-v", params={"tax_year": tax_year}))
    return draft["afa"]


def _cost(client, property_id: str, kind: str, amount: str) -> dict:
    body = {"property_id": property_id, "kind": kind, "amount": amount}
    return create(client, "/api/acquisition-costs", body)


def _select_split(client, property_id: str, share: str) -> dict:
    split = create(
        client,
        "/api/price-splits",
        {"property_id": property_id, "source": "purchase_contract", "building_share": share},
    )
    ok(client.post(f"/api/properties/{property_id}/price-splits/{split['id']}/select"))
    return split


def _this_year() -> int:
    return datetime.now(LOCAL_TIME_ZONE).date().year


# --- Nebenkosten Summe and the AfA line ---------------------------------------------------


def test_the_summe_and_the_afa_basis_are_the_drafts_own(client_a):
    """Price 289,000.00 + Nebenkosten 21,000.00 = 310,000.00, × 0.75 = 232,500.00 (the rule's
    arithmetic); the summary's figures are compared with what the draft and the rows say."""
    pid = flat(client_a)["property"]["id"]  # bought 1 May 2024, build year 1994
    _cost(client_a, pid, "real_estate_transfer_tax", "17500.00")
    _cost(client_a, pid, "notary", "3500.00")
    _select_split(client_a, pid, "0.75")

    summary = _summary(client_a, pid)
    rows = ok(client_a.get(f"/api/properties/{pid}/acquisition-costs"))
    assert summary["acquisition_cost_count"] == len(rows) == 2
    assert Decimal(summary["acquisition_costs_total"]) == sum(Decimal(r["amount"]) for r in rows)
    assert summary["acquisition_costs_total"] == "21000.00"

    year = summary["afa"]["tax_year"]
    assert year == _this_year()  # the purchase year is earlier
    assert summary["afa"] == _draft_afa(client_a, pid, year)
    assert (summary["afa"]["basis"], summary["afa"]["rate"]) == ("232500.00", "0.02")
    assert summary["afa"]["method"] == "statutory"


def test_without_a_split_the_summe_is_served_and_the_basis_is_null(client_a):
    pid = flat(client_a)["property"]["id"]
    _cost(client_a, pid, "notary", "3500.00")
    summary = _summary(client_a, pid)
    assert (summary["acquisition_costs_total"], summary["acquisition_cost_count"]) == (
        "3500.00",
        1,
    )
    afa = summary["afa"]
    assert (afa["basis"], afa["rate"], afa["amount"]) == (None, None, None)
    assert afa == _draft_afa(client_a, pid, afa["tax_year"])


def test_no_rows_sum_to_zero(client_a):
    pid = flat(client_a)["property"]["id"]
    summary = _summary(client_a, pid)
    assert (summary["acquisition_costs_total"], summary["acquisition_cost_count"]) == (
        "0.00",
        0,
    )


def test_the_gutachten_method_is_the_drafts(client_a):
    """D79: a Gutachten's shorter useful life; the line has no rate, the years instead."""
    prop = new_property(
        client_a,
        build_year=1904,
        notarized_on="2025-08-01",
        purchase_date="2025-10-15",
        purchase_price="375000.00",
    )
    pid = prop["id"]
    new_unit(client_a, pid)
    _select_split(client_a, pid, "0.8")
    doc = ok(upload(client_a, b"%PDF-1.7 Gutachten Nutzungsdauer", filename="gutachten.pdf"), 201)
    create(
        client_a,
        f"/api/properties/{pid}/tax-inputs",
        {
            "property_id": pid,
            "kind": "useful_life",
            "tax_year": 2025,
            "document_id": doc["id"],
            "value": {"years": 21},
        },
    )
    afa = _summary(client_a, pid)["afa"]
    assert (afa["method"], afa["rate"], afa["useful_life_years"]) == ("useful_life", None, 21)
    assert afa == _draft_afa(client_a, pid, afa["tax_year"])


def test_a_fitted_kitchen_reduces_the_basis_as_the_draft_does(client_a):
    """D80: the kitchen's share (with its Nebenkosten) leaves the building basis."""
    pid = flat(client_a)["property"]["id"]
    _cost(client_a, pid, "notary", "2890.00")
    _select_split(client_a, pid, "0.75")
    create(
        client_a,
        f"/api/properties/{pid}/tax-inputs",
        {
            "property_id": pid,
            "kind": "movable_asset",
            "tax_year": 2024,
            "value": {"label": "einbaukueche", "amount": "10000.00"},
        },
    )
    afa = _summary(client_a, pid)["afa"]
    # its basis: 10,000.00 × (289,000.00 + 2,890.00) ÷ 289,000.00 = 10,100.00
    assert afa["movable_deducted"] == "10100.00"
    # (289,000.00 + 2,890.00) × 0.75 = 218,917.50, less the kitchen
    assert afa["basis"] == "208817.50"
    assert afa == _draft_afa(client_a, pid, afa["tax_year"])


def test_a_purchase_year_without_a_rule_set_serves_the_summe_alone(client_a):
    """Bought in a year with no rule set file yet: no AfA line, the Summe still served."""
    pid = new_property(
        client_a, notarized_on="2099-01-02", purchase_date="2099-02-01", purchase_price="100000.00"
    )["id"]
    _cost(client_a, pid, "notary", "1000.00")
    _select_split(client_a, pid, "0.8")
    summary = _summary(client_a, pid)
    assert (summary["acquisition_costs_total"], summary["afa"]) == ("1000.00", None)


def test_the_purchase_summary_of_an_unknown_or_foreign_property_is_404(client_a, client_b):
    pid = flat(client_a)["property"]["id"]
    for client, property_id in ((client_a, str(uuid4())), (client_b, pid)):
        response = client.get(f"/api/properties/{property_id}/purchase-summary")
        assert (response.status_code, response.json()["entity_type"]) == (404, "property")


# --- Restschuld heute and the anfängliche Tilgung ----------------------------------------


def test_the_balance_today_is_the_net_worths_loan_line(client_a):
    pid = flat(client_a)["property"]["id"]  # handed over 1 May 2024, so the schedule counts
    loan = create(client_a, "/api/loans", loan_body(pid))
    summary = ok(client_a.get(f"/api/loans/{loan['id']}/summary"))
    on = summary["balance_on"]
    net_worth = ok(client_a.get("/api/net-worth", params={"on": on}))
    (line,) = [x for x in net_worth["lines"] if x["loan_id"] == loan["id"]]
    assert Decimal(summary["balance_today"]) == -Decimal(line["value_eur"])
    # and it is a closing balance of the schedule the loan page shows
    schedule = ok(client_a.get(f"/api/loans/{loan['id']}/schedule"))
    due = [e for e in schedule["entries"] if e["due_on"] <= on]
    assert summary["balance_today"] == due[-1]["closing_balance"]


def test_the_initial_repayment_rate_by_hand(client_a):
    """1,130.00 × 12 = 13,560.00 a year; ÷ 240,000.00 = 0.0565; less 3.65 % = 0.0200."""
    pid = new_property(client_a)["id"]
    loan = create(client_a, "/api/loans", loan_body(pid))
    summary = ok(client_a.get(f"/api/loans/{loan['id']}/summary"))
    assert summary["initial_repayment_rate"] == "0.020000"


def test_the_initial_repayment_rate_rounds_to_six_places():
    """The fraction is rounded half-up to a Rate's 6 places."""
    terms = LoanTerms(
        principal=Decimal("70000.00"),
        nominal_rate=Decimal("0.031"),
        monthly_payment=Decimal("333.33"),
        disbursed_on=date(2026, 1, 1),
        first_payment_on=date(2026, 2, 1),
    )
    # 333.33 × 12 = 3,999.96; ÷ 70,000.00 = 0.05714228…; less 0.031 = 0.02614228… → 0.026142
    assert initial_repayment_rate(terms) == Decimal("0.026142")
    # A payment that repays more than the principal in a year is no Rate: None, never a 500.
    short = terms.model_copy(update={"monthly_payment": Decimal("7000.00")})
    assert initial_repayment_rate(short) is None


def test_before_the_payout_the_balance_is_null(client_a):
    pid = new_property(client_a)["id"]
    today = datetime.now(LOCAL_TIME_ZONE).date()
    payout = today + timedelta(days=40)
    loan = create(
        client_a,
        "/api/loans",
        loan_body(
            pid,
            disbursed_on=payout.isoformat(),
            first_payment_on=(payout + timedelta(days=31)).isoformat(),
            fixed_until=None,
        ),
    )
    summary = ok(client_a.get(f"/api/loans/{loan['id']}/summary"))
    assert summary["balance_today"] is None
    assert summary["initial_repayment_rate"] == "0.020000"


def test_a_stored_loan_that_never_amortises_answers_409(client_a, app_engine, auth_a):
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
    response = client_a.get(f"/api/loans/{stored.id}/summary")
    assert response.status_code == 409
    assert response.json()["code"] == "loan.payment_does_not_cover_interest"


def test_the_loan_summary_of_an_unknown_or_foreign_loan_is_404(client_a, client_b):
    pid = new_property(client_a)["id"]
    loan = create(client_a, "/api/loans", loan_body(pid))
    for client, loan_id in ((client_a, str(uuid4())), (client_b, loan["id"])):
        response = client.get(f"/api/loans/{loan_id}/summary")
        assert (response.status_code, response.json()["entity_type"]) == (404, "loan")
