"""POST /transfers/own-wallet-deposit, „An eigene Wallet“ (phase 7 D181 (c) amended 16 Nov
2026), through the API: its status codes and what it returns. The service's behaviour is in
tests/db/test_wallet_sync_bsc_deposits.py; invented data only."""

import uuid

from db.test_wallet_sync_bsc_deposits import Seed, _deposits, _hash

URL = "/api/transfers/own-wallet-deposit"


def _body(withdrawal, address, remember=True):
    return {
        "withdrawal_movement_id": str(withdrawal),
        "address_id": str(address),
        "remember_withdrawal_pair": remember,
    }


def test_the_route_books_once_and_maps_each_refusal(client_a, client_b, dbname, fresh_workspaces):
    ws = fresh_workspaces[0]
    seed = Seed(dbname, ws)
    bison = seed.withdrawal("2.001", fee="0.001", tx_hash=None, network=None)
    hashed = seed.withdrawal(tx_hash=_hash("to be looked up first"))

    assert client_a.post(URL, json=_body(uuid.uuid4(), seed.address)).status_code == 404
    assert client_b.post(URL, json=_body(bison, seed.address)).status_code == 404
    refused = client_a.post(URL, json=_body(hashed, seed.address))
    assert refused.status_code == 422
    assert "transfer.legs_invalid" in refused.text

    created = client_a.post(URL, json=_body(bison, seed.address))
    assert created.status_code == 201, created.text
    link = created.json()
    assert (link["out_movement_id"], link["matched_by"], link["learned"]) == (
        str(bison),
        "user",
        "learned",
    )
    assert client_a.post(URL, json=_body(bison, seed.address)).status_code == 409
    assert len(_deposits(dbname, seed.address)) == 1


def test_a_binance_row_without_its_history_is_refused_by_the_route(
    client_a, dbname, fresh_workspaces
):
    """Review of 16 Nov 2026: Binance's gross row with no fee, network or hash waits for its
    withdrawal history; the route refuses it as legs that do not fit and writes nothing."""
    ws = fresh_workspaces[0]
    seed = Seed(dbname, ws, provider="Binance")
    gross = seed.withdrawal("1.0005", fee=None, tx_hash=None, network=None)

    refused = client_a.post(URL, json=_body(gross, seed.address))
    assert refused.status_code == 422
    assert "transfer.legs_invalid" in refused.text
    assert _deposits(dbname, seed.address) == []
