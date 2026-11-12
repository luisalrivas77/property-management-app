"""The inbox and a person's answer through the API (phase 2 step 4, §8.1, §8.3).

The behaviour is covered in tests/db/test_services_matching.py and test_jobs_matching.py;
these check the routes: status codes, coded 422s, the read shapes (no IBAN, no HMAC) and
isolation. No worker runs here, so imported lines stay unmatched until answered.
"""

from uuid import uuid4

import pytest
from api_support import (
    TOKEN_A,
    TOKEN_B,
    FakeResolver,
    auth_for,
    client_for,
    create,
    make_settings,
    ok,
)
from db.db_support import _admin
from test_bank_parsers import CAMT, build

from hausbuch.api.app import create_app

pytestmark = pytest.mark.db

SAMPLE_IBAN = "DE50100000000000000001"
ZIP = build(next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports").input)
ACCOUNT = {
    "label": "Mietkonto",
    "bank": "Sparkasse",
    "iban": SAMPLE_IBAN,
    "opening_balance": "0.00",
    "opening_balance_on": "2026-03-31",
}


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


@pytest.fixture
def lines(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    response = client_a.post(
        f"/api/accounts/{account['id']}/imports",
        files={"file": ("umsaetze.zip", ZIP, "application/zip")},
    )
    assert ok(response, 201)["status"] == "reconciled"
    return ok(client_a.get(f"/api/accounts/{account['id']}/transactions"))


def _body(line, *parts, **extra):
    return {
        "allocation_set": {
            "transaction_id": line["id"],
            "allocations": [{"amount": amount, "category": category} for amount, category in parts],
            "method": "user",
            "confidence": "1.000",
        },
        **extra,
    }


def _debit(lines):
    return next(line for line in lines if line["amount"].startswith("-"))


def test_the_inbox_lists_every_unmatched_line_without_an_iban(client_a, lines):
    items = ok(client_a.get("/api/inbox"))
    assert {i["kind"] for i in items} == {"unmatched"}
    assert {i["transaction_id"] for i in items} == {line["id"] for line in lines}
    assert all(i["question"] and i["reasoning"] for i in items)
    text = client_a.get("/api/inbox").text
    assert SAMPLE_IBAN not in text and "hmac" not in text


def test_an_answer_is_confirmed_and_leaves_the_inbox(client_a, lines):
    line = _debit(lines)
    view = ok(client_a.get(f"/api/transactions/{line['id']}/allocations"))
    assert view["allocations"] == [] and view["transaction"]["id"] == line["id"]
    assert "counterparty_iban_hmac" not in view["transaction"]
    # The categories that never auto-confirm, for the checkbox default of typed rows (§8.1).
    assert "repairs" in view["no_default_rule_categories"]
    assert "insurance" not in view["no_default_rule_categories"]
    answered = ok(
        client_a.put(
            f"/api/transactions/{line['id']}/allocations",
            json=_body(line, (line["amount"], "bank_fees"), learn_rule=False),
        )
    )
    (allocation,) = answered["allocations"]
    assert (allocation["status"], allocation["method"], allocation["amount"]) == (
        "confirmed",
        "user",
        line["amount"],
    )
    assert answered["rule"] is None
    assert allocation["confirmed_by"] is not None  # the signed-in person
    assert line["id"] not in {i["transaction_id"] for i in ok(client_a.get("/api/inbox"))}
    again = ok(client_a.get(f"/api/transactions/{line['id']}/allocations"))
    assert again["allocations"] == answered["allocations"]


def test_refusals_are_coded_422s_and_write_nothing(client_a, lines):
    line = _debit(lines)
    url = f"/api/transactions/{line['id']}/allocations"
    short = ok(client_a.put(url, json=_body(line, ("-0.01", "bank_fees"))), 422)
    assert short["code"] == "allocation.sum_mismatch"
    other = next(x for x in lines if x["id"] != line["id"])
    mismatch = ok(client_a.put(url, json=_body(other, (other["amount"], "bank_fees"))), 422)
    assert mismatch["code"] == "allocation.transaction_mismatch"
    pattern = ok(
        client_a.put(
            url,
            json=_body(
                line, (line["amount"], "bank_fees"), learn_rule=True, purpose_pattern="[abc"
            ),
        ),
        422,
    )
    assert pattern["code"] == "rule.invalid_pattern"
    # Phase 6 D159: a household code on a rental line would be on no Anlage V line and in no
    # to-do, so the answer is refused rather than the cost silently dropped from the draft.
    private = ok(client_a.put(url, json=_body(line, (line["amount"], "hh_insurance"))), 422)
    assert private["code"] == "allocation.category_not_for_account"
    assert ok(client_a.get(url))["allocations"] == []


def test_a_patch_echoing_the_migrated_owner_flag_is_not_refused(client_a, lines, dbname):
    """Migration 0025 sets is_owner_account on the rental account too (phase 6 D156); a PATCH
    that sends the flag unchanged with another field must not hit the 'imports cannot become
    an owner account' guard, which is for a change of the flag only."""
    account_id = lines[0]["account_id"]
    with _admin(dbname) as conn:
        conn.execute("UPDATE account SET is_owner_account = true WHERE id = %s", (account_id,))
    patched = ok(
        client_a.patch(
            f"/api/accounts/{account_id}", json={"label": "Mietkonto neu", "is_owner_account": True}
        )
    )
    assert patched["label"] == "Mietkonto neu"
    assert patched["purpose"] == "rental"


def test_another_workspace_gets_404(client_a, client_b, lines):
    line = _debit(lines)
    url = f"/api/transactions/{line['id']}/allocations"
    assert client_b.get(url).status_code == 404
    assert client_b.put(url, json=_body(line, (line["amount"], "bank_fees"))).status_code == 404
    assert ok(client_b.get("/api/inbox")) == []
    assert ok(client_a.get(url))["allocations"] == []


def test_an_unknown_property_is_404_and_writes_nothing(client_a, lines):
    """Review round 2: a property id outside the workspace is a 404, not a foreign-key 500."""
    line = _debit(lines)
    url = f"/api/transactions/{line['id']}/allocations"
    body = _body(line, (line["amount"], "bank_fees"))
    body["allocation_set"]["allocations"][0]["property_id"] = str(uuid4())
    assert client_a.put(url, json=body).status_code == 404
    assert ok(client_a.get(url))["allocations"] == []


# --- POST /inbox/ai-retry (D50) ---------------------------------------------------------


def test_ai_retry_is_refused_while_the_ai_layer_is_off(client_a, lines):
    body = ok(client_a.post("/api/inbox/ai-retry"), 422)
    assert body["code"] == "ai.disabled"


def test_ai_retry_queues_the_workspaces_own_unmatched_lines(
    app_engine, sessions, tmp_path, blob_store, client_a, lines
):
    settings = make_settings(tmp_path, ai_enabled=True, anthropic_api_key="sk-test")
    app = create_app(settings, app_engine, blob_store, FakeResolver(sessions), worker=None)
    line = _debit(lines)
    url = f"/api/transactions/{line['id']}/allocations"
    ok(client_a.put(url, json=_body(line, (line["amount"], "bank_fees"), learn_rule=False)))
    assert ok(client_for(app, TOKEN_A).post("/api/inbox/ai-retry")) == {
        "queued": len(lines) - 1,
        "private_held_back": 0,
    }
    assert ok(client_for(app, TOKEN_B).post("/api/inbox/ai-retry")) == {
        "queued": 0,
        "private_held_back": 0,
    }
    assert client_for(app, None).post("/api/inbox/ai-retry").status_code == 401
    no_origin = client_for(app, TOKEN_A, origin=None).post("/api/inbox/ai-retry")
    assert no_origin.status_code == 403
