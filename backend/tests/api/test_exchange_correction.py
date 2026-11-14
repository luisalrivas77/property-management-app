"""„Differenz als Korrektur buchen“ through the API (docs/phases/phase-6.md D178 amended,
migration 0030): the reason required, the coin's difference booked and the import
reconciled, the movement listed with its reason, EUR refused, and isolation. The service
behaviour is tests/db/test_services_exchange_correction.py. Invented figures."""

import pytest
from api_support import create, ok
from test_exchange import ACCOUNT, _upload

pytestmark = pytest.mark.db


def test_a_coin_difference_booked_as_a_correction(client_a, client_b):
    account = create(client_a, "/api/accounts", ACCOUNT)
    imported = ok(_upload(client_a, account["id"]), 201)["movement_import"]
    [view] = ok(client_a.get(f"/api/accounts/{account['id']}/exchange"))["imports"]
    ids = {b["symbol"]: b["asset_id"] for b in view["balances"]}
    wrong = ok(
        client_a.post(
            f"/api/movement-imports/{imported['id']}/holdings",
            json={
                "balances": [
                    {"asset_id": ids["EUR"], "units": "251.00"},
                    {"asset_id": ids["BTC"], "units": "0.00019965"},
                ]
            },
        )
    )
    assert wrong["status"] == "unreconciled"
    url = f"/api/movement-imports/{imported['id']}/corrections"

    missing = client_a.post(url, json={"asset_id": ids["BTC"], "reason": "  "})
    assert missing.status_code == 422
    eur = client_a.post(url, json={"asset_id": ids["EUR"], "reason": "Gutschrift fehlt"})
    assert eur.status_code == 422 and "exchange.correction_eur" in eur.text
    assert client_b.post(url, json={"asset_id": ids["BTC"], "reason": "abc"}).status_code == 404

    booked = ok(client_a.post(url, json={"asset_id": ids["BTC"], "reason": "Gutschrift fehlt"}))
    # EUR still differs, so the import stays unreconciled; BTC agrees now.
    assert booked["status"] == "unreconciled"
    balances = {b["symbol"]: b for b in booked["balances"]}
    assert (balances["BTC"]["reconciled"], balances["EUR"]["reconciled"]) == (True, False)
    movements = ok(client_a.get(f"/api/accounts/{account['id']}/movements"))
    [correction] = [m for m in movements if m["kind"] == "balance_correction"]
    assert (correction["note"], correction["source"], correction["eur_amount"]) == (
        "Gutschrift fehlt",
        "user",
        None,
    )
    assert correction["quantity"].startswith("0.0001")
