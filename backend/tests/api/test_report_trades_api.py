"""POST /accounts/{id}/report-trades, „Umsätze aus dem Bericht buchen“ (phase 6 D165 amended,
15 Nov 2026): status codes and isolation. The booking itself is
tests/db/test_services_depot_report_trades.py. Invented accounts only."""

import pytest
from api_support import create, ok

pytestmark = pytest.mark.db

DEPOT = {
    "label": "Depot",
    "bank": "Beispiel",
    "kind": "depot",
    "opening_balance": "0.00",
    "opening_balance_on": "2026-03-01",
}


def test_report_trades_books_nothing_without_a_report_and_needs_a_depot(client_a, client_b):
    depot = create(client_a, "/api/accounts", DEPOT)
    url = f"/api/accounts/{depot['id']}/report-trades"
    assert ok(client_a.post(url)) == {"booked": 0, "prices_stored": 0}
    # Another workspace does not see the account.
    assert client_b.post(url).status_code == 404

    exchange = create(
        client_a,
        "/api/accounts",
        DEPOT | {"label": "Börse", "kind": "exchange", "provider": "BISON"},
    )
    refused = ok(client_a.post(f"/api/accounts/{exchange['id']}/report-trades"), 422)
    assert refused["code"] == "depot.account_required"
