"""A bank PIN typed into the app and the login name, both stored encrypted, against Postgres
(docs/phases/phase-6.md D196): services.bank_credentials and its wiring into the connection
commands and the sync job, as hausbuch_app under RLS, with the scripted fake FinTS client.

Reuses test_jobs_bank_sync's Bank helper. Every PIN, login and IBAN is invented."""

import dataclasses
import logging
from datetime import timedelta

import pytest
from db.test_jobs_bank_sync import (  # the helpers only, never a test_* name
    LOGIN,
    PIN,
    PRODUCT_ID,
    T0,
    T1,
    Bank,
    _good,
    _settings,
)
from fints_fake import FAKE_STATE
from pydantic import SecretStr
from sqlalchemy import select, text, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_credential as credential_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import event as event_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.bank_sync import BankConnectionPatch, CredentialSource
from hausbuch.schemas.events import EventType
from hausbuch.services import bank_credentials as credentials
from hausbuch.services import bank_sync as service

pytestmark = pytest.mark.db

APP_PIN = "app-PIN-90210-secret"
NEW_LOGIN = "other-login-name-77"
OTHER_KEY = bytes(range(101, 133))  # a rotated session key
SECRETS = (PIN, APP_PIN, PRODUCT_ID, LOGIN, NEW_LOGIN)


@pytest.fixture
def bank(run, read, app_engine, blob_store, tmp_path, fresh_workspaces):
    ws, _ = fresh_workspaces
    return Bank(run, read, app_engine, blob_store, tmp_path, ws)


def _set_pin(bank, pin=APP_PIN, slot="primary", now=T0):
    return bank.run(
        bank.ws, credentials.set_pin, slot, SecretStr(pin), settings=bank.settings, now=now
    )


def _delete_pin(bank, slot="primary"):
    return bank.run(bank.ws, credentials.delete_pin, slot, now=T0)


def _connection(bank, settings=None):
    return bank.read(
        bank.ws, service.get_connection, bank.account.id, settings=settings or bank.settings
    )


def _sync(bank, *, at=T1, script=None):
    """One manual start and its job; the fake client of this run."""
    sync = bank.start(now=at - timedelta(seconds=5))
    outcome = bank.job(sync.id, script or _good(approval_polls=0), at=at)
    return sync, outcome


def _make_readable(bank) -> None:
    """The connection as migration 0039 found it: the login readable, nothing encrypted."""
    with workspace_transaction(bank.engine, bank.ws) as conn:
        conn.execute(
            update(connection_table)
            .where(connection_table.c.account_id == bank.account.id)
            .values(
                login_name=LOGIN, login_nonce=None, login_ciphertext=None, login_key_version=None
            )
        )


def _all_text(bank) -> bytes:
    """Every row of the tables a secret could leak into, as bytes."""
    with workspace_transaction(bank.engine, bank.ws) as conn:
        rows = [
            *conn.execute(select(credential_table)).all(),
            *conn.execute(select(connection_table)).all(),
            *conn.execute(select(sync_table)).all(),
            *conn.execute(select(event_table)).all(),
        ]
        jobs = conn.execute(text("SELECT args FROM procrastinate_jobs")).scalars().all()
    out = bytearray(repr([tuple(r) for r in rows]).encode() + repr(jobs).encode())
    for row in rows:
        for value in row:
            if isinstance(value, bytes | memoryview):
                out += bytes(value)
    return bytes(out)


# --- The PIN typed into the app --------------------------------------------------------------


def test_a_pin_is_stored_encrypted_and_its_event_names_the_slot_only(bank):
    stored = _set_pin(bank)
    assert stored.slot == "primary" and stored.set_at == T0
    (row,) = bank.rows(credential_table)
    assert APP_PIN.encode() not in bytes(row.ciphertext)
    assert len(bytes(row.nonce)) == 12 and row.key_version == credentials.KEY_VERSION
    (event,) = [
        e for e in bank.rows(event_table) if e.type == EventType.WORKSPACE_BANK_PIN_SET.value
    ]
    assert event.entity_id == bank.ws and event.payload == {"credential_slot": "primary"}
    assert APP_PIN.encode() not in _all_text(bank)


def test_setting_again_replaces_the_row_with_a_new_nonce(bank):
    _set_pin(bank)
    (first,) = bank.rows(credential_table)
    _set_pin(bank, "another-PIN-1234", now=T1)
    (second,) = bank.rows(credential_table)
    assert first.id == second.id and bytes(first.nonce) != bytes(second.nonce)
    assert second.updated_at == T1 and second.created_at == T0
    with workspace_transaction(bank.engine, bank.ws) as conn:
        assert credentials.resolve_pin(conn, "primary", bank.settings) == "another-PIN-1234"


def test_the_app_pin_wins_over_the_container_secret_and_deleting_it_falls_back(bank):
    assert _connection(bank).credential_source is CredentialSource.SERVER
    _set_pin(bank)
    connection = _connection(bank)
    assert connection.credential_source is CredentialSource.APP
    assert connection.credential_set_at == T0 and connection.credential_readable is True

    _sync(bank)
    assert bank.clients[0].init["pin"] == APP_PIN

    assert _delete_pin(bank) is True
    assert _delete_pin(bank) is False  # nothing left to delete, no second event
    assert _connection(bank).credential_source is CredentialSource.SERVER
    _sync(bank, at=T1 + timedelta(minutes=1))
    assert bank.clients[1].init["pin"] == PIN
    deleted = [
        e for e in bank.rows(event_table) if e.type == EventType.WORKSPACE_BANK_PIN_DELETED.value
    ]
    assert [e.payload for e in deleted] == [{"credential_slot": "primary"}]


def test_with_only_the_app_pin_the_sync_runs_and_without_either_it_is_pin_missing(bank, tmp_path):
    bank.settings = _settings(tmp_path, fints_pin=None)  # the product id alone (D196)
    assert _connection(bank).credential_source is CredentialSource.NONE
    with pytest.raises(RuleError) as missing:
        bank.start()
    assert missing.value.code == E.BANK_PIN_MISSING

    _set_pin(bank)
    sync, outcome = _sync(bank)
    assert outcome == "done" and bank.clients[0].init["pin"] == APP_PIN


def test_a_pin_deleted_after_the_start_fails_the_run_before_any_dialog(bank, tmp_path):
    bank.settings = _settings(tmp_path, fints_pin=None)
    _set_pin(bank)
    sync = bank.start()
    # gone between the start and the job's begin: begin_run's check fails the run
    _delete_pin(bank)
    bank.job(sync.id, _good(approval_polls=0))
    assert bank.sync(sync.id).error_code == E.BANK_PIN_MISSING
    assert bank.clients == []


def test_a_pin_another_workspace_stored_is_invisible(bank, read, fresh_workspaces):
    _set_pin(bank)
    _, other = fresh_workspaces
    assert read(other, credentials.app_pins, bank.settings) == []
    with workspace_transaction(bank.engine, other) as conn:
        assert conn.execute(select(credential_table)).all() == []
        no_pin = _settings_without_pin(bank)
        with pytest.raises(RuleError) as missing:
            credentials.resolve_pin(conn, "primary", no_pin)
    assert missing.value.code == E.BANK_PIN_MISSING


def _settings_without_pin(bank):
    return dataclasses.replace(bank.settings, fints_pin=None, fints_pins={})


def test_a_ciphertext_moved_to_another_slot_does_not_decrypt(bank):
    """The AAD binds a PIN to its workspace and slot: a row copied to another slot is
    unreadable, never the other slot's PIN."""
    _set_pin(bank)
    with workspace_transaction(bank.engine, bank.ws) as conn:
        conn.execute(update(credential_table).values(credential_slot="dkb"))
        with pytest.raises(RuleError) as unreadable:
            credentials.resolve_pin(conn, "dkb", bank.settings)
    assert unreadable.value.code == E.BANK_PIN_UNREADABLE


# --- A key that no longer opens them (the session key rotated) -----------------------------


def test_a_rotated_session_key_makes_the_pin_unreadable_never_a_crash(bank):
    _set_pin(bank)
    rotated = dataclasses.replace(bank.settings, session_key=OTHER_KEY)
    with workspace_transaction(bank.engine, bank.ws) as conn:
        with pytest.raises(RuleError) as unreadable:
            credentials.resolve_pin(conn, "primary", rotated)
        assert credentials.pin_state(conn, "primary", rotated).readable is False
    assert unreadable.value.code == E.BANK_PIN_UNREADABLE
    connection = _connection(bank, rotated)
    assert connection.credential_source is CredentialSource.APP  # the stored one still wins
    assert (connection.credential_readable, connection.name_readable) == (False, False)
    assert connection.login_masked == "***"


def test_a_stored_pin_that_no_longer_decrypts_fails_the_run_as_pin_unreadable(bank):
    _set_pin(bank)
    with workspace_transaction(bank.engine, bank.ws) as conn:
        conn.execute(update(credential_table).values(key_version=2))  # a key this build lacks
    sync, outcome = _sync(bank)
    assert outcome == "failed" and bank.clients == []  # before any dialog
    assert bank.sync(sync.id).error_code == E.BANK_PIN_UNREADABLE
    reasoning, question = service.failed_card_text(E.BANK_PIN_UNREADABLE, None)
    assert "PIN ändern" in question and "(bank.pin_unreadable)" in reasoning


def test_a_login_that_no_longer_decrypts_fails_the_run_as_login_unreadable(bank):
    bank.settings = dataclasses.replace(bank.settings, session_key=OTHER_KEY)
    sync, _ = _sync(bank)
    assert bank.clients == []  # before any dialog
    assert bank.sync(sync.id).error_code == E.BANK_LOGIN_UNREADABLE
    # typing the login again stores it under the current key, and the next run logs in
    bank.run(
        bank.ws,
        service.update_connection,
        bank.account.id,
        BankConnectionPatch(login_name=LOGIN),
        settings=bank.settings,
        now=T1,
    )
    assert _connection(bank).name_readable is True
    _sync(bank, at=T1 + timedelta(minutes=1))
    assert bank.clients[0].init["login_name"] == LOGIN


# --- The login name --------------------------------------------------------------------------


def test_a_new_connection_stores_its_login_encrypted_only(bank):
    row = bank.connection_row()
    assert row.login_name is None and row.login_key_version == credentials.KEY_VERSION
    assert LOGIN.encode() not in bytes(row.login_ciphertext)
    assert credentials.connection_login(row, bank.settings) == LOGIN
    assert _connection(bank).login_masked == "lo***yz"
    assert LOGIN.encode() not in _all_text(bank)


def test_an_unchanged_login_keeps_the_state_and_a_new_one_is_encrypted_and_drops_it(bank):
    _sync(bank)
    assert bank.connection_row().client_data is not None
    same = bank.run(
        bank.ws,
        service.update_connection,
        bank.account.id,
        BankConnectionPatch(login_name=LOGIN),
        settings=bank.settings,
        now=T1,
    )
    assert same.state_present is True

    changed = bank.run(
        bank.ws,
        service.update_connection,
        bank.account.id,
        BankConnectionPatch(login_name=NEW_LOGIN),
        settings=bank.settings,
        now=T1,
    )
    assert changed.state_present is False and changed.login_masked == "ot***77"
    row = bank.connection_row()
    assert row.login_name is None and NEW_LOGIN.encode() not in bytes(row.login_ciphertext)
    assert credentials.connection_login(row, bank.settings) == NEW_LOGIN
    updated = bank.events(EventType.ACCOUNT_CONNECTION_UPDATED)[-1]
    assert updated["login_masked"] == "ot***77" and NEW_LOGIN not in str(updated)


def test_a_live_connection_keeps_its_state_after_its_login_is_encrypted(bank):
    """The rental connection made before migration 0039: its state's MAC was computed over
    the readable login. Encrypting the login changes no MAC input, so the next sync restores
    the state and the bank asks for no new approval."""
    _sync(bank)
    _make_readable(bank)
    readable = bank.connection_row()
    assert readable.login_name == LOGIN and readable.login_ciphertext is None
    key = service.state_key(bank.settings.session_key)
    assert service.verified_state(key, readable, LOGIN) == FAKE_STATE

    with workspace_transaction(bank.engine, bank.ws) as conn:
        assert credentials.encrypt_readable_logins(conn, bank.settings) == 1
    encrypted = bank.connection_row()
    assert encrypted.login_name is None
    login = credentials.connection_login(encrypted, bank.settings)
    assert login == LOGIN and service.verified_state(key, encrypted, login) == FAKE_STATE

    _sync(bank, at=T1 + timedelta(minutes=1))
    assert bank.clients[1].init["client_data"] == FAKE_STATE  # restored, no approval asked


def test_the_first_sync_encrypts_a_readable_login_and_keeps_the_state(bank):
    _sync(bank)
    _make_readable(bank)
    _sync(bank, at=T1 + timedelta(minutes=1))
    assert bank.clients[1].init["client_data"] == FAKE_STATE
    assert bank.clients[1].init["login_name"] == LOGIN
    row = bank.connection_row()
    assert row.login_name is None and row.login_ciphertext is not None
    assert bank.sync(bank.rows(sync_table)[-1].id).error_code in (None, E.BANK_NOTHING_NEW)


def test_the_start_up_step_leaves_no_readable_login(bank, monkeypatch, caplog):
    _make_readable(bank)
    monkeypatch.setattr("hausbuch.auth.lookup.provisioned_workspace_ids", lambda engine: {bank.ws})
    with caplog.at_level(logging.INFO, logger=credentials.logger.name):
        assert credentials.encrypt_all_logins(bank.engine, bank.settings) == 1
        assert credentials.encrypt_all_logins(bank.engine, bank.settings) == 0  # idempotent
    with workspace_transaction(bank.engine, bank.ws) as conn:
        readable = conn.execute(
            select(connection_table).where(connection_table.c.login_name.is_not(None))
        ).all()
    assert readable == []
    assert [r.fields["encrypted"] for r in caplog.records] == [1, 0]
    assert not [r for r in caplog.records if LOGIN in repr(r.__dict__)]


def test_the_start_up_step_never_raises_and_logs_a_class_name_only(bank, monkeypatch, caplog):
    def broken(engine):
        raise RuntimeError(f"connection refused for {LOGIN}")

    monkeypatch.setattr("hausbuch.auth.lookup.provisioned_workspace_ids", broken)
    with caplog.at_level(logging.INFO, logger=credentials.logger.name):
        assert credentials.encrypt_all_logins(bank.engine, bank.settings) == 0
    (record,) = caplog.records
    assert record.fields["exc_type"] == "RuntimeError" and LOGIN not in repr(record.__dict__)


# --- Nothing secret anywhere (D119, D196) ----------------------------------------------------


def test_no_pin_or_login_reaches_a_row_an_event_a_job_or_a_log_line(bank, caplog):
    with caplog.at_level(logging.DEBUG):
        _set_pin(bank)
        _sync(bank)
        _sync(bank, at=T1 + timedelta(minutes=1), script=_good(refuse_at={"balance": "9800"}))
        bank.run(
            bank.ws,
            service.update_connection,
            bank.account.id,
            BankConnectionPatch(login_name=NEW_LOGIN),
            settings=bank.settings,
            now=T1,
        )
        _delete_pin(bank)
    stored = _all_text(bank)
    assert not [s for s in SECRETS if s.encode() in stored]
    logged = repr([(r.getMessage(), r.args, r.__dict__.get("fields")) for r in caplog.records])
    assert caplog.records and not [s for s in SECRETS if s in logged]
