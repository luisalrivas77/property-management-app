"""Lots, the Krypto tab and the year-end statement through the API (docs/phases/phase-7.md
D188, D190, §13; step 6). Invented accounts, coins and prices only; the API's clock is the real
one, so the statement is exported for 2025 (a year that is over)."""

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

pytestmark = pytest.mark.db

EXCHANGE = {
    "label": "Börse",
    "bank": "Testanbieter",
    "kind": "exchange",
    "provider": "Binance",
    "opening_balance": "0.00",
    "opening_balance_on": "2025-01-01",
}


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so an export's created_by (FK to app_user) is real."""
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


def _bought(client, dbname, workspace_id) -> tuple[str, str]:
    """An exchange account that bought 0.5 BTC for 20,000 € on 1 Mar 2025, and the coin."""
    account = create(client, "/api/accounts", EXCHANGE)["id"]
    with _admin(dbname) as conn:
        asset = conn.execute(
            "INSERT INTO asset (workspace_id, kind, symbol, name) VALUES (%s, 'crypto', 'BTC',"
            " 'Bitcoin') RETURNING id",
            (workspace_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO asset_movement (workspace_id, account_id, asset_id, kind, quantity,"
            " occurred_at, eur_amount, source, status) VALUES (%s, %s, %s, 'acquisition', 0.5,"
            " '2025-03-01T10:00:00Z', -20000.00, 'user', 'confirmed')",
            (workspace_id, account, asset),
        )
    return account, str(asset)


def test_the_tab_the_lots_and_the_statement(client_a, client_b, dbname, sessions):
    workspace_a = sessions[TOKEN_A].workspace_id
    account, asset = _bought(client_a, dbname, workspace_a)

    tab = ok(client_a.get("/api/holdings/crypto", params={"on": "2025-12-31"}))
    [coin] = tab["coins"]
    [lot] = coin["lots"]
    assert (lot["acquired_on"], lot["cost_eur"], lot["tax_free_from"]) == (
        "2025-03-01",
        "20000.00",
        "2026-03-02",
    )
    lots = ok(client_a.get(f"/api/wallets/{account}/lots", params={"asset": asset}))
    assert [Decimal(x["units_open"]) for x in lots] == [Decimal("0.5")]
    seen = ok(client_a.get("/api/wallets/holdings", params={"on": "2025-12-31"}))
    assert [a["account_id"] for a in seen["accounts"]] == [account]

    # 2025 is over: one statement for the one account, downloaded through its signed link.
    [export] = ok(client_a.post("/api/exports/holdings", json={"year": 2025}), 201)
    assert (export["account_id"], export["as_of"]) == (account, "2025-12-31")
    pdf = client_a.get(export["pdf_url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["cache-control"] == "private, no-store"
    assert "attachment" in pdf.headers["content-disposition"]
    csv = client_a.get(export["csv_url"])
    assert csv.headers["content-type"].startswith("text/csv")
    assert "Posten;BTC;;0,5;;;;2025-03-01;20000,00;2026-03-02;" in csv.content.decode("utf-8-sig")
    assert [e["id"] for e in ok(client_a.get("/api/exports/holdings"))] == [export["id"]]

    # A wrong, expired or borrowed link answers 404; the year must be over.
    url = urlsplit(export["pdf_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    wrong = query | {"signature": "0" * len(query["signature"])}
    assert client_a.get(url.path, params=wrong).status_code == 404
    expired = query | {"expires": str(int(datetime.now(UTC).timestamp()) - 10)}
    assert client_a.get(url.path, params=expired).status_code == 404
    as_csv = query | {"format": "csv"}  # signed for the PDF only
    assert client_a.get(url.path, params=as_csv).status_code == 404
    assert client_b.get(export["pdf_url"]).status_code == 404
    this_year = datetime.now(UTC).year
    refused = client_a.post("/api/exports/holdings", json={"year": this_year})
    assert refused.status_code == 422
    assert refused.json()["code"] == "holdings_export.year_open"
    nothing = client_b.post("/api/exports/holdings", json={"year": 2025})
    assert nothing.status_code == 422
    assert nothing.json()["code"] == "holdings_export.nothing_held"

    # Workspace isolation.
    assert ok(client_b.get("/api/holdings/crypto"))["coins"] == []
    assert ok(client_b.get("/api/exports/holdings")) == []
    assert ok(client_b.get(f"/api/wallets/{account}/lots")) == []
