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


@pytest.fixture
def booked(client_a) -> dict:
    """The flat (bought 1 May 2024, no price split), the camt fixture imported on its rental
    account, and two lines confirmed for it: a repair and an other income."""
    setup = flat(client_a)
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
    return {
        "pid": pid,
        "repair": repair,
        "income": income,
        "repair_id": _confirm(client_a, repair, "repairs", pid),
        "income_id": _confirm(client_a, income, "other_income", pid),
    }


def test_the_real_draft_shows_confirmed_lines_with_their_sources(client_a, booked):
    pid, repair_id, income_id = booked["pid"], booked["repair_id"], booked["income_id"]
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


# --- D140: "Als Miete buchen" on an Einnahme ohne Zuordnung ---------------------------------

_DRAFT_FIELDS = (
    "amount",
    "category",
    "property_id",
    "unit_id",
    "tenancy_id",
    "loan_id",
    "service_period_start",
    "service_period_end",
    "recurring",
    "document_id",
)


def _rebook_as_rent(client, transaction_id: str, allocation_id: str) -> dict:
    """What the to-do's button does (todos.tsx, D140): read the line's current rows, change
    the category of the cited allocation only, and send the whole set back through the
    existing answer command, without a learned rule."""
    view = ok(client.get(f"/api/transactions/{transaction_id}/allocations"))
    rows = view["allocations"]
    assert allocation_id in {row["id"] for row in rows}
    drafts = [
        {name: row[name] for name in _DRAFT_FIELDS}
        | ({"category": "rent_cold"} if row["id"] == allocation_id else {})
        for row in rows
    ]
    body = {
        "allocation_set": {
            "transaction_id": transaction_id,
            "allocations": drafts,
            "method": "user",
            "confidence": "1.000",
        },
        "learn_rule": False,
    }
    return ok(client.put(f"/api/transactions/{transaction_id}/allocations", json=body))


def test_an_other_income_part_rebooked_as_rent_lands_on_the_rent_row(client_a, booked):
    """The income line split in two other_income parts (the second with a service period);
    the button re-books one part as rent_cold without a tenancy: it moves to the flat's Z13
    row, Sonstige Einnahmen keep the other part with its to-do, and the change is a new
    transaction.reallocated event over the superseded rows (append-only)."""
    pid, line = booked["pid"], booked["income"]
    split = {
        "allocation_set": {
            "transaction_id": line["id"],
            "allocations": [
                {"amount": "200.00", "category": "other_income", "property_id": pid},
                {
                    "amount": "41.58",
                    "category": "other_income",
                    "property_id": pid,
                    "service_period_start": "2026-06-01",
                    "service_period_end": "2026-06-30",
                },
            ],
            "method": "user",
            "confidence": "1.000",
        },
        "learn_rule": False,
    }
    before = ok(client_a.put(f"/api/transactions/{line['id']}/allocations", json=split))
    part = next(a for a in before["allocations"] if a["amount"] == "41.58")
    draft = ok(_draft(client_a, pid, tax_year=2026))
    reviews = [w for w in draft["warnings"] if w["code"] == "income.other_review"]
    assert sorted(w["params"]["amount"] for w in reviews) == ["200.00", "41.58"]

    after = _rebook_as_rent(client_a, line["id"], part["id"])
    rows = {a["amount"]: a for a in after["allocations"]}
    assert set(rows) == {"200.00", "41.58"}
    rent = rows["41.58"]
    assert (rent["category"], rent["status"], rent["tenancy_id"]) == (
        "rent_cold",
        "confirmed",
        None,
    )
    assert (rent["property_id"], rent["service_period_start"], rent["service_period_end"]) == (
        pid,
        "2026-06-01",
        "2026-06-30",
    )
    assert rows["200.00"]["category"] == "other_income"
    assert after["rule"] is None  # no rule books the payer's later lines as rent

    body = ok(_draft(client_a, pid, tax_year=2026))
    by_key = {line_["key"]: line_ for line_ in body["lines"]}
    assert by_key["income.rent_total"]["amount"] == "41.58"
    assert ("allocation", rent["id"]) in _ids(by_key["income.rent_total"])
    assert by_key["income.other"]["amount"] == "200.00"
    (row,) = body["unit_rows"]
    assert (row["wohnung"], row["id"], row["amount"]) == ("property", pid, "41.58")
    reviews = [w for w in body["warnings"] if w["code"] == "income.other_review"]
    assert [w["params"]["amount"] for w in reviews] == ["200.00"]
    assert body["totals"]["income"] == "241.58"  # moved between lines, not added

    events = ok(client_a.get(f"/api/events?entity_id={line['id']}"))
    reallocated = [e for e in events if e["type"] == "transaction.reallocated"]
    assert len(reallocated) == 2  # the split, then the re-booking
    last = max(reallocated, key=lambda e: e["seq"])
    assert set(last["payload"]["supersedes"]) == {a["id"] for a in before["allocations"]}
    categories = {p["amount"]: p["category"] for p in last["payload"]["allocations"]}
    assert categories == {"200.00": "other_income", "41.58": "rent_cold"}


# --- GET …/anlage-v/sources: what the sources drawer shows --------------------------------


def _sources(client, property_id, **params):
    return client.get(f"/api/properties/{property_id}/anlage-v/sources", params=params)


def test_the_sources_give_each_cited_payment_its_details(client_a, booked):
    pid = booked["pid"]
    response = _sources(client_a, pid, tax_year=2026)
    rows = {row["allocation_id"]: row for row in ok(response)}
    assert set(rows) == {booked["repair_id"], booked["income_id"]}
    repair = rows[booked["repair_id"]]
    assert (repair["booking_date"], repair["amount"], repair["category"]) == (
        "2026-06-30",
        "-360.18",
        "repairs",
    )
    assert repair["transaction_id"] == booked["repair"]["id"]
    assert repair["property_id"] == pid
    income = rows[booked["income_id"]]
    assert (income["amount"], income["category"]) == ("241.58", "other_income")
    assert income["counterparty_iban_masked"] is not None
    # Every payment a draft line cites is in the list.
    cited = {
        s["id"]
        for line in ok(_draft(client_a, pid, tax_year=2026))["lines"]
        for s in line["sources"]
        if s["kind"] == "allocation"
    }
    assert cited <= set(rows)


def test_the_sources_carry_no_name_iban_or_purpose_text(client_a, booked):
    response = _sources(client_a, booked["pid"], tax_year=2026)
    text = response.text
    assert "Partei" not in text  # the counterparty names
    assert "DE16100000000000001001" not in text and "DE86100000000000001002" not in text
    assert SAMPLE_IBAN not in text
    assert "Verwendungszweck" not in text  # the purpose texts
    assert "hmac" not in text
    for row in ok(response):
        assert {"booking_date", "amount", "category", "counterparty_iban_masked"} <= set(row)
        assert not {"counterparty_name", "purpose", "counterparty_iban"} & set(row)


def test_the_sources_refuse_the_years_the_draft_refuses(client_a):
    pid = flat(client_a)["property"]["id"]
    for year, code in ((2023, "tax.year_not_available"), (2024, "tax.no_rule_set")):
        response = _sources(client_a, pid, tax_year=year)
        assert response.status_code == 422
        assert response.json()["code"] == code
    assert _sources(client_a, pid).status_code == 422  # the year is required


def test_another_workspaces_property_has_no_sources(client_a, client_b, booked):
    for client, property_id in ((client_b, booked["pid"]), (client_a, str(uuid4()))):
        response = _sources(client, property_id, tax_year=2026)
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
        assert booked["repair_id"] not in response.text
