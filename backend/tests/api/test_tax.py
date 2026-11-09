"""GET /api/properties/{id}/anlage-v (docs/phases/phase-3.md §10): the draft over HTTP, its
years and its error codes. Most tests replace draft() by a stub returning a minimal draft (the
rule's own fixtures test what it computes); one runs the real rule end to end over a
reconciled statement and two confirmed answers."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, flat, ok
from db.db_support import _admin
from test_bank_parsers import CAMT, build

from hausbuch.schemas.tax import (
    AnlageVDraft,
    Coverage,
    DraftHeader,
    DraftTotals,
    RuleSetStamp,
)
from hausbuch.services import tax as tax_service

pytestmark = pytest.mark.db


def _minimal(facts, tax_year, rule_set) -> AnlageVDraft:
    return AnlageVDraft(
        property_id=facts.property.id,
        tax_year=tax_year,
        rule_set=RuleSetStamp(
            tax_year=rule_set.tax_year,
            version=rule_set.version,
            form_year=rule_set.form.form_year,
            form_status=rule_set.form.status,
        ),
        header=DraftHeader(
            transfer_date=facts.property.purchase_date, completed=facts.property.build_year
        ),
        totals=DraftTotals(
            income=Decimal("0.00"),
            costs=Decimal("0.00"),
            result=Decimal("0.00"),
            income_rounded=0,
            costs_rounded=0,
            result_rounded=0,
        ),
        coverage=Coverage(months_owned=12, months_covered=0),
    )


@pytest.fixture
def stub_draft(monkeypatch) -> list:
    calls: list = []

    def fake(facts, tax_year, rule_set):
        calls.append((facts, tax_year, rule_set))
        return _minimal(facts, tax_year, rule_set)

    monkeypatch.setattr(tax_service, "draft", fake)
    return calls


def _draft(client, property_id, **params):
    return client.get(f"/api/properties/{property_id}/anlage-v", params=params)


def test_the_draft_of_a_year(client_a, stub_draft):
    setup = flat(client_a)  # bought 1 May 2024
    pid = setup["property"]["id"]
    body = ok(_draft(client_a, pid, tax_year=2026))
    assert body["property_id"] == pid
    assert body["tax_year"] == 2026
    assert body["rule_set"]["form_status"] == "provisional"
    assert body["header"]["transfer_date"] == "2024-05-01"
    assert body["totals"]["result"] == "0.00"  # money stays a decimal string
    ((facts, year, rule_set),) = stub_draft
    assert facts.property.id == UUID(pid)
    assert (year, rule_set.tax_year) == (2026, 2026)
    assert [t.id for t in facts.tenancies] == [UUID(setup["tenancy"]["id"])]


def test_a_year_outside_the_available_years_is_422(client_a, stub_draft):
    pid = flat(client_a)["property"]["id"]
    response = _draft(client_a, pid, tax_year=2023)
    assert response.status_code == 422
    assert response.json()["code"] == "tax.year_not_available"
    assert stub_draft == []


def test_a_year_without_a_rule_set_is_422(client_a, stub_draft):
    pid = flat(client_a)["property"]["id"]
    response = _draft(client_a, pid, tax_year=2024)
    assert response.status_code == 422
    assert response.json()["code"] == "tax.no_rule_set"
    assert stub_draft == []


def test_the_year_is_required_and_bounded(client_a, stub_draft):
    pid = flat(client_a)["property"]["id"]
    assert _draft(client_a, pid).status_code == 422
    response = _draft(client_a, pid, tax_year=1999)
    assert response.status_code == 422
    assert response.json()["error"] == "validation"


def test_an_unknown_or_foreign_property_is_404(client_a, client_b, stub_draft):
    pid = flat(client_a)["property"]["id"]
    for client, property_id in ((client_a, str(uuid4())), (client_b, pid)):
        response = _draft(client, property_id, tax_year=2026)
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
    assert stub_draft == []


def test_today_bounds_the_years(client_a, stub_draft):
    """The current (Berlin) year is the last available one."""
    pid = flat(client_a)["property"]["id"]
    next_year = date.today().year + 1
    response = _draft(client_a, pid, tax_year=next_year)
    assert response.status_code == 422
    assert response.json()["code"] == "tax.year_not_available"


# --- End to end: the real rule over a statement and two answers ---------------------------

SAMPLE_IBAN = "DE50100000000000000001"
ZIP = build(next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports").input)


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so a confirmation's confirmed_by (FK to app_user) is real."""
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


def _confirm(client, line, category, property_id) -> str:
    body = {
        "allocation_set": {
            "transaction_id": line["id"],
            "allocations": [
                {"amount": line["amount"], "category": category, "property_id": property_id}
            ],
            "method": "user",
            "confidence": "1.000",
        },
        "learn_rule": False,
    }
    answered = ok(client.put(f"/api/transactions/{line['id']}/allocations", json=body))
    (allocation,) = answered["allocations"]
    assert allocation["status"] == "confirmed"
    return allocation["id"]


def _ids(line) -> list[tuple[str, str | None]]:
    return [(s["kind"], s["id"]) for s in line["sources"]]


def test_the_real_draft_shows_confirmed_lines_with_their_sources(client_a):
    setup = flat(client_a)  # bought 1 May 2024, no price split
    pid = setup["property"]["id"]
    account = create(
        client_a,
        "/api/accounts",
        {
            "label": "Mietkonto",
            "bank": "Sparkasse",
            "iban": SAMPLE_IBAN,
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
        },
    )
    imported = client_a.post(
        f"/api/accounts/{account['id']}/imports",
        files={"file": ("umsaetze.zip", ZIP, "application/zip")},
    )
    assert ok(imported, 201)["status"] == "reconciled"
    lines = ok(client_a.get(f"/api/accounts/{account['id']}/transactions"))
    assert len(lines) == 22  # the fixture's lines, all booked in 2026
    repair = next(x for x in lines if x["amount"] == "-360.18")  # 30 Jun 2026
    income = next(x for x in lines if x["amount"] == "241.58")  # 30 Jun 2026, Partei A
    repair_id = _confirm(client_a, repair, "repairs", pid)
    income_id = _confirm(client_a, income, "other_income", pid)

    response = _draft(client_a, pid, tax_year=2026)
    body = ok(response)
    by_key = {line["key"]: line for line in body["lines"]}
    assert by_key["wk.maintenance"]["amount"] == "360.18"
    assert ("allocation", repair_id) in _ids(by_key["wk.maintenance"])
    assert by_key["income.other"]["amount"] == "241.58"
    assert ("allocation", income_id) in _ids(by_key["income.other"])
    assert (body["totals"]["income"], body["totals"]["costs"]) == ("241.58", "360.18")
    assert body["totals"]["result"] == "-118.60"
    codes = {w["code"] for w in body["warnings"]}
    assert {"income.other_review", "afa.no_split", "draft.open_lines_excluded"} <= codes
    # The 20 lines nobody answered are left out and counted, not booked.
    assert body["excluded_open"]["count"] == 20
    # Statements reconciled through 6 Nov 2026: January to October of twelve months owned.
    assert (body["coverage"]["months_owned"], body["coverage"]["months_covered"]) == (12, 10)
    # A counterparty is at most its masked IBAN (D5): no name, full IBAN or purpose text.
    text = response.text
    assert "Partei A" not in text and "DE16100000000000001001" not in text
    assert "Verwendungszweck" not in text and SAMPLE_IBAN not in text
    # The year before has none of 2026's payments (its Hausgeld line may still be provisional).
    earlier = ok(_draft(client_a, pid, tax_year=2025))
    sourced = {ref for line in earlier["lines"] for ref in _ids(line)}
    assert not sourced & {("allocation", repair_id), ("allocation", income_id)}
    assert earlier["totals"]["income"] == "0.00"
