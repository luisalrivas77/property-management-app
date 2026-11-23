"""GET /assets/performance (docs/phases/phase-10.md §7.6 Anlagen, items 1–3, build decision (b)):
each portfolio's value equals its total on GET /assets/overview, the payments in and out it
reads, a coin move between two own exchanges netting out in the Krypto total, and workspace
isolation. The arithmetic is tests/rules/performance/test_perf_portfolio.py. Invented coins,
amounts and prices only."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from api_support import create, ok
from sqlalchemy import insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.services.documents import LOCAL_TIME_ZONE

pytestmark = pytest.mark.db

TODAY = datetime.now(LOCAL_TIME_ZONE).date()
DEPOSIT_ON = TODAY - timedelta(days=500)
BUY_ON = TODAY - timedelta(days=480)
MOVE_ON = TODAY - timedelta(days=200)


def _exchange(client, label):
    return create(
        client,
        "/api/accounts",
        {
            "label": label,
            "bank": "Testanbieter",
            "kind": "exchange",
            "provider": "BISON",
            "opening_balance": "0.00",
            "opening_balance_on": str(DEPOSIT_ON - timedelta(days=1)),
        },
    )["id"]


def _seed(engine, ws, first, second):
    """Exchange 1: 1,000 € deposited (from outside), 2 coins bought for 600 €, 1 coin moved to
    exchange 2 on a day the coin cost 400 €; the coin costs 500 € today.
      exchange 1: value 400 € + 1 coin = 900; in 1,000, out 400 → gain 300, 30 %
      exchange 2: value 500;                in 400            → gain 100, 25 %
      Krypto:     value 1,400; in 1,000 (the move nets out)    → gain 400, 40 %"""
    at = datetime.combine(TODAY, time(9), UTC)
    with workspace_transaction(engine, ws) as conn:

        def asset(**values):
            return conn.execute(
                insert(asset_table)
                .values(workspace_id=ws, created_at=at, updated_at=at, **values)
                .returning(asset_table.c.id)
            ).scalar_one()

        def move(account, asset_id, kind, units, on, eur=None):
            return conn.execute(
                insert(movement_table)
                .values(
                    workspace_id=ws,
                    account_id=account,
                    asset_id=asset_id,
                    kind=kind,
                    quantity=Decimal(units),
                    eur_amount=None if eur is None else Decimal(eur),
                    occurred_at=datetime.combine(on, time(10), UTC),
                    source="user",
                    status="confirmed",
                    created_at=at,
                )
                .returning(movement_table.c.id)
            ).scalar_one()

        coin = asset(kind="crypto", symbol="ZZQ", name="Testmünze")
        euro = asset(kind="cash", symbol="EUR", name="Euro", currency="EUR")
        move(first, euro, "internal_transfer_in", "1000", DEPOSIT_ON)
        move(first, coin, "acquisition", "2", BUY_ON, eur="-600.00")
        out = move(first, coin, "internal_transfer_out", "-1", MOVE_ON)
        into = move(second, coin, "internal_transfer_in", "1", MOVE_ON)
        conn.execute(
            insert(link_table).values(
                workspace_id=ws,
                kind="chain",
                out_movement_id=out,
                in_movement_id=into,
                matched_by="user",
                created_at=at,
            )
        )
        for on, price in ((MOVE_ON, "400"), (TODAY, "500")):
            conn.execute(
                insert(price_table).values(
                    workspace_id=ws,
                    asset_id=coin,
                    on=on,
                    price_eur=Decimal(price),
                    source="user",
                    fetched_at=at,
                    raw={},
                    created_at=at,
                )
            )
    return str(out), str(into)


def test_each_portfolio_its_flows_and_the_crypto_total(app_engine, auth_a, client_a, client_b):
    first = _exchange(client_a, "Börse eins")
    second = _exchange(client_a, "Börse zwei")
    out, into = _seed(app_engine, auth_a.workspace_id, first, second)

    seen = ok(client_a.get("/api/assets/performance"))
    overview = ok(client_a.get("/api/assets/overview"))
    totals = {a["account_id"]: a["total_eur"] for a in overview["accounts"]}
    by_id = {p["account_id"]: p for p in seen["portfolios"]}
    assert {i: by_id[i]["figures"]["value_eur"] for i in by_id} == totals
    assert totals == {first: "900.00", second: "500.00"}

    one = by_id[first]
    assert one["figures"]["start"] == str(DEPOSIT_ON)
    assert (one["figures"]["paid_in_eur"], one["figures"]["taken_out_eur"]) == ("1000.00", "400.00")
    assert (one["figures"]["gain_eur"], one["figures"]["gain_ratio"]) == ("300.00", "0.300000")
    assert one["figures"]["yearly_return"] is not None and one["figures"]["no_rate"] is None
    deposit, moved = one["flows"]
    assert (deposit["kind"], deposit["amount_eur"], deposit["internal"]) == (
        "transfer",
        "1000.00",
        False,
    )
    assert (moved["kind"], moved["amount_eur"], moved["internal"]) == ("units", "-400.00", True)
    assert (moved["movement_id"], moved["counterpart_account_id"]) == (out, second)

    two = by_id[second]
    assert (two["figures"]["gain_eur"], two["figures"]["gain_ratio"]) == ("100.00", "0.250000")
    assert two["figures"]["yearly_return"] is None  # 200 days: euros and percent only
    assert two["figures"]["no_rate"] == "short_history"
    [received] = two["flows"]
    assert (received["movement_id"], received["internal"]) == (into, True)

    crypto = seen["crypto"]
    assert (crypto["value_eur"], crypto["paid_in_eur"], crypto["taken_out_eur"]) == (
        "1400.00",
        "1000.00",
        "0.00",
    )
    assert (crypto["gain_eur"], crypto["gain_ratio"]) == ("400.00", "0.400000")
    assert seen["invested"] == crypto
    assert seen["etf"]["value_eur"] == "0.00" and seen["etf"]["no_rate"] == "no_flows"
    assert seen["total_eur"] == "1400.00"  # no property
    # Anlagen › Übersicht shows this total above the split read from GET /assets/overview, so the
    # invested value is the overview's total (its cash and money in transit included).
    assert seen["invested"]["value_eur"] == overview["total_eur"]
    assert (seen["distributions_year"], seen["distributions_eur"]) == (TODAY.year, "0.00")

    assert ok(client_b.get("/api/assets/performance"))["portfolios"] == []
