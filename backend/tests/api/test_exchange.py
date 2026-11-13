"""An exchange account through the API (phase 6 step 5a, D166 amended, D178): the account,
the multipart upload (201 then 200), the page, the balances, the answers, a refusal that
quotes no cell, and isolation. The service behaviour is tests/db/test_services_exchange_import.py.
"""

from decimal import Decimal

import fixture_models_phase6 as fixtures
import pytest
from api_support import create, ok

pytestmark = pytest.mark.db

BISON = fixtures.load("bison_csv")
BINANCE = fixtures.load("binance_csv")
REAL_LAYOUT = next(c for c in BISON.parse_cases if c.id == "real_layout").text.encode()
ACCOUNT = {
    "label": "BISON",
    "bank": "BISON",
    "kind": "exchange",
    "provider": "BISON",
    "opening_balance": "0.00",
    "opening_balance_on": "2026-01-01",
}


def _upload(client, account_id, data=REAL_LAYOUT, media_type="text/csv", name="export.csv"):
    return client.post(
        f"/api/accounts/{account_id}/movement-imports", files={"file": (name, data, media_type)}
    )


def test_an_exchange_account_its_upload_balances_and_answers(client_a, client_b):
    account = create(client_a, "/api/accounts", ACCOUNT)
    assert (account["kind"], account["purpose"], account["iban_masked"]) == (
        "exchange",
        "private",
        None,
    )
    upload = ok(_upload(client_a, account["id"]), 201)
    imported = upload["movement_import"]
    assert (imported["rows_new"], imported["status"]) == (4, "imported")
    assert ok(_upload(client_a, account["id"]), 200)["movement_import"] == imported

    page = ok(client_a.get(f"/api/accounts/{account['id']}/exchange"))
    assert {p["symbol"]: Decimal(p["units"]) for p in page["positions"]} == {
        "EUR": Decimal("250.00"),
        "BTC": Decimal("0.00009965"),
    }
    [view] = page["imports"]
    balances = {b["symbol"]: b for b in view["balances"]}
    checked = ok(
        client_a.post(
            f"/api/movement-imports/{imported['id']}/holdings",
            json={
                "balances": [
                    {"asset_id": balances["EUR"]["asset_id"], "units": "250.00"},
                    {"asset_id": balances["BTC"]["asset_id"], "units": "0.00009965"},
                ]
            },
        )
    )
    assert checked["status"] == "reconciled"

    kinds = {i["kind"] for i in ok(client_a.get("/api/inbox"))}
    assert {"exchange_withdrawals", "exchange_movement"} <= kinds
    confirmed = ok(client_a.post(f"/api/movement-imports/{imported['id']}/withdrawals/confirm"))
    assert confirmed == {"confirmed": 1}
    movements = ok(client_a.get(f"/api/accounts/{account['id']}/movements"))
    [deposit] = [m for m in movements if m["kind"] == "cashback_reward"]
    assert deposit["question"] == "exchange.deposit_question"
    assert deposit["answers"] == ["cashback_reward", "staking_reward", "internal_transfer_in"]
    response = client_a.post(f"/api/movements/{deposit['id']}/confirm")
    assert response.status_code == 204
    again = client_a.post(
        f"/api/movements/{deposit['id']}/answer", json={"kind": "internal_transfer_in"}
    )
    assert again.status_code == 422 and "exchange.movement_done" in again.text

    # Another workspace sees none of it.
    assert client_b.get(f"/api/accounts/{account['id']}/exchange").status_code == 404
    assert _upload(client_b, account["id"]).status_code == 404
    assert (
        client_b.post(f"/api/movement-imports/{imported['id']}/withdrawals/confirm").status_code
        == 404
    )


def test_a_refused_file_quotes_no_cell(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    secret = BINANCE.never_stored.user_id
    response = _upload(client_a, account["id"], BINANCE.cases[0].transactions.encode())
    assert response.status_code == 422
    assert "exchange.account_mismatch" in response.text
    assert secret not in response.text
    bad = f"User ID,Time,Account,Operation,Coin,Change,Remark\n{secret},x,Spot,Deposit,EUR,1,\n"
    binance = create(client_a, "/api/accounts", ACCOUNT | {"provider": "Binance", "bank": "B"})
    response = _upload(client_a, binance["id"], bad.encode())
    assert response.status_code == 422 and secret not in response.text


def test_a_typed_zero_balance_comes_back_plain_and_can_be_sent_again(client_a):
    """A typed 0 is stored as numeric(28,10) 0.0000000000; it came back as "0E-10", which the
    form pre-filled and the next save refused as not a number."""
    account = create(client_a, "/api/accounts", ACCOUNT)
    imported = ok(_upload(client_a, account["id"]), 201)["movement_import"]
    [view] = ok(client_a.get(f"/api/accounts/{account['id']}/exchange"))["imports"]
    balances = {b["symbol"]: b for b in view["balances"]}
    body = {
        "balances": [
            {"asset_id": balances["EUR"]["asset_id"], "units": "250.00"},
            {"asset_id": balances["BTC"]["asset_id"], "units": "0"},
        ]
    }
    ok(client_a.post(f"/api/movement-imports/{imported['id']}/holdings", json=body))
    [view] = ok(client_a.get(f"/api/accounts/{account['id']}/exchange"))["imports"]
    typed = {b["symbol"]: b["typed"] for b in view["balances"]}
    assert typed["BTC"] == "0.0000000000"
    assert all("E" not in (value or "") for value in typed.values())
    again = {
        "balances": [
            {"asset_id": b["asset_id"], "units": b["typed"]}
            for b in view["balances"]
            if b["typed"] is not None
        ]
    }
    ok(client_a.post(f"/api/movement-imports/{imported['id']}/holdings", json=again))


def test_the_page_of_an_account_that_is_no_exchange_is_refused(client_a):
    bank = create(
        client_a,
        "/api/accounts",
        {
            "label": "Giro",
            "bank": "Sparkasse",
            "iban": "DE50100000000000000001",
            "opening_balance": "0.00",
            "opening_balance_on": "2026-01-01",
        },
    )
    response = client_a.get(f"/api/accounts/{bank['id']}/exchange")
    assert response.status_code == 422 and "exchange.not_an_exchange" in response.text
