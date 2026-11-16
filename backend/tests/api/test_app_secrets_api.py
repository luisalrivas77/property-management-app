"""Provider keys and settings typed in the app over HTTP (docs/phases/phase-6.md D260): the
card's list, PUT and DELETE, refusals that never quote the value, isolation between
workspaces, no value in any answer or log line, the wallet sync queued after a chain key, and
the wallet page reading the typed key (and saying when it cannot). Invented values only."""

import json
import logging

import pytest
from api_support import TOKEN_A, FakeResolver, client_for, make_settings, ok
from db.db_support import _admin
from db.test_phase7_migration import _address, _wallet

from hausbuch.api.app import create_app
from hausbuch.rules import errors as E

pytestmark = pytest.mark.db

KEY = "typed-etherscan-KEY-31337"
PASSWORD = "abcd efgh ijkl mnop"
OTHER_KEY = bytes(range(101, 133))  # a rotated session key


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG)

    def text() -> str:
        return "\n".join(
            f"{r.getMessage()} {r.__dict__!r}"
            for r in caplog.records
            if r.name.split(".")[0] not in ("httpx", "httpx2")
        )

    return text


def _states(client) -> dict[str, dict]:
    return {s["name"]: s for s in ok(client.get("/api/app-secrets"))["secrets"]}


def _jobs(dbname, ws) -> list:
    with _admin(dbname) as conn:
        return conn.execute(
            "SELECT args FROM procrastinate_jobs WHERE task_name = 'sync_wallet'"
            " AND args->>'workspace_id' = %s",
            (str(ws),),
        ).fetchall()


def test_a_key_is_set_listed_by_its_source_and_deleted_never_returned(client_a, client_b, logs):
    before = _states(client_a)
    assert list(before)[:2] == ["anthropic_api_key", "etherscan_key"]
    assert before["etherscan_key"]["source"] == "none"
    assert before["etherscan_key"]["group"] == "chains"
    assert before["etherscan_key"]["variable"] == "HAUSBUCH_ETHERSCAN_KEY"
    response = client_a.put("/api/app-secrets/etherscan_key", json={"value": KEY})
    stored = ok(response)
    assert (stored["name"], stored["source"], stored["value"]) == ("etherscan_key", "app", None)
    assert KEY not in response.text
    after = client_a.get("/api/app-secrets")
    assert KEY not in after.text
    assert _states(client_a)["etherscan_key"]["source"] == "app"
    assert _states(client_b)["etherscan_key"]["source"] == "none"  # workspace B sees nothing
    assert client_b.delete("/api/app-secrets/etherscan_key").status_code == 404
    assert client_a.delete("/api/app-secrets/etherscan_key").status_code == 204
    assert _states(client_a)["etherscan_key"]["source"] == "none"
    assert client_a.delete("/api/app-secrets/etherscan_key").status_code == 404
    assert KEY not in logs()


def test_a_setting_that_is_not_secret_is_shown_back_a_password_never(client_a, logs):
    ok(client_a.put("/api/app-secrets/inbox_label", json={"value": "Hausbuch Belege"}))
    response = client_a.put("/api/app-secrets/inbox_imap_password", json={"value": PASSWORD})
    assert ok(response)["value"] is None and PASSWORD not in response.text
    states = _states(client_a)
    assert states["inbox_label"]["value"] == "Hausbuch Belege"
    assert states["inbox_label"]["secret"] is False
    assert states["inbox_imap_password"]["secret"] is True
    assert PASSWORD not in json.dumps(states) and PASSWORD not in logs()


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("etherscan_key", "two words QUOTEME", E.APP_SECRET_INVALID),
        ("etherscan_key", "QUOTEME" * 74, E.APP_SECRET_INVALID),
        ("solana_rpc_url", "solana-QUOTEME.rpc.example/path", E.APP_SECRET_URL_INVALID),
        ("mail_from", "no-at-QUOTEME", E.APP_SECRET_INVALID),
    ],
    ids=["blank", "too_long", "url", "mail"],
)
def test_a_refused_value_is_never_quoted(client_a, logs, name, value, code):
    response = client_a.put(f"/api/app-secrets/{name}", json={"value": value})
    assert (response.status_code, response.json()["code"]) == (422, code)
    assert "QUOTEME" not in response.text and value not in logs()


@pytest.mark.parametrize(
    "body",
    [{}, {"value": 42}, {"value": "k", "extra": "leak-secret"}],
    ids=["missing", "not_a_string", "extra"],
)
def test_a_body_outside_its_schema_is_refused_without_echo(client_a, body):
    response = client_a.put("/api/app-secrets/etherscan_key", json=body)
    assert response.status_code == 422 and "leak-secret" not in response.text


def test_a_name_off_the_allowlist_is_refused(client_a):
    for name in ("database_url", "session_key", "iban_hmac_key"):
        assert client_a.put(f"/api/app-secrets/{name}", json={"value": "x"}).status_code == 422


def test_a_chain_key_queues_the_wallets_that_were_waiting_for_it(
    client_a, fresh_workspaces, dbname
):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        ethereum = _wallet(conn, ws)
        _address(conn, ws, ethereum)  # an Ethereum address, read through Etherscan
        bitcoin = _wallet(conn, ws, label="Bitcoin")
        _address(
            conn,
            ws,
            bitcoin,
            chain="bitcoin",
            address="bc1q" + "x" * 38,
            address_masked="bc1q…xxxx",
        )
    (status,) = ok(client_a.get(f"/api/wallets/{ethereum}/state"))["statuses"]
    assert status["state"] == "key_missing"
    assert "Einstellungen › Zugangsschlüssel" in status["todo"]
    ok(client_a.put("/api/app-secrets/coingecko_key", json={"value": "cg-key-1"}))
    assert _jobs(dbname, ws) == []  # no chain reads the CoinGecko key
    ok(client_a.put("/api/app-secrets/etherscan_key", json={"value": KEY}))
    (args,) = (row[0] for row in _jobs(dbname, ws))
    assert args == {"workspace_id": str(ws), "account_id": str(ethereum)}
    assert KEY not in json.dumps(args)
    (status,) = ok(client_a.get(f"/api/wallets/{ethereum}/state"))["statuses"]
    assert status["state"] != "key_missing"


def test_a_rotated_session_key_makes_the_wallet_say_type_it_again(
    client_a, fresh_workspaces, dbname, app_engine, blob_store, sessions, tmp_path
):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        _address(conn, ws, account)
    ok(client_a.put("/api/app-secrets/etherscan_key", json={"value": KEY}))
    rotated = make_settings(
        tmp_path, session_key=OTHER_KEY, etherscan_key=None
    )  # the container has no key either: nothing may stand in
    app = create_app(rotated, app_engine, blob_store, FakeResolver(sessions), worker=None)
    client = client_for(app, TOKEN_A)
    try:
        assert _states(client)["etherscan_key"]["source"] == "unreadable"
        (status,) = ok(client.get(f"/api/wallets/{account}/state"))["statuses"]
        assert status["state"] == "key_unreadable"
        assert "Einstellungen › Zugangsschlüssel" in status["todo"]
        inbox = client.get("/api/inbox")
        assert inbox.status_code == 200 and KEY not in inbox.text
    finally:
        client.close()


def test_the_settings_page_and_the_inbox_address_read_the_typed_values(client_a, client_b):
    assert ok(client_a.get("/api/workspace/settings"))["reminder_emails_available"] is False
    for name, value in (
        ("tem_secret_key", "typed-tem-key-1"),
        ("tem_project_id", "7c1a2c56-58c1-4a34-9fd3-0b2d1d5f1e01"),
        ("mail_from", "erinnerung@typed.example"),
    ):
        ok(client_a.put(f"/api/app-secrets/{name}", json={"value": value}))
    assert ok(client_a.get("/api/workspace/settings"))["reminder_emails_available"] is True
    assert ok(client_b.get("/api/workspace/settings"))["reminder_emails_available"] is False
    assert ok(client_a.get("/api/inbound-mail/address"))["configured"] is False
    for name, value in (
        ("inbox_imap_user", "haushalt@typed.example"),
        ("inbox_imap_password", PASSWORD),
        ("inbox_address", "haushalt+t0k3n@typed.example"),
        ("inbox_label", "Hausbuch"),
    ):
        ok(client_a.put(f"/api/app-secrets/{name}", json={"value": value}))
    address = client_a.get("/api/inbound-mail/address")
    assert ok(address) == {
        "configured": True,
        "address": "haushalt+t0k3n@typed.example",
        "label": "Hausbuch",
    }
    assert PASSWORD not in address.text


def test_the_ai_button_opens_with_a_typed_key_alone(
    app_engine, sessions, tmp_path, blob_store, client_a
):
    settings = make_settings(tmp_path, ai_enabled=True, anthropic_api_key=None)
    app = create_app(settings, app_engine, blob_store, FakeResolver(sessions), worker=None)
    client = client_for(app, TOKEN_A)
    try:
        refused = ok(client.post("/api/inbox/ai-retry"), 422)
        assert refused["code"] == E.AI_DISABLED
        ok(client.put("/api/app-secrets/anthropic_api_key", json={"value": "sk-ant-typed-1"}))
        assert ok(client.post("/api/inbox/ai-retry"))["queued"] == 0  # nothing unmatched yet
    finally:
        client.close()
