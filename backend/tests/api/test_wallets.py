"""A wallet and its addresses through the API (docs/phases/phase-7.md D179, §4, §13; step 1).

Every address here is a published test vector (BIP-173, EIP-55) or one of the invented
addresses of tests/fixtures/phase7/chains.yaml, never a household's.
"""

import base64
import json
import logging
from typing import Any

import pytest
from api_support import TEST_IBAN_KEY, create, ok
from db.db_support import _admin

from hausbuch.rules.chain.address import address_hmac

pytestmark = pytest.mark.db

EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55's first example
EVM_STORED = EVM.lower()
EVM_OTHER = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"  # EIP-55's second example
SOL = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"  # chains.yaml sol_generated
BTC = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"  # BIP-173's example

WALLET = {
    "label": "Hardware-Wallet",
    "bank": "Testwallet",
    "kind": "wallet",
    "provider": "Testwallet",
    "opening_balance": "0.00",
    "opening_balance_on": "2026-03-31",
}


def _wallet(client, label="Hardware-Wallet") -> str:
    return create(client, "/api/accounts", WALLET | {"label": label})["id"]


def _add(client, wallet_id, chain, address, status=201, **extra):
    body = {"chain": chain, "address": address} | extra
    return ok(client.post(f"/api/wallets/{wallet_id}/addresses", json=body), status)


def _events(client, entity_id, type_=None):
    events = ok(client.get("/api/events", params={"entity_id": entity_id}))
    return [e for e in events if type_ is None or e["type"] == type_]


def test_a_wallet_page_lists_its_addresses_masked(client_a, dbname):
    wallet_id = _wallet(client_a)
    created = _add(
        client_a, wallet_id, "ethereum", EVM, label="Konto 1", also_on=["bsc", "arbitrum"]
    )
    assert [a["chain"] for a in created] == ["ethereum", "arbitrum", "bsc"]
    assert {a["address_masked"] for a in created} == {"0x5a…eaed"}
    assert {a["label"] for a in created} == {"Konto 1"}
    _add(client_a, wallet_id, "solana", SOL)
    _add(client_a, wallet_id, "bitcoin", BTC.upper())

    page = ok(client_a.get(f"/api/wallets/{wallet_id}"))
    assert page["account"]["id"] == wallet_id
    assert page["account"]["kind"] == "wallet"
    assert [
        (a["chain"], a["address_masked"], a["kind"], a["active"]) for a in page["addresses"]
    ] == [
        ("bitcoin", "bc1q…f3t4", "receive", True),
        ("ethereum", "0x5a…eaed", "receive", True),
        ("arbitrum", "0x5a…eaed", "receive", True),
        ("bsc", "0x5a…eaed", "receive", True),
        ("solana", "BU8o…jsJP", "receive", True),
    ]
    assert all(a["last_sync"] is None for a in page["addresses"])
    assert page["balances"] == [] and page["syncs"] == []

    added = _events(client_a, wallet_id, "wallet.address_added")
    assert len(added) == 5
    assert {e["entity_type"] for e in added} == {"wallet"}
    assert sorted(e["payload"]["chain"] for e in added) == [
        "arbitrum",
        "bitcoin",
        "bsc",
        "ethereum",
        "solana",
    ]

    # Stored: the normalised address (EVM and bech32 lower case) and the keyed HMAC.
    with _admin(dbname) as conn:
        rows = conn.execute(
            "SELECT chain, address, address_hmac FROM wallet_address WHERE account_id = %s",
            (wallet_id,),
        ).fetchall()
    stored = {chain: (address, bytes(digest)) for chain, address, digest in rows}
    assert stored["ethereum"] == (EVM_STORED, address_hmac(EVM_STORED, TEST_IBAN_KEY))
    assert stored["bitcoin"][0] == BTC
    assert stored["solana"][0] == SOL


def test_the_ethereum_shortcut_skips_a_chain_already_read(client_a):
    wallet_id = _wallet(client_a)
    _add(client_a, wallet_id, "arbitrum", EVM_STORED)
    created = _add(client_a, wallet_id, "ethereum", EVM, also_on=["arbitrum", "bsc"])
    assert [a["chain"] for a in created] == ["ethereum", "bsc"]
    # Every chain asked for is registered on this wallet already.
    again = _add(client_a, wallet_id, "bsc", EVM, 422, also_on=["ethereum"])
    assert again["code"] == "wallet.address_taken"


def test_also_on_takes_evm_chains_for_an_evm_address_only(client_a):
    wallet_id = _wallet(client_a)
    refused = _add(client_a, wallet_id, "solana", SOL, 422, also_on=["ethereum"])
    assert refused["code"] == "wallet.address_invalid"
    refused = _add(client_a, wallet_id, "ethereum", EVM, 422, also_on=["solana"])
    assert refused["code"] == "wallet.address_invalid"
    assert ok(client_a.get(f"/api/wallets/{wallet_id}"))["addresses"] == []


def test_an_address_on_another_own_wallet_is_taken(client_a):
    first = _wallet(client_a, "Hardware-Wallet")
    second = _wallet(client_a, "Solana-Wallet")
    _add(client_a, first, "ethereum", EVM)
    for chain in ("ethereum", "arbitrum"):  # the HMAC is the address's, on any chain
        response = client_a.post(
            f"/api/wallets/{second}/addresses", json={"chain": chain, "address": EVM_STORED}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "wallet.address_taken"
        assert EVM_STORED not in response.text.lower()
    assert ok(client_a.get(f"/api/wallets/{second}"))["addresses"] == []
    # The same address on a second chain of its own wallet is no conflict.
    assert [a["chain"] for a in _add(client_a, first, "arbitrum", EVM_STORED)] == ["arbitrum"]
    # Another workspace may hold the same address: the HMAC is unique per workspace.


def test_an_invalid_paste_is_refused_without_repeating_it(client_a):
    wallet_id = _wallet(client_a)
    wrong_checksum = "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    for chain, text in (
        ("ethereum", wrong_checksum),
        ("bitcoin", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5"),
        ("solana", SOL[:-2] + "0P"),
        ("solana", EVM_OTHER),  # an EVM address pasted under Solana
    ):
        response = client_a.post(
            f"/api/wallets/{wallet_id}/addresses", json={"chain": chain, "address": text}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "wallet.address_invalid"
        assert text not in response.text
    for text in ("0x5aaeb6053f", "1" * 129):  # the body's length limits (pydantic)
        response = client_a.post(
            f"/api/wallets/{wallet_id}/addresses", json={"chain": "solana", "address": text}
        )
        assert response.status_code == 422
        assert text not in response.text


def test_relabel_retire_and_reactivate(client_a):
    wallet_id = _wallet(client_a)
    (address,) = _add(client_a, wallet_id, "solana", SOL)
    url = f"/api/wallets/{wallet_id}/addresses/{address['id']}"
    relabelled = ok(client_a.patch(url, json={"label": "Alte Wallet"}))
    assert relabelled["label"] == "Alte Wallet" and relabelled["active"] is True
    retired = ok(client_a.patch(url, json={"active": False}))
    assert retired["active"] is False and retired["label"] == "Alte Wallet"
    # The same values again change nothing and record nothing.
    assert ok(client_a.patch(url, json={"active": False})) == retired
    cleared = ok(client_a.patch(url, json={"active": True, "label": None}))
    assert cleared["active"] is True and cleared["label"] is None

    updated = _events(client_a, wallet_id, "wallet.address_updated")
    assert [set(e["payload"]["changes"]) for e in updated] == [
        {"label"},
        {"active"},
        {"active", "label"},
    ]
    assert all(e["payload"]["address_masked"] == "BU8o…jsJP" for e in updated)
    # A retired address stays on the page, after the active ones.
    (other,) = _add(client_a, wallet_id, "bitcoin", BTC)
    ok(client_a.patch(url, json={"active": False}))
    page = ok(client_a.get(f"/api/wallets/{wallet_id}"))
    assert [(a["id"], a["active"]) for a in page["addresses"]] == [
        (other["id"], True),
        (address["id"], False),
    ]
    # Only label and active change.
    assert client_a.patch(url, json={"chain": "bitcoin"}).status_code == 422


def test_the_reveal_is_audited_and_never_cached(client_a):
    wallet_id = _wallet(client_a)
    (address,) = _add(client_a, wallet_id, "ethereum", EVM)
    response = client_a.get(f"/api/wallets/{wallet_id}/addresses/{address['id']}/plain")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"id": address["id"], "chain": "ethereum", "address": EVM_STORED}
    (revealed,) = _events(client_a, wallet_id, "wallet.address_revealed")
    assert revealed["payload"] == {
        "address_id": address["id"],
        "chain": "ethereum",
        "address_masked": "0x5a…eaed",
    }


def test_another_wallet_or_account_is_not_found(client_a):
    first = _wallet(client_a)
    second = _wallet(client_a, "Solana-Wallet")
    (address,) = _add(client_a, first, "solana", SOL)
    elsewhere = f"/api/wallets/{second}/addresses/{address['id']}"
    assert client_a.get(elsewhere + "/plain").status_code == 404
    assert client_a.patch(elsewhere, json={"active": False}).status_code == 404
    bank = create(
        client_a,
        "/api/accounts",
        {
            "label": "Girokonto",
            "bank": "Sparkasse",
            "iban": "DE50100000000000000001",
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
            "purpose": "private",
        },
    )
    assert client_a.get(f"/api/wallets/{bank['id']}").status_code == 404
    response = client_a.post(
        f"/api/wallets/{bank['id']}/addresses", json={"chain": "bitcoin", "address": BTC}
    )
    assert response.status_code == 404
    assert _events(client_a, first, "wallet.address_revealed") == []


def test_another_workspace_cannot_read_or_reveal(client_a, client_b):
    wallet_id = _wallet(client_a)
    (address,) = _add(client_a, wallet_id, "bitcoin", BTC)
    base = f"/api/wallets/{wallet_id}"
    assert client_b.get(base).status_code == 404
    assert client_b.get(f"{base}/addresses/{address['id']}/plain").status_code == 404
    assert (
        client_b.patch(f"{base}/addresses/{address['id']}", json={"label": "x"}).status_code == 404
    )
    response = client_b.post(f"{base}/addresses", json={"chain": "solana", "address": SOL})
    assert response.status_code == 404
    assert _events(client_a, wallet_id, "wallet.address_revealed") == []
    assert ok(client_a.get(base))["addresses"][0]["label"] is None
    # The second workspace may register the same address on its own wallet.
    own = _wallet(client_b)
    assert [a["chain"] for a in _add(client_b, own, "bitcoin", BTC)] == ["bitcoin"]


def _strings(value: Any) -> list[str]:
    """Every string in a JSON value, keys included: the walk over every dict and list."""
    if isinstance(value, dict):
        return [s for key, item in value.items() for s in [key, *_strings(item)]]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return [value] if isinstance(value, str) else []


def test_no_address_or_hmac_leaves_except_through_the_reveal(client_a, caplog):
    caplog.set_level(logging.DEBUG)
    wallet_id = _wallet(client_a)
    other_id = _wallet(client_a, "Solana-Wallet")
    bodies: list[Any] = []

    def call(method, url, **kwargs):
        response = client_a.request(method, url, **kwargs)
        bodies.append(response.json() if response.content else None)
        return response

    added = call(
        "POST",
        f"/api/wallets/{wallet_id}/addresses",
        json={"chain": "ethereum", "address": EVM, "label": "Konto 1", "also_on": ["bsc"]},
    ).json()
    call("POST", f"/api/wallets/{wallet_id}/addresses", json={"chain": "solana", "address": SOL})
    call("POST", f"/api/wallets/{wallet_id}/addresses", json={"chain": "bitcoin", "address": BTC})
    # The refusals: taken on another wallet, already here, a wrong checksum, too short.
    call("POST", f"/api/wallets/{other_id}/addresses", json={"chain": "arbitrum", "address": EVM})
    call("POST", f"/api/wallets/{wallet_id}/addresses", json={"chain": "solana", "address": SOL})
    call(
        "POST",
        f"/api/wallets/{wallet_id}/addresses",
        json={"chain": "ethereum", "address": EVM[:-1] + "D"},
    )
    call(
        "POST", f"/api/wallets/{wallet_id}/addresses", json={"chain": "solana", "address": SOL[:15]}
    )
    call(
        "PATCH",
        f"/api/wallets/{wallet_id}/addresses/{added[0]['id']}",
        json={"label": "Neu", "active": False},
    )
    plain = client_a.get(f"/api/wallets/{wallet_id}/addresses/{added[0]['id']}/plain")
    assert plain.json()["address"] == EVM_STORED  # the one answer that carries it

    reads = [
        ok(client_a.get(f"/api/wallets/{wallet_id}")),
        ok(client_a.get(f"/api/wallets/{other_id}")),
        ok(client_a.get("/api/accounts")),
        ok(client_a.get(f"/api/accounts/{wallet_id}")),
        _events(client_a, wallet_id),
        _events(client_a, other_id),
        ok(client_a.get("/api/events")),
    ]
    events = _events(client_a, wallet_id)
    assert {e["type"] for e in events} >= {
        "wallet.address_added",
        "wallet.address_updated",
        "wallet.address_revealed",
    }

    secrets = {EVM, EVM_STORED, EVM.upper()[2:], SOL, BTC, BTC.upper()}
    for normalised in (EVM_STORED, SOL, BTC):
        digest = address_hmac(normalised, TEST_IBAN_KEY)
        secrets |= {
            digest.hex(),
            "\\x" + digest.hex(),
            base64.b64encode(digest).decode(),
            base64.urlsafe_b64encode(digest).decode(),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
        }
    strings = _strings(bodies) + _strings(reads)
    assert strings  # the walk saw the responses
    assert caplog.records  # and the request log saw the calls
    for secret in secrets:
        assert not [s for s in strings if secret.lower() in s.lower()], secret
        assert secret.lower() not in json.dumps([bodies, reads], ensure_ascii=False).lower()
        assert secret.lower() not in caplog.text.lower()
        for record in caplog.records:
            assert secret.lower() not in json.dumps(record.__dict__, default=str).lower()
