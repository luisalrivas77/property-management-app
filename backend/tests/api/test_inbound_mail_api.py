"""Email-in over HTTP (docs/phases/phase-5.md §10, §11): the settings page's address, and the
list of attempts, per workspace. Synthetic addresses only."""

import hashlib
from datetime import UTC, datetime

import pytest
from api_support import TOKEN_A, FakeResolver, client_for, make_settings, ok
from sqlalchemy import insert

from hausbuch.api.app import create_app
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import inbound_mail as mail_table

pytestmark = pytest.mark.db

TOKEN_ADDRESS = "haushalt+x7k2q9@example.com"


def test_the_address_is_null_until_email_in_is_set_up(client_a):
    assert ok(client_a.get("/api/inbound-mail/address")) == {
        "configured": False,
        "address": None,
        "label": None,
    }


def test_the_address_and_label_but_never_the_login(app_engine, blob_store, sessions, tmp_path):
    settings = make_settings(
        tmp_path,
        inbox_imap_user="haushalt@example.com",
        inbox_imap_password="app-password-test-0000",
        inbox_address=TOKEN_ADDRESS,
        inbox_label="Hausbuch",
    )
    app = create_app(settings, app_engine, blob_store, FakeResolver(sessions), worker=None)
    client = client_for(app, TOKEN_A)
    try:
        response = client.get("/api/inbound-mail/address")
    finally:
        client.close()
    assert ok(response) == {"configured": True, "address": TOKEN_ADDRESS, "label": "Hausbuch"}
    assert "app-password" not in response.text and "haushalt@example.com" not in response.text


def test_the_attempts_are_listed_without_the_hash_and_per_workspace(
    client_a, client_b, auth_a, app_engine
):
    with workspace_transaction(app_engine, auth_a.workspace_id) as conn:
        conn.execute(
            insert(mail_table).values(
                workspace_id=auth_a.workspace_id,
                message_id_hash=hashlib.sha256(b"<a@example.org>").digest(),
                attempt=1,
                decision="rejected",
                code="email.sender_not_allowed",
                recorded_at=datetime.now(UTC),
            )
        )

    [row] = ok(client_a.get("/api/inbound-mail"))
    assert (row["decision"], row["code"], row["attempt"]) == (
        "rejected",
        "email.sender_not_allowed",
        1,
    )
    assert row["documents"] == [] and "message_id_hash" not in row
    assert ok(client_b.get("/api/inbound-mail")) == []
