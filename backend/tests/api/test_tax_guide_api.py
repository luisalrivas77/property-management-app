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
    assert [s["state"] for s in got["steps"]] == ["done", "waiting", "done", "waiting"]
    assert got["steps_done"] == 2


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
