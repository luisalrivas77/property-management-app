"""The FinTS sync over HTTP (docs/phases/phase-5.md §10): the connection's routes, a start's
refusals and its 202, the run list and one run, isolation between workspaces. The job itself
is covered in tests/db/test_jobs_bank_sync.py. Synthetic values only."""

from datetime import UTC, datetime

import pytest
from api_support import (
    TOKEN_A,
    TOKEN_B,
    FakeResolver,
    auth_for,
    client_for,
    create,
    make_settings,
    ok,
)
from db.db_support import _admin

from hausbuch.api.app import create_app
from hausbuch.rules import errors as E

pytestmark = pytest.mark.db

ACCOUNT = {
    "label": "Mietkonto",
    "bank": "Sparkasse",
    "iban": "DE50100000000000000001",
    "opening_balance": "0.00",
    "opening_balance_on": "2026-03-31",
}
LOGIN = "loginname-xyz"
PIN = "PIN-4711-secret"
PRODUCT_ID = "PRODUCTID0123456789ABCDEF"


def _connection(account_id: str, **overrides) -> dict:
    return {
        "account_id": account_id,
        "bank_code": "10000000",
        "fints_url": "https://fints.example.org/fints",
        "login_name": LOGIN,
    } | overrides


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so a run's created_by (FK to app_user) is real."""
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


@pytest.fixture
def configured(app_engine, blob_store, sessions, tmp_path):
    """A client of an app whose Settings carry a PIN and a product id."""
    settings = make_settings(tmp_path, fints_pin=PIN, fints_product_id=PRODUCT_ID)
    app = create_app(settings, app_engine, blob_store, FakeResolver(sessions), worker=None)
    client = client_for(app, TOKEN_A)
    try:
        yield client
    finally:
        client.close()


def test_the_connection_is_created_read_and_patched_with_the_login_masked(client_a, client_b):
    account = create(client_a, "/api/accounts", ACCOUNT)
    path = f"/api/accounts/{account['id']}/bank-connection"
    assert client_a.get(path).status_code == 404

    created = create(client_a, path, _connection(account["id"]))
    assert created["login_masked"] == "lo***yz" and created["state_present"] is False
    assert LOGIN not in str(created) and "login_name" not in created
    assert ok(client_a.get(path)) == created
    assert ok(client_a.get(f"/api/accounts/{account['id']}"))["source"] == "fints"

    patched = ok(client_a.patch(path, json={"auto_sync": True}))
    assert patched["auto_sync"] is True and patched["fetch_statements"] is False
    patched = ok(client_a.patch(path, json={"fetch_statements": True}))
    assert patched["fetch_statements"] is True and patched["auto_sync"] is True
    assert client_a.patch(path, json={"fetch_statements": None}).status_code == 422
    assert client_a.post(path, json=_connection(account["id"])).status_code == 409
    assert client_b.get(path).status_code == 404


def test_the_connection_refuses_http_another_account_id_and_a_null(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    path = f"/api/accounts/{account['id']}/bank-connection"
    http = client_a.post(path, json=_connection(account["id"], fints_url="http://x.example/f"))
    assert http.status_code == 422 and "http://x.example" not in http.text
    other = client_a.post(path, json=_connection("00000000-0000-0000-0000-000000000001"))
    assert other.status_code == 422
    create(client_a, path, _connection(account["id"]))
    assert client_a.patch(path, json={"login_name": None}).status_code == 422


def test_a_start_without_the_server_secrets_is_not_configured(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    create(client_a, f"/api/accounts/{account['id']}/bank-connection", _connection(account["id"]))
    response = client_a.post(f"/api/accounts/{account['id']}/syncs")
    assert response.status_code == 422
    assert response.json()["code"] == E.BANK_NOT_CONFIGURED


def test_a_start_is_202_with_the_run_and_a_second_is_refused(configured, client_b):
    account = create(configured, "/api/accounts", ACCOUNT)
    create(configured, f"/api/accounts/{account['id']}/bank-connection", _connection(account["id"]))
    response = configured.post(f"/api/accounts/{account['id']}/syncs")
    assert response.status_code == 202
    run = response.json()
    assert (run["status"], run["trigger"], run["requested_from"]) == (
        "queued",
        "manual",
        "2026-04-01",
    )
    again = configured.post(f"/api/accounts/{account['id']}/syncs")
    assert (again.status_code, again.json()["code"]) == (422, E.BANK_SYNC_RUNNING)

    assert run["created_by"] is not None
    assert ok(configured.get(f"/api/accounts/{account['id']}/syncs")) == [run]
    assert ok(configured.get(f"/api/syncs/{run['id']}")) == run
    assert client_b.get(f"/api/syncs/{run['id']}").status_code == 404
    assert client_b.get(f"/api/accounts/{account['id']}/syncs").status_code == 404
    assert PIN not in response.text and PRODUCT_ID not in response.text


def test_a_start_of_an_account_without_a_connection_is_404(configured):
    account = create(configured, "/api/accounts", ACCOUNT)
    assert configured.post(f"/api/accounts/{account['id']}/syncs").status_code == 404


def test_the_fetched_statements_are_listed_newest_first_per_workspace(client_a, client_b, dbname):
    """D154: GET /accounts/{id}/bank-statements, newest first by year and number; the rows are
    written directly (the FinTS run is covered in tests/db/test_jobs_bank_sync.py)."""
    account = create(client_a, "/api/accounts", ACCOUNT)
    path = f"/api/accounts/{account['id']}/bank-statements"
    assert ok(client_a.get(path)) == []
    connection = create(
        client_a, f"/api/accounts/{account['id']}/bank-connection", _connection(account["id"])
    )
    with _admin(dbname) as conn:
        ws = conn.execute(
            "SELECT workspace_id FROM account WHERE id = %s", (account["id"],)
        ).fetchone()[0]
        (sync_id,) = conn.execute(
            "INSERT INTO bank_sync (workspace_id, account_id, connection_id, trigger)"
            " VALUES (%s, %s, %s, 'manual') RETURNING id",
            (ws, account["id"], connection["id"]),
        ).fetchone()
        for year, number in ((2025, 12), (2026, 1), (2026, 2)):
            digest = bytes([number]) * 32
            conn.execute(
                "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type,"
                " storage_key) VALUES (%s, %s, 11, 'application/pdf', %s)",
                (ws, digest, f"{ws}/sha256/{digest.hex()}"),
            )
            (document_id,) = conn.execute(
                "INSERT INTO document (workspace_id, file_sha256, source, type)"
                " VALUES (%s, %s, 'bank_sync', 'statement') RETURNING id",
                (ws, digest),
            ).fetchone()
            conn.execute(
                "INSERT INTO bank_statement (workspace_id, account_id, year, number,"
                " document_id, sync_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (ws, account["id"], year, number, document_id, sync_id),
            )
    listed = ok(client_a.get(path))
    assert [(s["year"], s["number"]) for s in listed] == [(2026, 2), (2026, 1), (2025, 12)]
    assert {s["sync_id"] for s in listed} == {str(sync_id)}
    assert set(listed[0]) == {
        "id",
        "account_id",
        "year",
        "number",
        "document_id",
        "sync_id",
        "fetched_at",
    }
    assert client_b.get(path).status_code == 404


def test_a_permission_refusal_is_on_the_connection_and_in_zu_pruefen(client_a, client_b, dbname):
    """14 Nov 2026: the bank refused the PDF statement fetch with 9370 (keine
    Auftragsberechtigung). The event is written directly (the FinTS run is covered in
    tests/db/test_jobs_bank_sync.py); the connection read carries it and Zu prüfen shows one
    card, while the switch „Kontoauszüge als PDF abrufen“ is on."""
    account = create(client_a, "/api/accounts", ACCOUNT)
    path = f"/api/accounts/{account['id']}/bank-connection"
    connection = create(client_a, path, _connection(account["id"]))
    assert connection["statements_refusal"] is None
    with _admin(dbname) as conn:
        ws = conn.execute(
            "SELECT workspace_id FROM account WHERE id = %s", (account["id"],)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO event (workspace_id, type, entity_type, entity_id, recorded_at,"
            " actor_type, actor_id, correlation_id, payload) VALUES (%s,"
            " 'account.statements_refused', 'account', %s, '2026-11-14T06:30:02+00:00',"
            " 'system', 'system', gen_random_uuid(), jsonb_build_object('connection_id', %s::text,"
            " 'sync_id', gen_random_uuid()::text, 'code', '9370', 'year', 2026, 'number', 1))",
            (ws, account["id"], connection["id"]),
        )

    def cards(client):
        items = ok(client.get("/api/inbox"))
        return [i for i in items if i["kind"] == "bank_statements_refused"]

    assert ok(client_a.get(path))["statements_refusal"] is None  # the switch is off
    assert cards(client_a) == []
    patched = ok(client_a.patch(path, json={"fetch_statements": True}))
    refusal = patched["statements_refusal"]
    assert datetime.fromisoformat(refusal.pop("at")) == datetime(2026, 11, 14, 6, 30, 2, tzinfo=UTC)
    assert refusal == {"code": "9370", "year": 2026, "number": 1}
    (card,) = cards(client_a)
    assert card["account_id"] == account["id"]
    assert "Code 9370: Signaturberechtigung reicht nicht aus" in card["reasoning"]
    assert cards(client_b) == []
    # Switching the fetch off is the person's answer to the refusal: no notice, no card.
    assert ok(client_a.patch(path, json={"fetch_statements": False}))["statements_refusal"] is None
    assert ok(client_a.get(path))["statements_refusal"] is None
    assert cards(client_a) == []
