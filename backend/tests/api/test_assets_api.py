"""The asset and price routes (docs/phases/phase-6.md §12, D167, D174; phase 6 step 5b):
status codes, the read shapes and workspace isolation. The service behaviour is
tests/db/test_services_prices_overview.py. Invented coins, funds and prices only."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from api_support import create, ok
from sqlalchemy import insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table

pytestmark = pytest.mark.db

AT = datetime(2026, 6, 24, 9, 30, tzinfo=UTC)


def _seed(engine, ws, account_id):
    with workspace_transaction(engine, ws) as conn:

        def asset(**values):
            return conn.execute(
                insert(asset_table)
                .values(workspace_id=ws, created_at=AT, updated_at=AT, **values)
                .returning(asset_table.c.id)
            ).scalar_one()

        coin = asset(kind="crypto", symbol="ZZZ", name="Unbekannte Münze")
        euro = asset(kind="cash", symbol="EUR", name="Euro", currency="EUR")
        for asset_id, units in ((coin, "3"), (euro, "10.5")):
            conn.execute(
                insert(movement_table).values(
                    workspace_id=ws,
                    account_id=account_id,
                    asset_id=asset_id,
                    kind="acquisition",
                    quantity=Decimal(units),
                    occurred_at=AT,
                    source="user",
                    status="confirmed",
                    created_at=AT,
                )
            )
    return str(coin), str(euro)


def test_overview_assets_patch_and_price(app_engine, auth_a, client_a, client_b):
    depot = create(
        client_a,
        "/api/accounts",
        {
            "label": "Depot",
            "bank": "Beispiel",
            "kind": "depot",
            "opening_balance": "0.00",
            "opening_balance_on": "2026-06-01",
        },
    )
    coin, euro = _seed(app_engine, auth_a.workspace_id, depot["id"])

    seen = ok(client_a.get("/api/assets/overview", params={"on": "2026-06-27"}))
    assert seen["on"] == "2026-06-27" and seen["total_eur"] == "10.50"
    [account] = seen["accounts"]
    assert account["label"] == "Depot" and account["kind"] == "depot"
    first, second = account["positions"]
    assert (first["asset_id"], first["value_eur"]) == (euro, "10.50")
    assert (second["asset_id"], second["not_valued_reason"]) == (coin, "price.no_source")
    assert ok(client_a.get("/api/assets/overview"))["on"]  # today by default

    listed = {a["id"]: a for a in ok(client_a.get("/api/assets"))}
    assert listed[coin]["has_source"] is False and listed[euro]["has_source"] is True

    patched = ok(client_a.patch(f"/api/assets/{coin}", json={"coingecko_id": "zzz-coin"}))
    assert patched["has_source"] is True and patched["coingecko_id"] == "zzz-coin"
    refused = client_a.patch(f"/api/assets/{euro}", json={"kraken_pair": "EUREUR"})
    assert refused.status_code == 422 and refused.json()["code"] == "asset.not_crypto"

    body = {"on": "2026-06-26", "price_eur": "12.50"}
    price = ok(client_a.post(f"/api/assets/{coin}/prices", json=body), 201)
    assert (price["source"], price["price_eur"], price["on"]) == (
        "user",
        "12.50000000",
        "2026-06-26",
    )
    euro_price = client_a.post(f"/api/assets/{euro}/prices", json=body)
    assert euro_price.status_code == 422 and euro_price.json()["code"] == "asset.is_euro"
    valued = ok(client_a.get("/api/assets/overview", params={"on": "2026-06-27"}))
    coin_row = valued["accounts"][0]["positions"][1]
    assert coin_row["value_eur"] == "37.50" and coin_row["price"]["source"] == "user"

    bad = client_a.post(f"/api/assets/{coin}/prices", json={"on": "2026-06-26", "price_eur": "0"})
    assert bad.status_code == 422

    # Another workspace sees nothing and cannot touch these assets.
    assert ok(client_b.get("/api/assets/overview"))["accounts"] == []
    assert ok(client_b.get("/api/assets")) == []
    assert client_b.patch(f"/api/assets/{coin}", json={"name": "x"}).status_code == 404
    assert client_b.post(f"/api/assets/{coin}/prices", json=body).status_code == 404
