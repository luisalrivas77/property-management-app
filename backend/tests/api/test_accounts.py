"""Accounts, statement imports and possible duplicates through the API (phase 2 step 3).

The service behaviour is covered in tests/db/test_services_statements.py; these check the
routes: status codes, the multipart import, the read shapes (no IBAN, no HMAC) and isolation.
"""

import pytest
from api_support import create, ok
from db.db_support import _admin
from test_bank_parsers import CAMT, CSV, build

pytestmark = pytest.mark.db

SAMPLE_IBAN = "DE50100000000000000001"
ZIP = build(next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports").input)
CSV_BYTES = build(next(c for c in CSV.cases if c.id == "sparkasse_export").input)
ACCOUNT = {
    "label": "Mietkonto",
    "bank": "Sparkasse",
    "iban": SAMPLE_IBAN,
    "opening_balance": "0.00",
    "opening_balance_on": "2026-03-31",
}


def _upload(client, account_id, data=ZIP, media_type="application/zip", name="umsaetze.zip"):
    return client.post(
        f"/api/accounts/{account_id}/imports", files={"file": (name, data, media_type)}
    )


def test_account_crud_never_returns_the_iban(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    assert account["iban_masked"] == "DE50 **** 0001"
    assert "iban" not in account and "iban_hmac" not in account
    assert ok(client_a.get("/api/accounts")) == [account]
    assert ok(client_a.get(f"/api/accounts/{account['id']}")) == account
    patched = ok(client_a.patch(f"/api/accounts/{account['id']}", json={"label": "Konto WE 3"}))
    assert patched["label"] == "Konto WE 3"
    response = client_a.post("/api/accounts", json=ACCOUNT)
    assert response.status_code == 409
    assert SAMPLE_IBAN not in response.text
    # A PATCH cannot change the IBAN.
    assert (
        client_a.patch(f"/api/accounts/{account['id']}", json={"iban": SAMPLE_IBAN}).status_code
        == 422
    )


def test_import_is_201_then_200_for_the_same_bytes(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    imported = ok(_upload(client_a, account["id"]), 201)
    assert imported["status"] == "reconciled" and imported["lines_new"] == 22
    assert ok(_upload(client_a, account["id"]), 200) == imported
    assert ok(client_a.get(f"/api/imports/{imported['id']}")) == imported
    assert ok(client_a.get(f"/api/accounts/{account['id']}/imports")) == [imported]

    lines = ok(client_a.get(f"/api/accounts/{account['id']}/transactions"))
    assert len(lines) == 22
    assert all("counterparty_iban_hmac" not in line and "raw" not in line for line in lines)
    window = ok(
        client_a.get(
            f"/api/accounts/{account['id']}/transactions",
            params={"booked_from": "2026-04-01", "booked_to": "2026-04-30"},
        )
    )
    assert window and all(line["booking_date"].startswith("2026-04") for line in window)


def test_the_format_is_sniffed_but_the_media_type_must_be_allowed(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    response = _upload(client_a, account["id"], media_type="application/octet-stream")
    assert response.status_code == 415
    # A ZIP declared as text/plain is still read as camt.
    assert ok(_upload(client_a, account["id"], media_type="text/plain"), 201)["format"] == "camt"


def test_an_unreadable_file_is_a_rule_violation(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    response = _upload(client_a, account["id"], b"hello", "text/plain", "x.txt")
    body = ok(response, 422)
    assert body["error"] == "rule_violation" and body["code"] == "statement.unknown_format"


def test_csv_balances_and_duplicates_through_the_api(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    from_csv = ok(_upload(client_a, account["id"], CSV_BYTES, "text/csv", "umsaetze.csv"), 201)
    assert from_csv["status"] == "imported" and from_csv["balance_source"] == "none"
    declared = ok(
        client_a.post(
            f"/api/imports/{from_csv['id']}/balances", json={"closing_balance": "-3028.51"}
        )
    )
    assert declared["status"] == "reconciled"

    from_camt = ok(_upload(client_a, account["id"]), 201)
    refused = ok(
        client_a.post(f"/api/imports/{from_camt['id']}/balances", json={"closing_balance": "0.00"}),
        422,
    )
    assert refused["code"] == "statement.balance_from_file"

    pairs = ok(client_a.get("/api/possible-duplicates", params={"status": "open"}))
    assert len(pairs) == 19
    pair = pairs[0]
    assert (
        client_a.post(f"/api/possible-duplicates/{pair['id']}", json={"status": "open"}).status_code
        == 422
    )
    merged = ok(client_a.post(f"/api/possible-duplicates/{pair['id']}", json={"status": "merged"}))
    assert merged["status"] == "merged" and merged["decided_at"] is not None
    again = client_a.post(f"/api/possible-duplicates/{pair['id']}", json={"status": "kept_both"})
    assert again.status_code == 409


def test_another_workspace_gets_404_and_empty_lists(client_a, client_b):
    account = create(client_a, "/api/accounts", ACCOUNT)
    imported = ok(_upload(client_a, account["id"]), 201)
    assert client_b.get(f"/api/accounts/{account['id']}").status_code == 404
    assert client_b.get(f"/api/accounts/{account['id']}/imports").status_code == 404
    assert client_b.get(f"/api/accounts/{account['id']}/transactions").status_code == 404
    assert client_b.get(f"/api/imports/{imported['id']}").status_code == 404
    assert _upload(client_b, account["id"]).status_code == 404
    assert ok(client_b.get("/api/accounts")) == []
    assert ok(client_b.get("/api/possible-duplicates")) == []


def test_a_statement_upload_gets_the_upload_limit_not_the_json_limit(client_a):
    """A year of daily reports or a PDF is over the 1 MiB JSON limit: the imports path must
    reach the route (which then judges the file), not be refused at the transport."""
    account = create(client_a, "/api/accounts", ACCOUNT)
    big = b"PK\x03\x04" + b"\0" * (2 * 1024 * 1024)
    response = _upload(client_a, account["id"], data=big)
    assert response.status_code != 413, response.text
    assert response.status_code == 422


# --- Phase 6 step 1: kind, purpose, provider and the owner shares (D156, D157) -------------


def _owners(client):
    a = create(client, "/api/owners", {"full_name": "Owner A"})
    b = create(client, "/api/owners", {"full_name": "Owner B"})
    return a["id"], b["id"]


def _shares(account):
    return [
        (s["owner_id"], s["share_numerator"], s["share_denominator"]) for s in account["ownership"]
    ]


def _events(client, account_id, type_):
    events = ok(client.get("/api/events", params={"entity_id": account_id}))
    return [e for e in events if e["type"] == type_]


def test_a_patch_sets_kind_purpose_and_provider_as_one_event(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT | {"purpose": "private"})
    url = f"/api/accounts/{account['id']}"
    body = {"kind": "bank_savings", "provider": "Beispielbank", "label": "Tagesgeld"}
    patched = ok(client_a.patch(url, json=body))
    assert (patched["kind"], patched["purpose"], patched["provider"], patched["label"]) == (
        "bank_savings",
        "private",
        "Beispielbank",
        "Tagesgeld",
    )
    (kind_set,) = _events(client_a, account["id"], "account.kind_set")
    assert kind_set["payload"] == {
        "kind": "bank_savings",
        "purpose": "private",
        "provider": "Beispielbank",
    }
    (updated,) = _events(client_a, account["id"], "account.updated")
    assert set(updated["payload"]["changes"]) == {"label"}
    # The same values again change nothing and record nothing.
    assert ok(client_a.patch(url, json={"kind": "bank_savings"})) == patched
    assert len(_events(client_a, account["id"], "account.kind_set")) == 1
    # A bank account stays a bank account: its IBAN is its identity (D156).
    refused = ok(client_a.patch(url, json={"kind": "depot"}), 422)
    assert refused["code"] == "account.kind_not_available"
    assert ok(client_a.patch(url, json={"provider": None}))["provider"] is None


@pytest.fixture
def user_a(auth_a, dbname):
    """client_a's user as a real app_user, so a confirmation's confirmed_by (FK) holds."""
    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )


def test_a_purpose_change_must_fit_the_bookings(client_a, user_a):
    """D159 for a change of purpose: the rental account holding a rental booking cannot
    become private; once the booking is a code both purposes allow, it can, and back."""
    account = create(client_a, "/api/accounts", ACCOUNT)
    ok(_upload(client_a, account["id"]), 201)
    line = next(
        t
        for t in ok(client_a.get(f"/api/accounts/{account['id']}/transactions"))
        if t["amount"].startswith("-")
    )
    url = f"/api/transactions/{line['id']}/allocations"

    def book(category):
        body = {
            "allocation_set": {
                "transaction_id": line["id"],
                "allocations": [{"amount": line["amount"], "category": category}],
                "method": "user",
                "confidence": "1.000",
            },
            "learn_rule": False,
        }
        ok(client_a.put(url, json=body))

    book("other_expense")
    refused = ok(client_a.patch(f"/api/accounts/{account['id']}", json={"purpose": "private"}), 422)
    assert refused["code"] == "allocation.category_not_for_account"
    assert ok(client_a.get(f"/api/accounts/{account['id']}"))["purpose"] == "rental"

    book("bank_fees")  # the correction supersedes the rental code
    private = ok(client_a.patch(f"/api/accounts/{account['id']}", json={"purpose": "private"}))
    assert private["purpose"] == "private"
    assert (
        ok(client_a.patch(f"/api/accounts/{account['id']}", json={"purpose": "rental"}))["purpose"]
        == "rental"
    )


def test_owner_shares_are_set_superseded_and_listed(client_a):
    a, b = _owners(client_a)
    account = create(client_a, "/api/accounts", ACCOUNT | {"purpose": "private"})
    assert (account["ownership"], account["ownership_missing"]) == ([], True)
    url = f"/api/accounts/{account['id']}/ownership"
    assert ok(client_a.get(url)) == []

    half = {"share_numerator": 1, "share_denominator": 2}
    body = {
        "shares": [
            {"owner_id": a, **half, "reason": "Gemeinschaftskonto"},
            {"owner_id": b, **half, "reason": "Gemeinschaftskonto"},
        ],
        "effective_from": "2026-01-01",
    }
    joint = ok(client_a.post(url, json=body))
    assert joint["ownership_missing"] is False
    assert set(_shares(joint)) == {(a, 1, 2), (b, 1, 2)}
    first = ok(client_a.get(url))
    assert {r["effective_from"] for r in first} == {"2026-01-01"}
    # The same set again is a no-op: no rows, no event.
    assert ok(client_a.post(url, json=body)) == joint
    assert len(_events(client_a, account["id"], "account.ownership_set")) == 1

    sole = ok(
        client_a.post(
            url,
            json={"shares": [{"owner_id": a, "share_numerator": 2, "share_denominator": 2}]},
        )
    )
    assert _shares(sole) == [(a, 1, 1)]  # 2/2 is stored reduced
    (current,) = ok(client_a.get(url))
    assert current["id"] not in {r["id"] for r in first}
    events = _events(client_a, account["id"], "account.ownership_set")
    assert len(events) == 2
    assert sorted(len(e["payload"]["superseded"]) for e in events) == [0, 2]
    assert "Gemeinschaftskonto" not in str([e["payload"] for e in events])
    listed = next(x for x in ok(client_a.get("/api/accounts")) if x["id"] == account["id"])
    assert listed["ownership"] == sole["ownership"]


@pytest.mark.parametrize(
    ("shares", "code"),
    [
        ([("a", 1, 2), ("b", 49, 100)], "ownership.sum_not_one"),
        ([("a", 1, 2), ("a", 1, 2)], "ownership.sum_not_one"),
        ([("a", 1, 2), ("x", 1, 2)], "ownership.owner_missing"),
    ],
)
def test_a_set_that_is_not_whole_is_refused(client_a, shares, code):
    a, b = _owners(client_a)
    ids = {"a": a, "b": b, "x": "00000000-0000-4000-8000-0000000000ff"}
    account = create(client_a, "/api/accounts", ACCOUNT)
    body = {
        "shares": [
            {"owner_id": ids[o], "share_numerator": n, "share_denominator": d} for o, n, d in shares
        ]
    }
    response = client_a.post(f"/api/accounts/{account['id']}/ownership", json=body)
    assert response.status_code == 422
    assert code in response.text
    assert ok(client_a.get(f"/api/accounts/{account['id']}"))["ownership_missing"] is True


def test_another_workspace_cannot_read_or_set_the_shares(client_a, client_b):
    a, _ = _owners(client_a)
    account = create(client_a, "/api/accounts", ACCOUNT)
    url = f"/api/accounts/{account['id']}/ownership"
    body = {"shares": [{"owner_id": a, "share_numerator": 1, "share_denominator": 1}]}
    assert client_b.get(url).status_code == 404
    assert client_b.post(url, json=body).status_code == 404
    assert ok(client_a.get(url)) == []
