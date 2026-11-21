"""GET /properties/equity: the own money put into each property, Übersicht's „Eigenkapital
eingesetzt“ (Luis, 21 Nov 2026). Price + Nebenkosten rows + the loan costs the household paid
itself (private_cost rows of class financing in force, every year) − the loans' principal,
checked by hand. Invented values only."""

from decimal import Decimal

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, loan_body, new_property, ok
from db.db_support import _admin

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


def _equity(client) -> dict:
    return ok(client.get("/api/properties/equity"))


def _cost(client, property_id: str, kind: str, amount: str) -> dict:
    body = {"property_id": property_id, "kind": kind, "amount": amount}
    return create(client, "/api/acquisition-costs", body)


def _private(
    client,
    property_id: str,
    amount: str,
    *,
    cost_class: str = "financing",
    tax_year: int = 2026,
    supersedes: str | None = None,
    withdrawn: bool = False,
) -> dict:
    value = {"cost_class": cost_class, "label": "beleg", "amount": amount}
    body = {
        "property_id": property_id,
        "kind": "private_cost",
        "tax_year": tax_year,
        "value": value,
        "supersedes_id": supersedes,
        "withdrawn": withdrawn,
    }
    return create(client, f"/api/properties/{property_id}/tax-inputs", body)


def _loan(client, property_id: str, principal: str) -> dict:
    """A loan paid out before the handover; its rate is 1 % of the principal a month."""
    payment = f"{Decimal(principal) / 100:.2f}"
    body = loan_body(
        property_id,
        principal=principal,
        monthly_payment=payment,
        disbursed_on="2026-04-15",
        first_payment_on="2026-05-30",
        fixed_until="2036-04-30",
    )
    return create(client, "/api/loans", body)


def _bought_2026(client, price: str = "325000.00", label: str = "Wohnung A") -> str:
    return new_property(
        client,
        label=label,
        notarized_on="2026-02-01",
        purchase_date="2026-05-01",
        purchase_price=price,
    )["id"]


def test_the_parts_and_the_result_by_hand(client_a):
    """325,000.00 + (11,374.00 + 347.50) + (1,530.97 + 635.00) − 308,700.00 = 30,187.47."""
    pid = _bought_2026(client_a)
    _cost(client_a, pid, "real_estate_transfer_tax", "11374.00")
    _cost(client_a, pid, "land_registry", "347.50")
    _private(client_a, pid, "1530.97")
    _private(client_a, pid, "635.00", tax_year=2027)  # another year counts too
    _private(client_a, pid, "99.00", cost_class="postage")  # not a loan cost
    _loan(client_a, pid, "308700.00")

    body = _equity(client_a)
    assert body["properties"] == [
        {
            "property_id": pid,
            "label": "Wohnung A",
            "purchase_price": "325000.00",
            "acquisition_costs": "11721.50",
            "own_loan_costs": "2165.97",
            "loan_principal": "308700.00",
            "paid_in": "30187.47",
            "reason": None,
        }
    ]
    assert body["total_paid_in"] == "30187.47"


def test_the_nebenkosten_part_equals_the_property_pages_summe(client_a):
    pid = _bought_2026(client_a)
    _cost(client_a, pid, "notary", "2890.00")
    _cost(client_a, pid, "broker", "10115.00")
    summary = ok(client_a.get(f"/api/properties/{pid}/purchase-summary"))
    (row,) = _equity(client_a)["properties"]
    assert row["acquisition_costs"] == summary["acquisition_costs_total"] == "13005.00"


def test_a_corrected_or_withdrawn_row_counts_once(client_a):
    """A superseded row gives way to its correction and a withdrawn one counts nothing, so
    each loan cost counts once."""
    pid = _bought_2026(client_a, price="100000.00")
    first = _private(client_a, pid, "700.00")
    _private(client_a, pid, "635.00", supersedes=first["id"])
    gone = _private(client_a, pid, "50.00")
    _private(client_a, pid, "50.00", supersedes=gone["id"], withdrawn=True)

    (row,) = _equity(client_a)["properties"]
    assert row["own_loan_costs"] == "635.00"
    assert row["paid_in"] == "100635.00"


def test_a_loan_above_price_and_costs_is_negative_as_it_is(client_a):
    pid = _bought_2026(client_a, price="200000.00")
    _cost(client_a, pid, "notary", "1000.00")
    _loan(client_a, pid, "210000.00")
    body = _equity(client_a)
    assert body["properties"][0]["paid_in"] == "-9000.00"
    assert body["total_paid_in"] == "-9000.00"


def test_two_properties_and_their_loans_sum_to_the_total(client_a):
    a = _bought_2026(client_a, price="100000.00", label="A")
    b = _bought_2026(client_a, price="50000.00", label="B")
    for pid, principal in ((a, "80000.00"), (b, "45000.00"), (b, "1000.00")):
        _loan(client_a, pid, principal)
    body = _equity(client_a)
    assert [(p["label"], p["loan_principal"], p["paid_in"]) for p in body["properties"]] == [
        ("A", "80000.00", "20000.00"),
        ("B", "46000.00", "4000.00"),
    ]
    assert Decimal(body["total_paid_in"]) == Decimal("24000.00")


def test_no_property_serves_an_empty_list(client_a):
    assert _equity(client_a) == {"properties": [], "total_paid_in": "0.00"}


def test_another_workspace_sees_nothing(client_a, client_b):
    _bought_2026(client_a)
    assert _equity(client_b) == {"properties": [], "total_paid_in": "0.00"}
    assert len(_equity(client_a)["properties"]) == 1
