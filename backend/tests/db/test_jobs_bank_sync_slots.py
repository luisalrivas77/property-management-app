"""Phase 6 step 3 against Postgres (docs/phases/phase-6.md D163, D173, §15 step 3): a
connection's credential slot picks its PIN, a slot without its secret is bank.pin_missing, a
private account needs its reconciled history before a connection and its first window starts
after it, the bank's parameters are recorded once per change, and a DKB connection's daily
fetch waits for two manual syncs without an approval.

Reuses test_jobs_bank_sync's Bank helper and the scripted fake client. The private account's
history is inserted with SQL (a file, a document and a reconciled statement_import), never
through phase 6 step 1's import code. Every PIN, login and IBAN is invented."""

import hashlib
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_jobs_bank_sync import (  # the helpers only, never a test_* name
    LOGIN,
    MORNING,
    PIN,
    PRODUCT_ID,
    T0,
    T1,
    URL,
    Bank,
    _good,
    _settings,
)
from sqlalchemy import insert, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.bank_sync import BankConnectionCreate, BankConnectionPatch, SyncStatus
from hausbuch.schemas.events import EventType
from hausbuch.services import bank_sync as service
from hausbuch.services import review
from hausbuch.services.accounts import create_account

pytestmark = pytest.mark.db

DKB_PIN = "PIN-dkb-0815-secret"
PRIVATE_IBAN = "DE23100000000000000002"  # Bundesbank test bank code 10000000, invented
DKB_LOGIN = "dkb-login-abc"
SECRETS = (PIN, DKB_PIN, PRODUCT_ID, LOGIN, DKB_LOGIN)


@pytest.fixture
def bank(run, read, app_engine, blob_store, tmp_path, fresh_workspaces):
    """The rental account with its connection on the primary slot (test_jobs_bank_sync)."""
    ws, _ = fresh_workspaces
    return Bank(run, read, app_engine, blob_store, tmp_path, ws)


def _with_dkb_slot(bank, tmp_path) -> None:
    """The primary PIN and the slot dkb's (bank.start and bank.job read bank.settings)."""
    bank.settings = _settings(tmp_path, fints_pins={"dkb": DKB_PIN})


def _slot(bank, slot: str):
    return bank.run(
        bank.ws,
        service.update_connection,
        bank.account.id,
        BankConnectionPatch(credential_slot=slot),
        now=T0,
    )


def _connection(bank, account_id=None):
    return bank.read(bank.ws, service.get_connection, account_id or bank.account.id)


# --- Slots (D163) -----------------------------------------------------------------------------


def test_a_connection_on_its_own_slot_logs_in_with_that_slots_pin(bank, tmp_path):
    patched = _slot(bank, "dkb")
    assert (patched.credential_slot, patched.credential_variable) == (
        "dkb",
        "HAUSBUCH_FINTS_PIN_DKB",
    )
    updated = bank.events(EventType.ACCOUNT_CONNECTION_UPDATED)[-1]
    assert updated["changed"] == ["credential_slot"] and updated["credential_slot"] == "dkb"

    with pytest.raises(RuleError) as raised:  # only the primary PIN is set
        bank.start()
    assert raised.value.code == E.BANK_PIN_MISSING
    assert "HAUSBUCH_FINTS_PIN_DKB" in str(raised.value)

    _with_dkb_slot(bank, tmp_path)
    sync = bank.start()
    assert bank.job(sync.id, _good()) == "done"
    assert bank.clients[0].init["pin"] == DKB_PIN


def test_a_run_whose_slot_lost_its_pin_fails_and_the_card_names_the_variable(bank, tmp_path):
    _slot(bank, "dkb")
    _with_dkb_slot(bank, tmp_path)
    sync = bank.start()
    bank.settings = _settings(tmp_path)  # redeployed without the slot's secret
    assert bank.job(sync.id, _good()) == "skipped"
    row = bank.sync(sync.id)
    assert (row.status, row.error_code) == (SyncStatus.FAILED, E.BANK_PIN_MISSING)
    assert bank.clients == []  # no dialog without the PIN
    (card,) = [i for i in bank.read(bank.ws, review.inbox, now=T1) if i.sync_id == sync.id]
    assert card.kind is InboxItemKind.BANK_SYNC_FAILED
    assert "HAUSBUCH_FINTS_PIN_DKB" in card.reasoning + card.question
    assert "Scaleway" in card.question


def test_the_scheduled_run_of_a_slot_without_its_pin_ends_on_the_same_card(bank, tmp_path):
    _slot(bank, "dkb")
    bank.auto_sync()
    assert dict(bank.schedule().outcomes) == {"queued": 1}
    (row,) = bank.rows(sync_table)
    assert bank.job(row.id, _good(), at=MORNING + timedelta(seconds=5)) == "skipped"
    assert bank.sync(row.id).error_code == E.BANK_PIN_MISSING


# --- A private account (§15 step 3, D173 (4)) -----------------------------------------------


def _private_account(bank):
    return bank.run(
        bank.ws,
        create_account,
        AccountCreate(
            label="Girokonto B",
            bank="DKB",
            iban=PRIVATE_IBAN,
            opening_balance=Decimal("100.00"),
            opening_balance_on=date(2025, 12, 31),
            is_owner_account=True,
        ),
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )


def _import(bank, account_id, *, last: date, status: str, fmt: str = "csv_camt") -> None:
    """A statement import of the account, inserted directly: reconciled with a declared and
    computed closing, or still waiting for its balance."""
    sha = hashlib.sha256(uuid.uuid4().bytes).digest()
    reconciled = status == "reconciled"
    with workspace_transaction(bank.engine, bank.ws) as conn:
        conn.execute(
            insert(file_table).values(
                workspace_id=bank.ws,
                sha256=sha,
                size_bytes=1,
                media_type="text/csv",
                storage_key=f"test/{sha.hex()}",
                created_at=T0,
            )
        )
        document_id = conn.execute(
            insert(document_table)
            .values(
                workspace_id=bank.ws,
                file_sha256=sha,
                source="upload",
                type="statement",
                received_at=T0,
                created_at=T0,
                updated_at=T0,
            )
            .returning(document_table.c.id)
        ).scalar_one()
        conn.execute(
            insert(import_table).values(
                workspace_id=bank.ws,
                account_id=account_id,
                document_id=document_id,
                format=fmt,
                first_booking_on=date(2026, 1, 2),
                last_booking_on=last,
                declared_closing=Decimal("250.00") if reconciled else None,
                computed_closing=Decimal("250.00") if reconciled else None,
                balance_source="user" if reconciled else "none",
                status=status,
                created_at=T0,
                updated_at=T0,
            )
        )


def _connect(bank, account_id, slot="dkb"):
    return bank.run(
        bank.ws,
        service.create_connection,
        BankConnectionCreate(
            account_id=account_id,
            bank_code="10000000",
            fints_url=URL,
            login_name=DKB_LOGIN,
            credential_slot=slot,
        ),
        now=T0,
    )


def test_a_private_account_is_connected_only_after_its_reconciled_history(bank):
    account = _private_account(bank)
    with pytest.raises(RuleError) as raised:
        _connect(bank, account.id)
    assert raised.value.code == E.BANK_HISTORY_MISSING

    _import(bank, account.id, last=date(2026, 11, 5), status="imported")  # no balance yet
    with pytest.raises(RuleError) as raised:
        _connect(bank, account.id)
    assert raised.value.code == E.BANK_HISTORY_MISSING

    _import(bank, account.id, last=date(2026, 10, 31), status="reconciled")
    connection = _connect(bank, account.id)
    assert connection.credential_slot == "dkb"
    assert connection.credential_variable == "HAUSBUCH_FINTS_PIN_DKB"


def test_the_first_window_starts_after_the_last_reconciled_import(bank, tmp_path):
    account = _private_account(bank)
    _import(bank, account.id, last=date(2026, 9, 30), status="reconciled")
    _import(bank, account.id, last=date(2026, 10, 31), status="reconciled")
    _import(bank, account.id, last=date(2026, 11, 8), status="imported")  # not reconciled
    _connect(bank, account.id)
    _with_dkb_slot(bank, tmp_path)
    sync = bank.run(bank.ws, service.start_sync, account.id, settings=bank.settings, now=T0)
    # max(31 Dec 2025 + 1, 31 Oct 2026 + 1) = 1 Nov 2026, to today (T0 is 11 Nov 2026)
    assert (sync.requested_from, sync.requested_to) == (date(2026, 11, 1), date(2026, 11, 11))


def _synced_through(bank, account_id, last: date) -> None:
    """The connection's last booking day, as a successful sync would have left it."""
    with workspace_transaction(bank.engine, bank.ws) as conn:
        conn.execute(
            update(connection_table)
            .where(connection_table.c.account_id == account_id)
            .values(last_booking_on=last)
        )


def test_a_later_window_never_reaches_back_into_a_csv_history(bank, tmp_path):
    """D173 (4) as amended after the step 3 review: the 14-day overlap stops at the end of
    the reconciled history from files without the bank's reference; camt imports, the sync's
    own among them, and an unreconciled CSV do not count."""
    account = _private_account(bank)
    _import(bank, account.id, last=date(2026, 10, 31), status="reconciled")  # the CSV export
    _import(bank, account.id, last=date(2026, 11, 2), status="imported")  # never reconciled
    _import(bank, account.id, last=date(2026, 11, 5), status="reconciled", fmt="camt")
    _connect(bank, account.id)
    _with_dkb_slot(bank, tmp_path)
    _synced_through(bank, account.id, date(2026, 11, 5))
    sync = bank.run(bank.ws, service.start_sync, account.id, settings=bank.settings, now=T0)
    # max(5 Nov 2026 - 14 days = 22 Oct 2026, 31 Oct 2026 + 1 = 1 Nov 2026) = 1 Nov 2026
    assert (sync.requested_from, sync.requested_to) == (date(2026, 11, 1), date(2026, 11, 11))


def test_a_camt_only_history_keeps_the_overlap(bank):
    _import(bank, bank.account.id, last=date(2026, 11, 5), status="reconciled", fmt="camt")
    _synced_through(bank, bank.account.id, date(2026, 11, 5))
    # 5 Nov 2026 - 14 days = 22 Oct 2026, as in phase 5
    assert bank.start().requested_from == date(2026, 10, 22)


def test_the_rental_account_needs_no_history_for_its_connection(bank):
    # The Bank fixture connected the rental account without any import (phase 5 unchanged).
    assert bank.connection.account_id == bank.account.id
    assert bank.start().requested_from == date(2026, 4, 1)


# --- The bank's parameters (D173 (7)) ----------------------------------------------------------


def _parameter_events(bank):
    return bank.events(EventType.ACCOUNT_BANK_PARAMETERS_READ)


def test_the_bank_parameters_are_recorded_once_per_change_without_a_secret(bank):
    first = bank.start()
    bank.job(first.id, _good())
    (read,) = _parameter_events(bank)
    assert read["bank_name"] == "Testbank" and read["sync_id"] == str(first.id)
    assert read["camt_readable"] is True

    later = T1 + timedelta(minutes=1)
    second = bank.start(now=later)
    bank.job(second.id, _good(), at=later + timedelta(seconds=5))
    assert len(_parameter_events(bank)) == 1  # nothing changed, nothing recorded

    latest = T1 + timedelta(minutes=2)
    third = bank.start(now=latest)
    bank.job(third.id, _good(bank_name="DKB AG"), at=latest + timedelta(seconds=5))
    assert [e["bank_name"] for e in _parameter_events(bank)] == ["Testbank", "DKB AG"]
    connection = _connection(bank)
    assert connection.bank_name == "DKB AG"
    assert connection.approval_text == "Jetzt in der DKB-App freigeben"

    rows = bank.rows(
        event_table, event_table.c.type == EventType.ACCOUNT_BANK_PARAMETERS_READ.value
    )
    assert not [s for s in SECRETS if s in repr([tuple(r) for r in rows])]


def test_a_refused_login_still_records_the_parameters(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(refuse_at={"open": "9942"})) == "failed"
    (read,) = _parameter_events(bank)
    assert read["sync_id"] == str(sync.id)


def test_parameters_that_cannot_be_read_never_fail_the_run(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(parameters_fail=True)) == "done"
    assert _parameter_events(bank) == []
    assert _connection(bank).bank_name is None


# --- DKB's daily fetch (D173 (5)) ---------------------------------------------------------------


def _manual(bank, minute: int, **script):
    at = T1 + timedelta(minutes=minute)
    sync = bank.start(now=at)
    assert bank.job(sync.id, _good(bank_name="DKB AG", **script), at=at + timedelta(seconds=5)) in (
        "done",
        "nothing_new",
    )


def test_a_dkb_connection_waits_for_two_manual_syncs_without_an_approval(bank):
    _manual(bank, 0, approval_polls=1)  # the first login asks
    assert _connection(bank).auto_sync_allowed is False
    with pytest.raises(RuleError) as raised:
        bank.auto_sync()
    assert raised.value.code == E.BANK_AUTO_SYNC_WAITS

    _manual(bank, 1, approval_polls=1, approval_after_restore=True)  # it asks again
    _manual(bank, 2)
    assert _connection(bank).auto_sync_allowed is False  # one clean sync is not two

    _manual(bank, 3)
    assert _connection(bank).auto_sync_allowed is True
    bank.auto_sync()
    assert bank.connection_row().auto_sync is True


def test_any_other_bank_may_switch_the_daily_fetch_on_at_once(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(approval_polls=1)) == "done"  # Testbank asks at login
    assert _connection(bank).auto_sync_allowed is True
    bank.auto_sync()


def test_a_dkb_switch_set_before_the_first_sync_is_held_by_the_schedule(bank):
    bank.auto_sync()  # no parameters read yet: the bank is not known to be DKB
    _manual(bank, 0, approval_polls=1)
    result = bank.schedule()
    assert dict(result.outcomes) == {service.SCHEDULE_AUTO_SYNC_WAITS: 1}
    assert [r.trigger for r in bank.rows(sync_table)] == ["manual"]
