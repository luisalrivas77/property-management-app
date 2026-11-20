"""The server's English through the API (phase 10 D284, step 4c): `Accept-Language: en` returns
the to-dos and the plan explanation in English, no header German, and what an English request
writes is the same German a German one writes. Invented accounts and amounts only."""

import pytest
from api_support import create, ok

pytestmark = pytest.mark.db

EN = {"Accept-Language": "en-GB,en;q=0.9"}
GERMAN_MARKS = ("ä", "ö", "ü", "ß", "„")


def test_the_key_figures_to_dos_follow_the_request(client_a):
    params = {"from": "2026-01", "to": "2026-01"}
    [german] = ok(client_a.get("/api/health/months", params=params))
    [english] = ok(client_a.get("/api/health/months", params=params, headers=EN))
    assert german["todo"]["text"].startswith("Für den 31.01.2026 ist noch kein Monatsabschluss")
    assert german["todo"]["action"] == "Monatsabschluss festhalten"
    assert english["todo"]["text"].startswith("No month-end is stored for 31 Jan 2026")
    assert english["todo"]["action"] == "Record month-end"
    assert english["todo"]["href"] == german["todo"]["href"]
    assert english.keys() == german.keys() and english["todo"].keys() == german["todo"].keys()


def test_net_worth_todos_in_english_and_a_stored_run_in_either_language(client_a):
    created = create(
        client_a,
        "/api/accounts",
        {
            "label": "Tagesgeld",
            "bank": "Beispielbank",
            "iban": "DE73100000000000000072",
            "kind": "bank_savings",
            "purpose": "private",
            "opening_balance": "1234.56",
            "opening_balance_on": "2026-01-31",
        },
    )
    english = ok(client_a.get("/api/net-worth", params={"on": "2026-10-31"}, headers=EN))
    outdated, owners = english["todos"]
    assert (outdated["action"], owners["action"]) == ("Upload statement", "Set owners")
    assert not any(m in outdated["text"] for m in GERMAN_MARKS), outdated["text"]
    assert owners["href"] == f"/accounts/{created['id']}"

    written = ok(client_a.post("/api/net-worth/snapshots", headers=EN))
    assert written["outcome"] == "written"
    run_id = written["run"]["net_worth_run_id"]
    stored = ok(client_a.get(f"/api/net-worth/snapshots/{run_id}"))
    # A stored run is rebuilt from its rows: only the owners' to-do comes back with it.
    assert [t["action"] for t in stored["todos"]] == ["Eigentümer festlegen"]
    shown = ok(client_a.get(f"/api/net-worth/snapshots/{run_id}", headers=EN))
    assert [t["action"] for t in shown["todos"]] == ["Set owners"]
    # The stored figures are the same whichever language read them.
    assert {k: v for k, v in shown.items() if k != "todos"} == {
        k: v for k, v in stored.items() if k != "todos"
    }


def test_the_explanation_is_the_template_in_english(client_a):
    german = ok(client_a.get("/api/household-plan/explanation"))
    english = ok(client_a.get("/api/household-plan/explanation", headers=EN))
    assert (english["status"], english["reason"]) == ("template", "consent_off")
    assert english.keys() == german.keys()
    assert [p["topic"] for p in english["paragraphs"]] == [p["topic"] for p in german["paragraphs"]]
    for paragraph in english["paragraphs"]:
        assert not any(m in paragraph["text"] for m in GERMAN_MARKS), paragraph["text"]
    assert english["paragraphs"] != german["paragraphs"]


def test_the_budget_reference_points_follow_the_request(client_a):
    german = ok(client_a.get("/api/household-budget"))
    english = ok(client_a.get("/api/household-budget", headers=EN))
    assert english.keys() == german.keys()
    by_key = {r["key"]: r for r in german["references"]}
    assert by_key["split_60_20_20"]["does_not_measure"].startswith("Eine Faustregel, keine")
    shown = {r["key"]: r for r in english["references"]}
    assert shown.keys() == by_key.keys()
    assert shown["split_60_20_20"]["does_not_measure"].startswith("A rule of thumb, not")
    assert shown["housing_iwu_mieterbund"]["thresholds"][2]["label"] == "overburden"
    assert shown["split_50_30_20"]["needs"] == by_key["split_50_30_20"]["needs"]
    for ref in english["references"]:
        assert not any(m in ref["measures"] for m in GERMAN_MARKS), ref["measures"]
    # The comparison is read through the same walk (an empty month has no rows either way).
    month = {"month": "2027-03"}
    shown_month = ok(client_a.get("/api/household-plan/comparison", params=month, headers=EN))
    assert shown_month == ok(client_a.get("/api/household-plan/comparison", params=month))
