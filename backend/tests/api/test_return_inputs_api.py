"""The income tax return's read routes over HTTP (docs/phases/phase-4b.md §9, §10): the years an
Anlage N exists for, and a return input's evidence (its Beleg and, for a pre-filled row, each
value's page and quote; tests/db/test_services_proposals_return_inputs.py covers that part)."""

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

from hausbuch.rules.tax import registry

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


def test_the_anlage_n_years_are_the_rule_set_files(client_a):
    got = ok(client_a.get("/api/tax/anlage-n/years"))
    assert got == {"years": list(registry.anlage_n_years())}
    assert 2026 in got["years"]


def test_the_evidence_of_an_unknown_row_is_404(client_a):
    got = client_a.get("/api/return-inputs/00000000-0000-4000-8000-000000000001/evidence")
    assert got.status_code == 404


def test_a_typed_rows_evidence_names_no_document(client_a, client_b):
    owner = create(client_a, "/api/owners", {"full_name": "Person A"})
    row = create(
        client_a,
        "/api/return-inputs",
        {
            "owner_id": owner["id"],
            "kind": "training",
            "tax_year": 2026,
            "value": {"label": "Fachbuch", "amount": "40.00"},
        },
    )
    got = ok(client_a.get(f"/api/return-inputs/{row['id']}/evidence"))
    assert got["return_input_id"] == row["id"]
    assert got["document_id"] is None and got["values"] == [] and got["opens_inline"] is False
    # another workspace's row is not found (RLS)
    assert client_b.get(f"/api/return-inputs/{row['id']}/evidence").status_code == 404


def test_the_anlage_n_years_need_a_session(api_app):
    from api_support import client_for

    with client_for(api_app, None) as anonymous:
        assert anonymous.get("/api/tax/anlage-n/years").status_code == 401


# --- Phase 6 D159 amended (8): booked wage-replacement lines become a to-do ----------------


def _benefit_todos(client) -> list[dict]:
    estimate = ok(client.get("/api/tax/return-estimate", params={"tax_year": 2026}))
    return [w for w in estimate["warnings"] if w["code"] == "return.benefit_booked"]


def test_a_booked_benefit_is_a_to_do_until_it_is_typed(client_a, client_b):
    """A confirmed hh_wage_replacement line on a private account: on a joint account the to-do
    names no owner, on a one-owner account that owner, and the typed row ends it."""
    from test_bank_parsers import CAMT, build

    case = next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports")
    a = create(client_a, "/api/owners", {"full_name": "Person A"})["id"]
    b = create(client_a, "/api/owners", {"full_name": "Person B"})["id"]
    account = create(
        client_a,
        "/api/accounts",
        {
            "label": "Girokonto",
            "bank": "Testbank",
            "iban": "DE50100000000000000001",  # the fixture file's own
            "purpose": "private",
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
        },
    )
    files = {"file": ("umsaetze.zip", build(case.input), "application/zip")}
    ok(client_a.post(f"/api/accounts/{account['id']}/imports", files=files), 201)
    lines = ok(client_a.get(f"/api/accounts/{account['id']}/transactions"))
    line = next(t for t in lines if not t["amount"].startswith("-") and t["amount"] != "0.00")
    assert line["booking_date"].startswith("2026-")
    assert _benefit_todos(client_a) == []

    body = {
        "allocation_set": {
            "transaction_id": line["id"],
            "allocations": [{"amount": line["amount"], "category": "hh_wage_replacement"}],
            "method": "user",
            "confidence": "1.000",
        },
        "learn_rule": False,
    }
    ok(client_a.put(f"/api/transactions/{line['id']}/allocations", json=body))
    url = f"/api/accounts/{account['id']}/ownership"
    half = {"share_numerator": 1, "share_denominator": 2}
    ok(client_a.post(url, json={"shares": [{"owner_id": a, **half}, {"owner_id": b, **half}]}))
    (todo,) = _benefit_todos(client_a)
    assert "owner_id" not in todo["params"]
    assert todo["params"]["difference"] == line["amount"]
    assert todo["refs"][0]["kind"] == "allocation"
    # The line's payee and purpose never leave the server: amounts, year and codes only.
    assert set(todo["params"]) <= {"tax_year", "booked", "typed", "difference", "benefit"}

    sole = {"shares": [{"owner_id": a, "share_numerator": 1, "share_denominator": 1}]}
    ok(client_a.post(url, json=sole))
    (todo,) = _benefit_todos(client_a)
    assert todo["params"]["owner_id"] == a
    assert _benefit_todos(client_b) == []  # another workspace sees nothing of it (RLS)

    create(
        client_a,
        "/api/return-inputs",
        {
            "owner_id": a,
            "kind": "replacement_income",
            "tax_year": 2026,
            "value": {"benefit": "elterngeld", "amount": line["amount"]},
        },
    )
    assert _benefit_todos(client_a) == []
