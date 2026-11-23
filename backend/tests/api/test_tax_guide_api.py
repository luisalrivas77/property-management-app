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
    assert form["open"] == len(mine)
