"""The waiting line of a wallet through the API: the Krypto tab, the Anlagen overview and the
net worth (16 Nov 2026; docs/phases/phase-7.md step 6's as-built note). Invented wallet, coin
and quantities only; never an address or a hash in the answer."""

from decimal import Decimal

import pytest
from api_support import ok
from db.db_support import _admin, _insert
from db.test_phase6_migration import _asset
from db.test_phase7_migration import EVM, _address, _chain_movement, _group_row, _wallet

pytestmark = pytest.mark.db


@pytest.fixture
def waiting_wallet(dbname, fresh_workspaces):
    """An Ethereum wallet whose one transaction (0.4 ETH in, its fee) waits for a person."""
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws, label="Testwallet")
        address = _address(conn, ws, account)
        eth = _asset(conn, ws, symbol="ETH", name="Ether", chain="ethereum")
        row = _group_row(ws, account, status="proposed")
        group = _insert(conn, "movement_group", row)
        for index, (kind, quantity) in enumerate(
            [("internal_transfer_in", "0.4"), ("fee", "-0.001")]
        ):
            _insert(
                conn,
                "asset_movement",
                _chain_movement(
                    ws,
                    account,
                    eth,
                    address,
                    kind=kind,
                    status="proposed",
                    group_id=group,
                    quantity=quantity,
                    index=str(index),
                ),
            )
    return str(account), row["tx_hash"]


def test_each_screen_names_the_waiting_wallet(client_a, client_b, waiting_wallet):
    account, tx_hash = waiting_wallet
    expected = (
        "0,399 ETH auf „Testwallet“ sind gelesen, zählen aber erst nach Ihrer Bestätigung mit "
        "(1 Transaktion wartet)."
    )
    on = {"on": "2026-11-16"}
    tab = client_a.get("/api/holdings/crypto", params=on)
    [line] = ok(tab)["waiting"]
    assert (line["account_id"], line["groups"], line["text"]) == (account, 1, expected)
    assert [(c["symbol"], Decimal(c["units"])) for c in line["coins"]] == [
        ("ETH", Decimal("0.399"))
    ]
    assert (line["action"], line["href"]) == ("Transaktionen prüfen", f"/accounts/{account}")

    overview = client_a.get("/api/assets/overview", params=on)
    seen = ok(overview)
    assert seen["accounts"] == []  # nothing confirmed: no row, the waiting line instead
    assert [w["text"] for w in seen["waiting"]] == [expected]

    figure = client_a.get("/api/net-worth", params=on)
    [todo] = [t for t in ok(figure)["todos"] if t["code"] == "net_worth.wallet_waiting"]
    assert todo["text"] == f"{expected} Bis dahin fehlen sie im Nettovermögen."
    assert todo["href"] == f"/accounts/{account}"

    for response in (tab, overview, figure):
        assert EVM not in response.text and tx_hash not in response.text

    # Another household sees nothing of it.
    assert ok(client_b.get("/api/holdings/crypto", params=on))["waiting"] == []
    assert ok(client_b.get("/api/assets/overview", params=on))["waiting"] == []
