"""Accounts, statement imports and possible duplicates through the API (phase 2 step 3).

The service behaviour is covered in tests/db/test_services_statements.py; these check the
routes: status codes, the multipart import, the read shapes (no IBAN, no HMAC) and isolation.
"""

import pytest
from api_support import create, ok
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
