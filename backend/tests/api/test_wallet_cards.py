"""The wallet page's state and the wallet cards through the API (docs/phases/phase-7.md §13,
§14, D183 (5), D185 (2), D186, D187; the wallet screens).

A Bitcoin wallet is read end to end over the fake Esplora layer of
tests/db/test_wallet_sync_bitcoin.py (invented addresses and txids), then its cards are
answered through the routes; an Ethereum wallet with a token is seeded directly for the spam
verdict. No household fact; every response is checked for a full address or a txid (D179).
"""

import itertools
import json
import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from api_support import ok
from db.db_support import _admin
from db.test_phase7_migration import EVM, _address, _chain_movement, _group_row, _wallet
from db.test_wallet_sync_bitcoin import (  # noqa: F401  (esplora is a fixture)
    CHANGE,
    CHANGE3,
    OWN,
    PAYEE,
    SECOND,
    STRANGER,
    T0,
    _last_runs,
    _live_groups,
    _movements,
    _rows,
    esplora,
    record,
    txid,
)
from db.test_wallet_sync_core import ACTOR, SETTINGS

from hausbuch.services.chain import sync as sync_mod
from hausbuch.services.chain.sync import sync_wallets

pytestmark = pytest.mark.db

TOKEN = "0x" + "7e" * 20  # an invented contract


def _private(response_text: str, *secrets: str) -> None:
    """No full address and no txid in an answer (D179)."""
    for secret in secrets:
        assert secret.lower() not in response_text.lower()


ADDRESSES = (OWN, SECOND, CHANGE, CHANGE3, PAYEE, STRANGER)


def _tx_hashes() -> list[str]:
    return [txid(n) for n in range(1, 12)]


@pytest.fixture
def btc(dbname, fresh_workspaces, esplora, app_engine, blob_store):  # noqa: F811
    """A two-address Bitcoin wallet of workspace A after its first sync: receives 1, 3 and 6
    (uncategorised), a spend 2 with change to the second address, and two cards (spends 4
    and 7) that both ask about the same unknown change address."""
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        a = _address(conn, ws, account, chain="bitcoin", address=OWN, address_masked="bc1q…rtxz")
        b = _address(conn, ws, account, chain="bitcoin", address=SECOND, address_masked="bc1q…zqrl")
    esplora.records = [
        record(1, 100, [(STRANGER, 101_000)], [(OWN, 100_000)]),
        record(2, 101, [(OWN, 100_000)], [(PAYEE, 30_000), (SECOND, 69_000)]),
        record(3, 102, [(STRANGER, 51_000)], [(OWN, 50_000)]),
        record(4, 103, [(OWN, 50_000)], [(PAYEE, 20_000), (CHANGE, 29_500)]),
        record(6, 104, [(STRANGER, 41_000)], [(OWN, 40_000)]),
        record(7, 105, [(OWN, 40_000)], [(CHANGE3, 10_000), (CHANGE, 29_000)]),
    ]
    esplora.tip = 112
    ticks = itertools.count()

    def sync():
        return sync_wallets(
            app_engine,
            ws,
            settings=SETTINGS,
            store=blob_store,
            actor=ACTOR,
            clock=lambda: T0 + timedelta(days=1, seconds=next(ticks)),
        )

    sync()
    return ws, account, a, b, sync


def _group(dbname, account, n):
    (group,) = [g for g in _live_groups(dbname, account) if g.tx_hash == txid(n)]
    return group


def test_the_state_names_each_address_and_waits_for_the_cards(client_a, client_b, btc, dbname):
    ws, account, a, b, _ = btc
    response = client_a.get(f"/api/wallets/{account}/state")
    state = ok(response)
    _private(response.text, *ADDRESSES, *_tx_hashes())
    assert [(s["address_masked"], s["state"]) for s in state["statuses"]] == [
        ("bc1q…rtxz", "waiting"),
        ("bc1q…zqrl", "waiting"),
    ]
    assert all(s["text"].startswith("Wartet auf Antwort") for s in state["statuses"])
    assert state["open_groups"] == 6 and state["open_questions"] == 2
    # Bitcoin reconciles the wallet's addresses together: one holding for the account; the
    # booked figure waits for the answers (the difference is not stored while waiting)
    (holding,) = state["holdings"]
    assert holding["address_id"] is None and holding["symbol"] == "BTC"
    assert Decimal(holding["chain_units"]) == Decimal("0.00069")  # all on the second address
    assert holding["booked_units"] is None and holding["state"] == "waiting"

    assert client_b.get(f"/api/wallets/{account}/state").status_code == 404
    assert client_b.get(f"/api/wallets/{account}/groups").status_code == 404


def test_a_first_syncs_groups_are_listed_questions_first_and_answered(client_a, btc, dbname):
    ws, account, a, b, sync = btc
    response = client_a.get(f"/api/wallets/{account}/groups")
    groups = ok(response)
    _private(response.text, *ADDRESSES, *_tx_hashes())
    assert [g["card"] for g in groups] == ["change", "change", *["proposed"] * 4]
    assert all(g["question"] is None for g in groups[:2])  # the change card has its own view
    receive = next(g for g in groups if g["id"] == str(_group(dbname, account, 1).id))
    assert receive["confirmable"] is False  # an uncategorised leg needs an answer
    assert receive["answers"] == ["gift", "reward"]  # no spam for the chain's own coin
    assert receive["text"] == "0,001 BTC erhalten (bc1q…rtxz). Was war das?"
    spend = next(g for g in groups if g["id"] == str(_group(dbname, account, 2).id))
    assert spend["answers"] == ["payment"]
    assert [leg["kind"] for leg in spend["legs"]] == ["uncategorized", "fee"]

    refused = client_a.post(f"/api/groups/{receive['id']}/confirm")
    assert refused.status_code == 422
    assert refused.json()["code"] == "wallet.group_needs_answer"
    unfit = client_a.post(f"/api/groups/{receive['id']}/answer", json={"answer": "payment"})
    assert unfit.json()["code"] == "wallet.answer_unfit"
    unfit = client_a.post(f"/api/groups/{receive['id']}/answer", json={"answer": "spam"})
    assert unfit.json()["code"] == "wallet.answer_unfit"  # BTC is no token

    body = {"answer": "gift", "cost_eur": "12.50"}
    new_id = ok(client_a.post(f"/api/groups/{receive['id']}/answer", json=body))["group_id"]
    again = client_a.post(f"/api/groups/{receive['id']}/answer", json=body)
    assert again.status_code == 422 and again.json()["code"] == "wallet.group_done"
    paid = ok(client_a.post(f"/api/groups/{spend['id']}/answer", json={"answer": "payment"}))
    reward = _group(dbname, account, 3).id
    ok(client_a.post(f"/api/groups/{reward}/answer", json={"answer": "reward"}))

    rows = _rows(
        dbname,
        "SELECT g.id, g.kind, g.status, m.kind AS leg, m.source, m.status AS leg_status,"
        ' m.eur_amount, m.movement_hash, m."index", m.address_id, m.quantity'
        " FROM movement_group g JOIN asset_movement m ON m.group_id = g.id"
        ' WHERE g.id IN (%s, %s) ORDER BY g.id = %s DESC, m."index"',
        new_id,
        paid["group_id"],
        new_id,
    )
    gift, *payment = rows
    assert (gift.kind, gift.status, gift.leg, gift.source, gift.leg_status) == (
        "transfer",
        "confirmed",
        "acquisition",
        "user",
        "confirmed",
    )
    assert gift.eur_amount == Decimal("-12.50")  # signed like cash: the cost was paid
    assert gift.movement_hash is None and gift.index == "0" and gift.address_id == a
    assert [(p.leg, p.quantity) for p in payment] == [
        ("fee", Decimal("-0.00001000")),
        ("disposal", Decimal("-0.00030000")),
    ]
    (old,) = _rows(dbname, "SELECT superseded_by FROM movement_group WHERE id = %s", receive["id"])
    assert str(old.superseded_by) == new_id
    events = _rows(
        dbname,
        "SELECT type, payload FROM event WHERE workspace_id = %s AND type IN"
        " ('movement.superseded', 'movement_group.confirmed') ORDER BY seq",
        ws,
    )
    assert [e.type for e in events].count("movement_group.confirmed") == 3
    assert all(e.payload.get("reason") == "routing.person" for e in events if "group" in e.type)

    # a re-sync writes no leg again (the superseded chain rows keep their hashes)
    before = len(_movements(dbname, account))
    sync()
    assert len(_movements(dbname, account)) == before
    assert {str(g.id) for g in _live_groups(dbname, account)} >= {new_id, paid["group_id"]}


def test_ja_eigene_adresse_registers_the_change_and_settles_both_cards(
    client_a,
    btc,
    dbname,
    esplora,  # noqa: F811
):
    ws, account, a, b, sync = btc
    four, seven = _group(dbname, account, 4), _group(dbname, account, 7)
    response = client_a.get(f"/api/groups/{four.id}/change")
    card = ok(response)
    _private(response.text, *ADDRESSES, *_tx_hashes())
    assert card["original_missing"] is False
    asked = [(c["vout"], c["address_masked"], Decimal(c["amount_btc"])) for c in card["candidates"]]
    assert asked == [
        (0, "bc1q…atuf", Decimal("0.0002")),
        (1, "bc1q…gz2k", Decimal("0.000295")),
    ]

    calls = esplora.calls
    settled = ok(client_a.post(f"/api/groups/{four.id}/change/own", json={"vout": 1}))
    assert esplora.calls == calls  # answered from the stored original
    live = {g.tx_hash: g for g in _live_groups(dbname, account)}
    assert (str(live[txid(4)].id), live[txid(4)].kind, live[txid(4)].question) == (
        settled["group_id"],
        "transfer",
        None,
    )
    # the other card asked about the same address: derived again, a transfer too
    assert live[txid(7)].id != seven.id
    assert (live[txid(7)].kind, live[txid(7)].question) == ("transfer", None)

    (change,) = _rows(
        dbname,
        "SELECT id, kind, address_masked, discovered_from FROM wallet_address"
        " WHERE account_id = %s AND kind = 'change'",
        account,
    )
    assert (change.address_masked, change.discovered_from) == ("bc1q…gz2k", None)
    (added,) = _rows(
        dbname,
        "SELECT payload FROM event WHERE type = 'wallet.address_added' AND entity_id = %s",
        account,
    )
    assert added.payload["kind"] == "change" and added.payload["address_masked"] == "bc1q…gz2k"
    assert CHANGE not in json.dumps(added.payload)
    gone = client_a.post(f"/api/groups/{four.id}/change/own", json={"vout": 1})
    assert gone.status_code == 422 and gone.json()["code"] == "wallet.change_answered"

    # the first sync of the change address writes nothing twice and the wallet reconciles
    before = sorted((m.tx_hash, m.index) for m in _movements(dbname, account))
    sync()
    assert sorted((m.tx_hash, m.index) for m in _movements(dbname, account)) == before
    assert any(r.reconciled is True for r in _last_runs(dbname, account))
    state = ok(client_a.get(f"/api/wallets/{account}/state"))
    assert {s["state"] for s in state["statuses"]} == {"reconciled"}
    (holding,) = state["holdings"]
    assert holding["booked_units"] == holding["chain_units"]


def test_another_household_reaches_no_group_of_this_wallet(client_a, client_b, btc, dbname):
    ws, account, a, b, _ = btc
    before = ok(client_a.get(f"/api/wallets/{account}/groups"))
    proposed = next(g for g in before if g["card"] == "proposed")
    four = _group(dbname, account, 4)
    attempts = [
        client_b.post(f"/api/groups/{proposed['id']}/confirm"),
        client_b.post(f"/api/groups/{proposed['id']}/answer", json={"answer": "gift"}),
        client_b.get(f"/api/groups/{four.id}/change"),
        client_b.post(f"/api/groups/{four.id}/change/own", json={"vout": 1}),
        client_b.post(f"/api/groups/{four.id}/change/payee", json={"vout": 0}),
        client_b.get(f"/api/wallets/{account}/hidden-tokens"),
    ]
    for response in attempts:
        assert response.status_code in (404, 422), response.text
        _private(response.text, *ADDRESSES, *_tx_hashes())
    # nothing changed for the wallet's own household
    assert ok(client_a.get(f"/api/wallets/{account}/groups")) == before
    assert (
        _rows(
            dbname,
            "SELECT id FROM wallet_address WHERE account_id = %s AND kind = 'change'",
            account,
        )
        == []
    )


def test_nein_empfaenger_through_the_route(client_a, btc, dbname):
    ws, account, a, b, _ = btc
    four = _group(dbname, account, 4)
    narrower = ok(client_a.post(f"/api/groups/{four.id}/change/payee", json={"vout": 0}))
    card = ok(client_a.get(f"/api/groups/{narrower['group_id']}/change"))
    assert [c["address_masked"] for c in card["candidates"]] == ["bc1q…gz2k"]
    gone = client_a.get(f"/api/groups/{four.id}/change")
    assert gone.status_code == 422 and gone.json()["code"] == "wallet.change_answered"


def test_a_missing_key_and_a_failed_run_say_what_to_do(client_a, fresh_workspaces, dbname):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        _address(conn, ws, account)  # an Ethereum address, and no Etherscan key is set
    response = client_a.get(f"/api/wallets/{account}/state")
    (status,) = ok(response)["statuses"]
    assert status["state"] == "key_missing"
    assert status["setting"] == "HAUSBUCH_ETHERSCAN_KEY"
    assert "HAUSBUCH_ETHERSCAN_KEY" in status["todo"] and "Scaleway" in status["todo"]
    assert "http" not in response.text and EVM not in response.text


@pytest.fixture
def token_wallet(dbname, fresh_workspaces):
    """An Ethereum wallet with one proposed receive of an invented token (not spam yet)."""
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        address = _address(conn, ws, account)
        asset = conn.execute(
            "INSERT INTO asset (workspace_id, kind, symbol, name, chain, contract_address,"
            " decimals) VALUES (%s, 'crypto', 'FREE', 'Free Token', 'ethereum', %s, 18)"
            " RETURNING id",
            (ws, TOKEN),
        ).fetchone()[0]
        row = _group_row(ws, account, kind="airdrop", status="proposed")
        group = conn.execute(
            "INSERT INTO movement_group (workspace_id, account_id, chain, tx_hash, occurred_at,"
            " kind, status) VALUES (%(workspace_id)s, %(account_id)s, %(chain)s, %(tx_hash)s,"
            " %(occurred_at)s, %(kind)s, %(status)s) RETURNING id",
            row,
        ).fetchone()[0]
        leg = _chain_movement(
            ws,
            account,
            asset,
            address,
            kind="airdrop",
            status="proposed",
            group_id=group,
            quantity="5.000000000000000000",
        )
        columns = ", ".join(f'"{k}"' for k in leg)
        values = ", ".join(f"%({k})s" for k in leg)
        conn.execute(f"INSERT INTO asset_movement ({columns}) VALUES ({values})", leg)
    return ws, account, asset, group, row["tx_hash"]


def test_a_token_is_hidden_as_spam_and_shown_again(client_a, client_b, token_wallet, dbname):
    ws, account, asset, group, tx_hash = token_wallet
    (card,) = ok(client_a.get(f"/api/wallets/{account}/groups"))
    assert card["confirmable"] is True and card["answers"] == ["gift", "reward", "spam"]

    assert client_b.post(f"/api/assets/{asset}/demote").status_code == 404
    hidden = ok(client_a.post(f"/api/assets/{asset}/demote"))
    assert hidden == {"asset_id": str(asset), "spam": True, "groups": 1}
    assert ok(client_a.get(f"/api/wallets/{account}/groups")) == []
    response = client_a.get(f"/api/wallets/{account}/hidden-tokens")
    (token,) = ok(response)
    assert (token["name"], Decimal(token["units"]), token["groups"]) == ("Free Token", 5, 1)
    _private(response.text, TOKEN, tx_hash, EVM)
    again = client_a.post(f"/api/assets/{asset}/demote")
    assert again.status_code == 422 and again.json()["code"] == "wallet.answer_unfit"

    assert client_b.post(f"/api/assets/{asset}/promote").status_code == 404
    shown = ok(client_a.post(f"/api/assets/{asset}/promote"))
    assert shown == {"asset_id": str(asset), "spam": False, "groups": 1}
    assert ok(client_a.get(f"/api/wallets/{account}/hidden-tokens")) == []
    (card,) = ok(client_a.get(f"/api/wallets/{account}/groups"))
    assert card["kind"] == "airdrop" and [leg["kind"] for leg in card["legs"]] == ["airdrop"]
    done = ok(client_a.post(f"/api/groups/{card['id']}/confirm"))
    assert done["group_id"] == card["id"]
    (row,) = _rows(
        dbname,
        "SELECT g.status, m.status AS leg FROM movement_group g JOIN asset_movement m"
        " ON m.group_id = g.id WHERE g.id = %s",
        card["id"],
    )
    assert (row.status, row.leg) == ("confirmed", "confirmed")
    flips = _rows(
        dbname,
        "SELECT payload FROM event WHERE type = 'asset.updated' AND entity_id = %s ORDER BY seq",
        asset,
    )
    assert [f.payload["changes"]["spam"]["new"] for f in flips] == [True, False]
    assert ok(client_a.get(f"/api/wallets/{account}/groups")) == []


def test_a_spam_answer_hides_the_token(client_a, token_wallet, dbname):
    ws, account, asset, group, _ = token_wallet
    new = ok(client_a.post(f"/api/groups/{group}/answer", json={"answer": "spam"}))
    (row,) = _rows(
        dbname,
        "SELECT g.kind, g.status, m.kind AS leg, a.spam FROM movement_group g"
        " JOIN asset_movement m ON m.group_id = g.id JOIN asset a ON a.id = m.asset_id"
        " WHERE g.id = %s",
        new["group_id"],
    )
    assert (row.kind, row.status, row.leg, row.spam) == ("spam", "confirmed", "spam", True)


def test_zu_pruefen_lists_the_wallet_cards(client_a, btc, dbname):
    ws, account, a, b, _ = btc
    response = client_a.get("/api/inbox")
    items = [i for i in ok(response) if i["kind"].startswith("wallet_")]
    _private(response.text, *ADDRESSES, *_tx_hashes())
    by_kind: dict[str, list] = {}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item)
    (summary,) = by_kind["wallet_groups"]
    assert summary["account_id"] == str(account)
    assert summary["reasoning"].startswith("„Hardware-Wallet“: 4 Transaktionen")
    cards = by_kind["wallet_change"]
    assert {c["group_id"] for c in cards} == {
        str(_group(dbname, account, 4).id),
        str(_group(dbname, account, 7).id),
    }
    assert set(by_kind) == {"wallet_groups", "wallet_change"}  # waiting is no sync card


def test_a_refused_run_and_a_missing_key_are_cards(client_a, fresh_workspaces, dbname):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        _address(conn, ws, account)  # Ethereum without a key
        _address(conn, ws, account, chain="arbitrum")
        btc_address = _address(
            conn, ws, account, chain="bitcoin", address=OWN, address_masked="bc1q…rtxz"
        )
        conn.execute(
            "INSERT INTO wallet_sync (workspace_id, account_id, address_id, started_at,"
            " finished_at, status, source_host, error_code) VALUES (%s, %s, %s, now(), now(),"
            " 'refused', 'esplora.example.invalid', 'wallet.source_refused')",
            (ws, account, btc_address),
        )
    response = client_a.get("/api/inbox")
    cards = sorted(
        (i for i in ok(response) if i["kind"] == "wallet_sync"), key=lambda i: i["error_code"]
    )
    assert [c["error_code"] for c in cards] == ["key_missing", "refused"]
    missing, refused = cards
    assert "Ethereum 0x5a…eaed, Arbitrum 0x5a…eaed" in missing["reasoning"]
    assert "HAUSBUCH_ETHERSCAN_KEY" in missing["question"]
    assert "HAUSBUCH_ESPLORA_URL" in refused["question"]
    _private(response.text, OWN, EVM, "esplora.example.invalid", "http")
    state = ok(client_a.get(f"/api/wallets/{account}/state"))
    assert [s["state"] for s in state["statuses"]] == ["key_missing", "key_missing", "refused"]


def test_an_answer_during_a_syncs_write_leaves_no_leg_under_a_superseded_group(
    client_a,
    fresh_workspaces,
    esplora,  # noqa: F811
    app_engine,
    blob_store,
    dbname,
    monkeypatch,
):
    """Review finding 3: a sync's write transaction locks the wallet's account row before it
    reads a transaction's live group, as an answer does, so an answer given while the sync
    writes waits for it, and the sync never adds a leg under a group the answer superseded."""
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        _address(conn, ws, account, chain="bitcoin", address=OWN, address_masked="bc1q…rtxz")
    esplora.records = [record(1, 100, [(STRANGER, 200_000)], [(OWN, 100_000), (SECOND, 50_000)])]
    esplora.tip = 112
    ticks = itertools.count()

    def sync():
        return sync_wallets(
            app_engine,
            ws,
            settings=SETTINGS,
            store=blob_store,
            actor=ACTOR,
            clock=lambda: T0 + timedelta(days=1, seconds=next(ticks)),
        )

    sync()
    (card,) = ok(client_a.get(f"/api/wallets/{account}/groups"))
    with _admin(dbname) as conn:  # a second own address: the next sync adds its leg to tx 1
        _address(conn, ws, account, chain="bitcoin", address=SECOND, address_masked="bc1q…zqrl")

    seen, go = threading.Event(), threading.Event()
    real = sync_mod._live_group

    def held(ctx, start, tx_hash):  # the sync pauses right after reading the live group
        live = real(ctx, start, tx_hash)
        if live is not None and not seen.is_set():
            seen.set()
            go.wait(20)
        return live

    monkeypatch.setattr(sync_mod, "_live_group", held)
    syncing = threading.Thread(target=sync)
    syncing.start()
    assert seen.wait(30)
    answered: dict[str, int] = {}

    def answer():
        # no typed cost: by the time it runs the group holds the second address's leg too
        response = client_a.post(f"/api/groups/{card['id']}/answer", json={"answer": "gift"})
        answered["status"] = response.status_code

    answering = threading.Thread(target=answer)
    answering.start()
    answering.join(1.0)
    assert answering.is_alive()  # the answer waits for the sync's write
    go.set()
    syncing.join(30)
    answering.join(30)
    assert answered["status"] == 200
    orphans = _rows(
        dbname,
        "SELECT m.id FROM asset_movement m JOIN movement_group g ON g.id = m.group_id"
        " WHERE m.account_id = %s AND m.superseded_by IS NULL AND g.superseded_by IS NOT NULL",
        account,
    )
    assert orphans == []
