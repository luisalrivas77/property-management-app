"""GET /tax/{tax_year}/guide over HTTP (phase 10 §7.6, D290): the five steps, one Immobilie
step per property owned in the year, the forms' results copied from their drafts, and a year
without a rule set served in part instead of a 422. Invented owners and properties only."""

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, new_property, ok
from db.db_support import _admin

pytestmark = pytest.mark.db


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so an income input's recorded_by (FK to app_user) is real."""
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


def _guide(client, year: int) -> dict:
    return ok(client.get(f"/api/tax/{year}/guide"))


def test_the_guide_lists_the_steps_and_copies_each_forms_result(client_a):
    owner = create(client_a, "/api/owners", {"full_name": "Person A"})
    create(
        client_a,
        "/api/income-inputs",
        {
            "owner_id": owner["id"],
            "kind": "expected_gross_pay",
            "tax_year": 2026,
            "value": {"amount": "60000.00"},
        },
    )
    one = new_property(client_a)
    two = new_property(client_a, label="Wohnung Zwei", purchase_date="2025-02-01")
    got = _guide(client_a, 2026)
    assert [s["kind"] for s in got["steps"]] == [
        "angaben",
        "property",
        "property",
        "questions",
        "documents",
        "review",
    ]
    assert {s["property_id"] for s in got["steps"] if s["kind"] == "property"} == {
        one["id"],
        two["id"],
    }
    assert got["filing_deadline"] == "2027-08-02"
    assert got["steps_done"] == sum(1 for s in got["steps"] if s["state"] == "done")
    # each form's result is the draft's own
    for form in got["forms"]:
        if form["form"] == "anlage_v":
            draft = ok(
                client_a.get(
                    f"/api/properties/{form['property_id']}/anlage-v", params={"tax_year": 2026}
                )
            )
            assert form["result"] == draft["totals"]["result"]
        else:
            draft = ok(
                client_a.get(f"/api/owners/{owner['id']}/anlage-n", params={"tax_year": 2026})
            )
            assert (form["result"], form["status"]) == (draft["result"], draft["status"])
    estimate = ok(client_a.get("/api/tax/return-estimate", params={"tax_year": 2026}))
    assert got["estimate"] == {"result": estimate["result"], "status": estimate["status"]}
    # the expected pay is no certificate: the owner's Lohnsteuerbescheinigung is a Beleg
    wage = [d for d in got["documents"] if d["kind"] == "wage_certificate"]
    assert [(d["owner_id"], d["sender"], d["due_on"]) for d in wage] == [
        (owner["id"], "employer", "2027-03-01")
    ]
    assert got["unavailable"] == []


def test_a_year_before_any_rule_set_is_served_in_part(client_a):
    create(client_a, "/api/owners", {"full_name": "Person A"})
    prop = new_property(client_a)  # bought in 2024; no rule set of any kind for 2024
    got = _guide(client_a, 2024)
    assert got["estimate"] is None
    assert {"estimate:tax.no_rule_set", "anlage_v:tax.no_rule_set"} <= set(got["unavailable"])
    assert [s["kind"] for s in got["steps"]] == [
        "angaben",
        "property",
        "questions",
        "documents",
        "review",
    ]
    form = next(f for f in got["forms"] if f["form"] == "anlage_v")
    assert (form["property_id"], form["result"], form["reason"]) == (
        prop["id"],
        None,
        "tax.no_rule_set",
    )
    assert got["ready"] is False
    assert got["steps"][-1]["state"] != "done"
    assert got["steps"][2]["state"] != "done"  # the Jahresfragen could not be read


def test_a_year_without_its_rules_is_not_ready(client_a):
    """Nothing to type and nothing open, but 2025 has no tariff and no Jahresfragen yet: the
    guide must not say „bereit“ (it did, every step reading done)."""
    got = _guide(client_a, 2025)
    assert {"estimate:tax.no_rule_set", "questionnaire:tax.no_rule_set"} <= set(got["unavailable"])
    assert got["ready"] is False
    # Eure Angaben waits too: the estimate, whose points it lists, cannot be computed (wave 2's
    # open point, phase 10 §7.6; it read „erledigt“ until then).
    assert [s["state"] for s in got["steps"]] == ["waiting", "waiting", "done", "waiting"]
    assert got["steps_done"] == 1


def test_a_property_not_owned_in_the_year_has_no_step(client_a):
    new_property(client_a, purchase_date="2026-06-01", notarized_on="2026-03-01")
    got = _guide(client_a, 2025)
    assert [s["kind"] for s in got["steps"]] == ["angaben", "questions", "documents", "review"]


def test_another_workspace_sees_none_of_it(client_a, client_b):
    new_property(client_a)
    got = _guide(client_b, 2026)
    assert [s["kind"] for s in got["steps"]] == ["angaben", "questions", "documents", "review"]
    assert got["forms"] == [] and got["documents"] == []


def test_a_forms_open_count_is_every_point_listed_for_it(client_a):
    """A point the Immobilie step clears (the flat has no Wohnfläche) still counts on its
    Anlage V card, so the card never says „fertig“ under a point that names the form."""
    prop = new_property(client_a)
    got = _guide(client_a, 2026)
    mine = [p for p in got["points"] if p["form"] == "anlage_v" and p["property_id"] == prop["id"]]
    assert any(p["step"] == "property" for p in mine)
    form = next(f for f in got["forms"] if f["form"] == "anlage_v")
    belege = [
        d for d in got["documents"] if d["property_id"] == prop["id"] and d["status"] != "present"
    ]
    assert form["open"] == len(mine) + len(belege) + _open_questions(client_a, prop_id=prop["id"])


def _open_questions(client, *, prop_id=None, owner_id=None) -> int:
    view = ok(client.get("/api/tax/questionnaire", params={"tax_year": 2026}))
    return sum(
        1
        for q in view["progress"]["open"]
        if (prop_id and q["property_id"] == prop_id) or (owner_id and q["owner_id"] == owner_id)
    )


def test_an_anlage_n_card_counts_the_wage_certificate_still_to_come(client_a):
    """Pay from an estimate only: no point names the Anlage N, but its certificate is expected
    and the owner's Jahresfragen are open, so the card never reads 0 („fertig“)."""
    owner = _owner_with_pay(client_a)
    got = _guide(client_a, 2026)
    certificate = [
        d
        for d in got["documents"]
        if d["kind"] == "wage_certificate" and d["owner_id"] == owner["id"]
    ]
    assert [d["status"] for d in certificate] in (["expected"], ["missing"])
    card = next(f for f in got["forms"] if f["form"] == "anlage_n")
    points = [p for p in got["points"] if p["form"] == "anlage_n" and p["owner_id"] == owner["id"]]
    assert card["open"] == len(points) + 1 + _open_questions(client_a, owner_id=owner["id"])
    assert card["open"] > 0


def _owner_with_pay(client) -> dict:
    owner = create(client, "/api/owners", {"full_name": "Person A"})
    create(
        client,
        "/api/income-inputs",
        {
            "owner_id": owner["id"],
            "kind": "expected_gross_pay",
            "tax_year": 2026,
            "value": {"amount": "60000.00"},
        },
    )
    return owner


def test_a_benefit_announced_but_not_typed_keeps_eure_angaben_open(client_a):
    """The Jahresfragen say a benefit came; no amount is typed: only the estimate's `missing`
    knows, and the guide makes it a point of Eure Angaben instead of reaching „bereit“."""
    owner = _owner_with_pay(client_a)
    ok(
        client_a.post(
            "/api/tax/questionnaire/answers",
            json={
                "answer": {
                    "question_key": "owner.replacement_income",
                    "tax_year": 2026,
                    "owner_id": owner["id"],
                    "answer": "yes",
                    "choice": "arbeitslosengeld",
                }
            },
        ),
        201,
    )
    got = _guide(client_a, 2026)
    benefit = [p for p in got["points"] if p["code"] == "anlage_n.benefit_letter"]
    assert [(p["step"], p["form"], p["owner_id"]) for p in benefit] == [
        ("angaben", "anlage_n", owner["id"])
    ]
    assert got["steps"][0]["state"] == "open"
    assert got["ready"] is False


# --- Phase 10 §7.8 (a), D293: bookings propose tax inputs -----------------------------------


def _booked_line(client, *, debit: bool) -> dict:
    """A private account with the camt fixture's lines; one debit (or credit) line of 2026."""
    from test_bank_parsers import CAMT, build

    case = next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports")
    account = create(
        client,
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
    ok(client.post(f"/api/accounts/{account['id']}/imports", files=files), 201)
    lines = ok(client.get(f"/api/accounts/{account['id']}/transactions"))
    return next(
        t
        for t in lines
        if t["amount"].startswith("-") == debit
        and t["amount"] != "0.00"
        and t["booking_date"].startswith("2026-")
    )


def _allocate(client, line: dict, category: str) -> None:
    body = {
        "allocation_set": {
            "transaction_id": line["id"],
            "allocations": [{"amount": line["amount"], "category": category}],
            "method": "user",
            "confidence": "1.000",
        },
        "learn_rule": False,
    }
    ok(client.put(f"/api/transactions/{line['id']}/allocations", json=body))


def _proposal_points(client, code: str) -> list[dict]:
    return [p for p in _guide(client, 2026)["points"] if p["code"] == code]


def test_a_booked_donation_is_a_proposal_whose_payload_posts_verbatim(client_a, client_b):
    """The guide's donation point carries the exact POST /return-inputs body; posting it (with
    the recipient filled when the line names none) ends the point. It never blocks the year."""
    create(client_a, "/api/owners", {"full_name": "Person A"})
    line = _booked_line(client_a, debit=True)
    assert _proposal_points(client_a, "return.donation_booked") == []
    _allocate(client_a, line, "hh_donation")

    (point,) = _proposal_points(client_a, "return.donation_booked")
    proposal = point["proposal"]
    assert (point["step"], point["form"], point["blocking"]) == ("angaben", "return", False)
    assert proposal["difference"] == line["amount"].lstrip("-")
    assert proposal["allocation_ids"]
    assert "ask_party" not in proposal  # no party question (Luis, 26 Nov 2026)
    # The purpose text never leaves the server: the payee's name only („Zahlungen ansehen“).
    assert not {"purpose", "text"} & set(proposal)
    (shown,) = proposal["lines"]
    assert set(shown) == {"allocation_id", "booking_date", "amount", "payee"}
    assert (shown["booking_date"], shown["amount"]) == (line["booking_date"], line["amount"])
    assert [shown["allocation_id"]] == proposal["allocation_ids"]
    assert _proposal_points(client_b, "return.donation_booked") == []  # RLS

    body = proposal["payload"]
    if "recipient" in proposal["needs"]:
        body["value"]["recipient_label"] = "Empfänger"
    create(client_a, "/api/return-inputs", body)
    assert _proposal_points(client_a, "return.donation_booked") == []


def test_a_booked_benefit_is_one_point_with_its_payload(client_a):
    """`return.benefit_booked` is the proposal's point, no longer a second point from the
    estimate's warning; typing the payload ends it."""
    owner = create(client_a, "/api/owners", {"full_name": "Person A"})["id"]
    line = _booked_line(client_a, debit=False)
    _allocate(client_a, line, "hh_wage_replacement")
    (point,) = _proposal_points(client_a, "return.benefit_booked")
    assert point["blocking"] is True and point["proposal"]["group"] == "benefit"
    assert _guide(client_a, 2026)["steps"][0]["state"] == "open"

    body = point["proposal"]["payload"]
    if "owner" in point["proposal"]["needs"]:
        body["owner_id"] = owner
    if "benefit" in point["proposal"]["needs"]:
        body["value"]["benefit"] = "elterngeld"
    create(client_a, "/api/return-inputs", body)
    assert _proposal_points(client_a, "return.benefit_booked") == []


def test_booked_childcare_and_services_payloads_post_with_the_cards_fields(client_a):
    """The Kita and §35a cards post their proposal's payload with only the fields the bookings
    cannot say (the frontend's ProposalForm): the child when none is pre-selected, and for
    §35a the labour share and its class, typed from the invoice (never the bank amount). Each
    then ends its point; neither blocks the year."""
    create(client_a, "/api/owners", {"full_name": "Person A"})
    create(
        client_a,
        "/api/return-inputs",
        {"kind": "child", "tax_year": 2026, "value": {"born_on": "2022-05-01"}},
    )
    line = _booked_line(client_a, debit=True)
    _allocate(client_a, line, "hh_childcare")
    (point,) = _proposal_points(client_a, "return.childcare_booked")
    proposal = point["proposal"]
    assert point["blocking"] is False and proposal["group"] == "childcare"
    body = proposal["payload"]
    assert body["value"]["paid_to_account"] is True
    if "child" in proposal["needs"]:
        body["value"]["child_id"] = proposal["child_choices"][0]["id"]
    else:  # the one child under 14 is pre-selected, so nothing is offered to pick
        assert body["value"]["child_id"] and proposal["child_choices"] == []
    # The payee is pre-set on the row, so the row covers its own payee's card first.
    assert proposal["payee"] and body["value"]["payee_label"] == proposal["payee"]
    saved = create(client_a, "/api/return-inputs", body)
    assert saved["value"]["payee_label"] == proposal["payee"]
    assert _proposal_points(client_a, "return.childcare_booked") == []

    _allocate(client_a, line, "hh_household_services")
    (point,) = _proposal_points(client_a, "return.services_booked")
    proposal = point["proposal"]
    assert point["blocking"] is False and proposal["group"] == "household_services"
    assert {"labour_cost", "service_class"} <= set(proposal["needs"])
    body = proposal["payload"]
    assert "labour_cost" not in body["value"]  # never the bank line's amount
    assert "amount" not in body["value"] and body["value"]["payee_label"] == proposal["payee"]
    body["value"] |= {"labour_cost": "100.00", "service_class": "handwerker"}
    saved = create(client_a, "/api/return-inputs", body)
    assert saved["value"]["payee_label"] == proposal["payee"]
    assert _proposal_points(client_a, "return.services_booked") == []
