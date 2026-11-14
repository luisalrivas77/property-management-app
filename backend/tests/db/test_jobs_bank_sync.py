"""The FinTS sync end to end against Postgres, with the scripted fake client
(docs/phases/phase-5.md §4, D115-D120, D127): services.bank_sync's commands and
jobs.bank_sync.run_bank_sync, as hausbuch_app under RLS.

The bank's streams are the daily reports of phase 2's anonymised camt ZIP (22 bookings,
closing -3.028,51) for a synthetic IBAN; the PIN, login and product id are invented."""

import base64
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from fints.exceptions import FinTSConnectionError
from fints_fake import DECOUPLED, FAKE_STATE, Script, factory, fake_account
from sqlalchemy import func, insert, select, text, update
from test_bank_parsers import CAMT, build

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_statement as statement_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs import bank_sync as job
from hausbuch.jobs import bank_sync_schedule as schedule_job
from hausbuch.jobs.document_read import JobDeps, RetryLater
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.bank_sync import (
    BankConnectionCreate,
    BankConnectionPatch,
    SyncStatus,
    SyncTrigger,
)
from hausbuch.schemas.enums import AccountSource, BalanceSource, DocumentSource, ImportStatus
from hausbuch.schemas.events import EventType
from hausbuch.services import bank_sync as service
from hausbuch.services import review, statements
from hausbuch.services.accounts import create_account, get_account
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=5)
IBAN = "DE50100000000000000001"
OTHER_IBAN = "DE89370400440532013000"
CLOSING = Decimal("-3028.51")
PIN = "PIN-4711-secret"
PRODUCT_ID = "PRODUCTID0123456789ABCDEF"
LOGIN = "loginname-xyz"
URL = "https://fints.example.org/fints"
SECRETS = (PIN, PRODUCT_ID, LOGIN)
MORNING = datetime(2026, 11, 12, 6, 30, tzinfo=UTC)  # the daily run (D120: 06:30 UTC)

ZIP = build(next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports").input)
with zipfile.ZipFile(io.BytesIO(ZIP)) as _archive:
    STREAMS = [_archive.read(name) for name in sorted(_archive.namelist())]


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": TEST_IBAN_KEY,
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": tmp_path / "blobs",
        "fints_pin": PIN,
        "fints_product_id": PRODUCT_ID,
    } | overrides
    return Settings(**values)


class Bank:
    """One account with a connection, plus helpers to start and run syncs."""

    def __init__(self, run, read, app_engine, blob_store, tmp_path, ws):
        self.run, self.read, self.engine, self.ws = run, read, app_engine, ws
        self.settings = _settings(tmp_path)
        self.deps = JobDeps(app_engine, self.settings, blob_store, None, clock=lambda: T1)
        self.account = run(
            ws,
            create_account,
            AccountCreate(
                label="Mietkonto",
                bank="Sparkasse",
                iban=IBAN,
                opening_balance=Decimal("0.00"),
                opening_balance_on=date(2026, 3, 31),
            ),
            iban_key=TEST_IBAN_KEY,
            now=T0,
        )
        self.connection = run(
            ws,
            service.create_connection,
            BankConnectionCreate(
                account_id=self.account.id, bank_code="10000000", fints_url=URL, login_name=LOGIN
            ),
            now=T0,
        )
        self.clients: list = []
        self.clock = [0.0]

    def start(self, now=T0, settings=None):
        return self.run(
            self.ws,
            service.start_sync,
            self.account.id,
            settings=settings or self.settings,
            now=now,
        )

    def job(self, sync_id, script: Script, *, attempt=0, at=T1, on_sleep=None):
        deps = JobDeps(self.engine, self.settings, self.deps.blob_store, None, clock=lambda: at)

        def sleep(seconds):
            self.clock[0] += seconds
            if on_sleep is not None:
                on_sleep()

        network = job.Network(
            client_factory=factory(script, self.clients),
            sleep=sleep,
            monotonic=lambda: self.clock[0],
        )
        return job.run_bank_sync(
            deps, workspace_id=self.ws, sync_id=sync_id, attempt=attempt, network=network
        )

    def schedule(self, at=None, settings=None):
        """One run of the daily task over this workspace (D120)."""
        deps = JobDeps(
            self.engine,
            settings or self.settings,
            self.deps.blob_store,
            None,
            clock=lambda: at or MORNING,
        )
        return schedule_job.run_bank_sync_scheduled(deps, workspace_ids=[self.ws])

    def auto_sync(self, on=True, account_id=None):
        self.run(
            self.ws,
            service.update_connection,
            account_id or self.account.id,
            BankConnectionPatch(auto_sync=on),
            now=T0,
        )

    def sync(self, sync_id):
        return self.read(self.ws, service.get_sync, sync_id)

    def rows(self, table, *where):
        with workspace_transaction(self.engine, self.ws) as conn:
            return conn.execute(select(table).where(*where)).all()

    def events(self, type_):
        return [
            e.payload
            for e in self.rows(event_table, event_table.c.type == type_.value)
            if e.entity_id == self.account.id
        ]

    def connection_row(self):
        return self.rows(connection_table, connection_table.c.account_id == self.account.id)[0]


@pytest.fixture
def bank(run, read, app_engine, blob_store, tmp_path, fresh_workspaces):
    ws, _ = fresh_workspaces
    return Bank(run, read, app_engine, blob_store, tmp_path, ws)


def _good(**overrides) -> Script:
    return Script(**({"streams": list(STREAMS), "balance": CLOSING} | overrides))


# --- The connection --------------------------------------------------------------------------


def test_a_connection_makes_the_account_fints_and_masks_the_login(bank):
    assert bank.connection.login_masked == "lo***yz"
    assert bank.connection.state_present is False
    assert bank.read(bank.ws, get_account, bank.account.id).source is AccountSource.FINTS
    (created,) = bank.events(EventType.ACCOUNT_CONNECTION_CREATED)
    assert created["login_masked"] == "lo***yz" and LOGIN not in str(created)


def test_a_second_connection_of_the_account_is_a_conflict(bank):
    from hausbuch.services.errors import ConflictError

    with pytest.raises(ConflictError):
        bank.run(
            bank.ws,
            service.create_connection,
            BankConnectionCreate(
                account_id=bank.account.id, bank_code="10000000", fints_url=URL, login_name="x"
            ),
            now=T0,
        )


def test_an_owner_account_gets_no_connection(run, fresh_workspaces):
    ws, _ = fresh_workspaces
    owner = run(
        ws,
        create_account,
        AccountCreate(
            label="Privat",
            bank="Bank",
            iban=OTHER_IBAN,
            opening_balance=Decimal("0"),
            opening_balance_on=date(2026, 1, 1),
            is_owner_account=True,
        ),
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )
    with pytest.raises(RuleError):
        run(
            ws,
            service.create_connection,
            BankConnectionCreate(
                account_id=owner.id, bank_code="10000000", fints_url=URL, login_name=LOGIN
            ),
            now=T0,
        )


def test_a_new_login_drops_the_stored_state(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(approval_polls=0)) == "done"
    assert bank.connection_row().client_data is not None

    patched = bank.run(
        bank.ws,
        service.update_connection,
        bank.account.id,
        BankConnectionPatch(login_name="another-login"),
        now=T1,
    )
    assert patched.state_present is False and patched.tan_mechanism is None
    updated = bank.events(EventType.ACCOUNT_CONNECTION_UPDATED)[-1]
    assert updated["changed"] == ["login_name", "tan_mechanism"]
    assert "another-login" not in str(updated)


# --- Starting ---------------------------------------------------------------------------------


def test_a_start_queues_a_row_and_a_job_with_ids_only(bank):
    sync = bank.start()
    assert (sync.status, sync.requested_from, sync.requested_to) == (
        SyncStatus.QUEUED,
        date(2026, 4, 1),
        date(2026, 11, 11),
    )
    with workspace_transaction(bank.engine, bank.ws) as conn:
        args = conn.execute(
            text("SELECT args, lock FROM procrastinate_jobs WHERE lock = :lock"),
            {"lock": f"account:{bank.account.id}"},
        ).all()
    assert [a.args for a in args] == [{"workspace_id": str(bank.ws), "sync_id": str(sync.id)}]


def test_a_second_start_while_one_is_queued_is_refused(bank):
    bank.start()
    with pytest.raises(RuleError) as raised:
        bank.start()
    assert raised.value.code == E.BANK_SYNC_RUNNING


def test_a_stale_queued_row_is_restarted(bank):
    first = bank.start()
    later = T0 + timedelta(seconds=bank.settings.sync_approval_timeout_s + 301)
    again = bank.start(now=later)
    assert again.id == first.id and again.started_at == later


@pytest.mark.parametrize(
    "overrides",
    [{"fints_pin": None, "fints_product_id": None}, {"fints_product_id": "A" * 26}],
    ids=["no_pin", "product_id_too_long"],
)
def test_a_start_without_pin_or_usable_product_id_is_not_configured(bank, tmp_path, overrides):
    with pytest.raises(RuleError) as raised:
        bank.start(settings=_settings(tmp_path, **overrides))
    assert raised.value.code == E.BANK_NOT_CONFIGURED


def test_a_fifth_manual_start_within_the_hour_is_rate_limited(bank):
    for minute in range(4):
        sync = bank.start(now=T0 + timedelta(minutes=minute))
        script = _good(accounts=[fake_account(OTHER_IBAN)])
        at = T0 + timedelta(minutes=minute, seconds=5)
        assert bank.job(sync.id, script, at=at) == "failed"
    with pytest.raises(RuleError) as raised:
        bank.start(now=T0 + timedelta(minutes=10))
    assert raised.value.code == E.BANK_RATE_LIMITED
    assert bank.start(now=T0 + timedelta(minutes=61)).status is SyncStatus.QUEUED


# --- The first sync and the ones after it ---------------------------------------------------


def test_the_first_sync_waits_for_the_approval_and_imports_the_zip(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(approval_polls=2)) == "done"

    done = bank.sync(sync.id)
    assert (done.status, done.lines_total, done.lines_new) == (SyncStatus.DONE, 22, 22)
    assert done.balance_source is BalanceSource.FILE and done.finished_at == T1
    (imported,) = bank.rows(import_table, import_table.c.id == done.import_id)
    assert imported.status == ImportStatus.RECONCILED.value
    (document,) = bank.rows(document_table, document_table.c.id == imported.document_id)
    assert document.source == DocumentSource.BANK_SYNC.value
    assert document.original_filename == "fints-20260401-20261111.zip"
    assert len(bank.rows(transaction_table)) == 22

    client = bank.clients[0]
    assert client.init["pin"] == PIN and client.init["product_id"] == PRODUCT_ID
    assert client.init["client_data"] is None
    assert client.calls.count("poll") == 3 and client.calls[-1] == "state"
    assert client.window[:2] == (date(2026, 4, 1), date(2026, 11, 11))

    connection = bank.connection_row()
    assert bytes(connection.client_data) == FAKE_STATE
    assert connection.tan_mechanism == DECOUPLED and connection.sca_confirmed_at == T1
    assert (connection.last_sync_at, connection.last_booking_on) == (T1, imported.last_booking_on)
    assert service.verified_state(service.state_key(bank.settings.session_key), connection)

    assert len(bank.events(EventType.ACCOUNT_SYNC_APPROVAL_NEEDED)) == 1
    (completed,) = bank.events(EventType.ACCOUNT_SYNC_COMPLETED)
    assert completed["reconciled"] is True and completed["lines_new"] == 22


def test_the_new_lines_go_through_import_statement_unchanged(bank, monkeypatch):
    calls = []
    real = service.import_statement

    def spy(ctx, store, account_id, data, **kwargs):
        calls.append((account_id, kwargs["media_type"]))
        return real(ctx, store, account_id, data, **kwargs)

    monkeypatch.setattr(service, "import_statement", spy)
    sync = bank.start()
    bank.job(sync.id, _good())
    assert calls == [(bank.account.id, "application/zip")]


def test_a_later_sync_reuses_the_state_and_stores_nothing_when_nothing_is_new(bank):
    first = bank.start()
    bank.job(first.id, _good(approval_polls=0))
    documents = len(bank.rows(document_table))

    later = T1 + timedelta(minutes=1)
    second = bank.start(now=later)
    outcome = bank.job(second.id, _good(approval_polls=0), at=later + timedelta(seconds=5))

    assert outcome == "nothing_new"
    row = bank.sync(second.id)
    assert (row.status, row.lines_new, row.import_id) == (SyncStatus.NOTHING_NEW, 0, None)
    assert row.balance_checked is True and row.error_code == E.BANK_NOTHING_NEW
    assert len(bank.rows(document_table)) == documents
    assert len(bank.rows(import_table)) == 1
    assert bank.clients[1].init["client_data"] == FAKE_STATE  # restored, no approval asked
    assert "poll" not in bank.clients[1].calls


def test_a_sync_after_an_upload_brings_nothing_new_and_moves_the_window_on(bank):
    """D141: the lines were uploaded as a file first; the sync finds all of them stored, but
    the connection's last booking day still moves to the latest one the bank returned, so the
    next window starts 14 days before it instead of at the anchor again."""
    bank.run(
        bank.ws,
        statements.import_statement,
        bank.deps.blob_store,
        bank.account.id,
        ZIP,
        media_type="application/zip",
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )
    assert bank.connection_row().last_booking_on is None
    (uploaded,) = bank.rows(import_table)

    sync = bank.start()
    assert bank.job(sync.id, _good()) == "nothing_new"
    row = bank.sync(sync.id)
    assert (row.lines_total, row.lines_new, row.import_id) == (22, 0, None)
    assert bank.connection_row().last_booking_on == uploaded.last_booking_on

    later = T1 + timedelta(minutes=1)
    second = bank.start(now=later)
    assert second.requested_from == uploaded.last_booking_on - timedelta(days=14)


def test_a_sync_with_no_booked_stream_keeps_the_last_booking_day(bank):
    first = bank.start()
    bank.job(first.id, _good())
    before = bank.connection_row().last_booking_on
    later = T1 + timedelta(minutes=1)
    second = bank.start(now=later)
    assert bank.job(second.id, _good(streams=[]), at=later + timedelta(seconds=5)) == (
        "nothing_new"
    )
    assert bank.connection_row().last_booking_on == before


def test_a_balance_that_moved_without_new_lines_is_a_drift_card(bank):
    first = bank.start()
    bank.job(first.id, _good())
    later = T1 + timedelta(minutes=1)
    second = bank.start(now=later)
    bank.job(second.id, _good(balance=CLOSING + 1), at=later + timedelta(seconds=5))

    assert bank.sync(second.id).error_code == E.BANK_BALANCE_DRIFT
    cards = [i for i in bank.read(bank.ws, review.inbox, now=T1) if i.sync_id == second.id]
    assert [c.kind for c in cards] == [InboxItemKind.BANK_BALANCE_DRIFT]


def test_a_first_sync_with_no_booked_stream_compares_with_the_anchor(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(streams=[], balance=Decimal("0.00"))) == "nothing_new"
    row = bank.sync(sync.id)
    assert (row.lines_total, row.balance_checked, row.error_code) == (0, True, E.BANK_NOTHING_NEW)


# --- Failures ---------------------------------------------------------------------------------


def _failed(bank, sync_id, code):
    row = bank.sync(sync_id)
    assert (row.status, row.error_code) == (SyncStatus.FAILED, code)
    assert row.finished_at is not None
    cards = [i for i in bank.read(bank.ws, review.inbox, now=T1) if i.sync_id == sync_id]
    assert [c.kind for c in cards] == [InboxItemKind.BANK_SYNC_FAILED]
    assert code in cards[0].reasoning
    return row


def test_an_approval_that_never_comes_times_out_and_ends_the_dialog(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(approval_polls=0, never_confirms=True)) == "failed"
    _failed(bank, sync.id, E.BANK_APPROVAL_TIMEOUT)
    assert bank.clients[0].calls[-1] == "close"
    assert bank.clock[0] >= bank.settings.sync_approval_timeout_s
    assert bank.connection_row().client_data is None


def test_an_account_the_login_does_not_hold_is_not_found(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(accounts=[fake_account(OTHER_IBAN)])) == "failed"
    _failed(bank, sync.id, E.BANK_ACCOUNT_NOT_FOUND)


def test_a_bank_without_a_readable_camt_format_is_unsupported(bank):
    sync = bank.start()
    script = _good(camt_formats=["urn:iso:std:iso:20022:tech:xsd:camt.054.001.02"])
    assert bank.job(sync.id, script) == "failed"
    _failed(bank, sync.id, E.BANK_UNSUPPORTED_FORMAT)


def test_a_9xxx_answer_is_refused_with_its_code_only(bank):
    sync = bank.start()
    assert bank.job(sync.id, _good(refuse_at={"accounts": "9942"})) == "failed"
    _failed(bank, sync.id, E.BANK_REFUSED)
    (card,) = [i for i in bank.read(bank.ws, review.inbox, now=T1) if i.sync_id == sync.id]
    assert "(bank.refused, Code der Bank 9942)" in card.reasoning  # tells PIN from approval
    (failed,) = bank.events(EventType.ACCOUNT_SYNC_FAILED)
    assert failed == {
        "sync_id": str(sync.id),
        "error_code": E.BANK_REFUSED,
        "response_code": "9942",
    }


def test_the_segments_reason_decides_over_the_general_codes(bank):
    """D151: the first real refusal's answer (HIRMG 9050, 9800, 9010; HIRMS 3079, 9078,
    9010) is kept as 9078, and the card names the product id, not the PIN."""
    sync = bank.start()
    script = _good(refuse_at={"open": "m9050,m9800,m9010,3079,9078,9010"})
    assert bank.job(sync.id, script) == "failed"
    _failed(bank, sync.id, E.BANK_REFUSED)
    (card,) = [i for i in bank.read(bank.ws, review.inbox, now=T1) if i.sync_id == sync.id]
    assert "Produkt-ID" in card.reasoning
    assert "(bank.refused, Code der Bank 9078)" in card.reasoning
    (failed,) = bank.events(EventType.ACCOUNT_SYNC_FAILED)
    assert failed["response_code"] == "9078"


def test_an_unreachable_bank_is_retried_twice_then_fails(bank):
    sync = bank.start()
    script = _good(fail_at={"open": FinTSConnectionError("down")})
    for attempt in (0, 1):
        with pytest.raises(RetryLater):
            bank.job(sync.id, script, attempt=attempt)
        assert bank.sync(sync.id).status is SyncStatus.QUEUED
    assert bank.job(sync.id, script, attempt=2) == "failed"
    _failed(bank, sync.id, E.BANK_UNREACHABLE)
    assert all(c.calls[-1] == "open" or "close" in c.calls for c in bank.clients)


def test_an_unreachable_bank_during_the_approval_wait_fails_without_a_retry(bank):
    """No needs_approval → queued move exists, and a retry would ask the phone again: the
    run fails as bank.unreachable at once, whatever the attempt (PR #160 review)."""
    sync = bank.start()
    script = _good(approval_polls=10)
    sleeps = [0]

    def cut_after_three_polls():
        sleeps[0] += 1
        if sleeps[0] == 4:
            script.fail_at["poll"] = FinTSConnectionError("down")

    assert bank.job(sync.id, script, on_sleep=cut_after_three_polls) == "failed"
    _failed(bank, sync.id, E.BANK_UNREACHABLE)
    calls = bank.clients[0].calls
    assert calls.count("poll") == 4
    assert calls[-1] == "close"
    (failed,) = bank.events(EventType.ACCOUNT_SYNC_FAILED)
    assert failed == {
        "sync_id": str(sync.id),
        "error_code": E.BANK_UNREACHABLE,
        "response_code": None,
    }


def test_a_state_whose_mac_fails_is_dropped_and_the_run_fails(bank):
    first = bank.start()
    bank.job(first.id, _good())
    with workspace_transaction(bank.engine, bank.ws) as conn:
        conn.execute(
            update(connection_table)
            .where(connection_table.c.account_id == bank.account.id)
            .values(client_data=b"tampered")
        )
    later = T1 + timedelta(minutes=1)
    second = bank.start(now=later)
    assert bank.job(second.id, _good(), at=later + timedelta(seconds=5)) == "skipped"
    row = bank.sync(second.id)
    assert (row.status, row.error_code) == (SyncStatus.FAILED, E.BANK_STATE_REJECTED)
    assert bank.connection_row().client_data is None
    assert len(bank.clients) == 1  # no dialog was opened with the rejected state


def test_a_job_whose_settings_lost_the_pin_records_not_configured(bank, tmp_path):
    sync = bank.start()
    bank.settings = _settings(tmp_path, fints_pin=None, fints_product_id=None)
    assert bank.job(sync.id, _good()) == "skipped"
    assert bank.sync(sync.id).error_code == E.BANK_NOT_CONFIGURED


# --- Approvals, restarts and the schedule ---------------------------------------------------


def test_a_redeploy_mid_approval_leaves_a_restartable_row(bank):
    sync = bank.start()
    plan = bank.run(bank.ws, service.begin_run, sync.id, attempt=0, settings=bank.settings, now=T1)
    bank.run(bank.ws, service.approval_needed, plan, now=T1)
    # The worker died while polling; the stalled job is retried (attempt 1).
    assert bank.job(sync.id, _good(), attempt=1) == "skipped"
    assert bank.sync(sync.id).status is SyncStatus.NEEDS_APPROVAL

    later = T1 + timedelta(seconds=30)
    restarted = bank.start(now=later)
    assert restarted.id == sync.id and restarted.status is SyncStatus.NEEDS_APPROVAL
    assert bank.job(sync.id, _good(approval_polls=0), at=later) == "done"


def test_a_restart_while_polling_stops_the_old_job(bank):
    sync = bank.start()
    restarts = []

    def restart():
        if not restarts:
            restarts.append(bank.start(now=T1 + timedelta(seconds=1)))

    outcome = bank.job(sync.id, _good(approval_polls=5), on_sleep=restart)
    assert outcome == "superseded"
    assert bank.clients[0].calls[-1] == "close"
    row = bank.sync(sync.id)
    assert row.status is SyncStatus.NEEDS_APPROVAL and row.started_at == T1 + timedelta(seconds=1)
    assert bank.job(sync.id, _good(approval_polls=0), at=T1 + timedelta(seconds=2)) == "done"


def test_a_scheduled_run_that_meets_a_demand_ends_the_dialog_and_waits(bank):
    with workspace_transaction(bank.engine, bank.ws) as conn:
        sync_id = conn.execute(
            insert(sync_table)
            .values(
                workspace_id=bank.ws,
                account_id=bank.account.id,
                connection_id=bank.connection.id,
                trigger="scheduled",
                status="queued",
                requested_from=date(2026, 4, 1),
                requested_to=date(2026, 11, 11),
                started_at=T0,
                created_at=T0,
                updated_at=T0,
            )
            .returning(sync_table.c.id)
        ).scalar_one()
    assert bank.job(sync_id, _good(approval_polls=0)) == "needs_approval"
    assert "poll" not in bank.clients[0].calls and bank.clients[0].calls[-1] == "close"
    assert bank.sync(sync_id).status is SyncStatus.NEEDS_APPROVAL

    restarted = bank.start(now=T1 + timedelta(seconds=10))  # the card's button
    assert restarted.id == sync_id and restarted.trigger.value == "manual"
    assert bank.job(sync_id, _good(approval_polls=0), at=T1 + timedelta(seconds=20)) == "done"


# --- The daily schedule (D120, step 2) -------------------------------------------------------

APPROVAL_SENTENCE = (
    "Der automatische Abruf braucht eine Freigabe: jetzt abrufen und in der S-pushTAN-App"
    " bestätigen"
)


def _approval_cards(bank, now=MORNING):
    return [
        i
        for i in bank.read(bank.ws, review.inbox, now=now)
        if i.kind is InboxItemKind.BANK_APPROVAL_NEEDED
    ]


def _bank_sync_jobs(bank):
    with workspace_transaction(bank.engine, bank.ws) as conn:
        return conn.execute(  # the job table has no RLS: this workspace's jobs only
            text(
                "SELECT args, lock FROM procrastinate_jobs"
                " WHERE task_name = 'bank_sync' AND args->>'workspace_id' = :ws"
            ),
            {"ws": str(bank.ws)},
        ).all()


def test_the_schedule_queues_one_run_per_connection_with_auto_sync_on(bank):
    other = bank.run(
        bank.ws,
        create_account,
        AccountCreate(
            label="Zweitkonto",
            bank="Sparkasse",
            iban=OTHER_IBAN,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 3, 31),
        ),
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )
    bank.run(
        bank.ws,
        service.create_connection,
        BankConnectionCreate(
            account_id=other.id, bank_code="10000000", fints_url=URL, login_name=LOGIN
        ),
        now=T0,
    )
    nothing = bank.schedule()
    assert (nothing.status, dict(nothing.outcomes)) == ("done", {})
    assert bank.rows(sync_table) == []

    bank.auto_sync()  # only the first account's switch
    result = bank.schedule()
    assert dict(result.outcomes) == {"queued": 1} and not result.codes
    (row,) = bank.rows(sync_table)
    assert (row.account_id, row.trigger, row.status, row.created_by) == (
        bank.account.id,
        "scheduled",
        "queued",
        None,
    )
    # The manual start's window rule: the day after the anchor up to today in Berlin.
    assert (row.requested_from, row.requested_to) == (date(2026, 4, 1), date(2026, 11, 12))
    assert [(j.args, j.lock) for j in _bank_sync_jobs(bank)] == [
        ({"workspace_id": str(bank.ws), "sync_id": str(row.id)}, f"account:{bank.account.id}")
    ]
    (started,) = bank.rows(
        event_table,
        event_table.c.type == EventType.ACCOUNT_SYNC_STARTED.value,
        event_table.c.entity_id == bank.account.id,
    )
    assert started.payload["trigger"] == "scheduled"
    assert (started.actor_type, started.actor_id) == ("system", schedule_job.SCHEDULE_TASK)


def test_a_live_run_is_not_queued_twice(bank):
    bank.auto_sync()
    bank.start()
    assert dict(bank.schedule().outcomes) == {"live": 1}
    assert len(bank.rows(sync_table)) == 1 and len(_bank_sync_jobs(bank)) == 1


def test_without_the_fints_secrets_the_schedule_queues_nothing(bank, tmp_path):
    bank.auto_sync()
    settings = _settings(tmp_path, fints_pin=None, fints_product_id=None)
    assert bank.schedule(settings=settings).status == "not_configured"
    assert bank.rows(sync_table) == [] and _bank_sync_jobs(bank) == []


def test_a_scheduled_demand_ends_the_dialog_becomes_a_card_and_the_cards_sync_clears_it(bank):
    bank.auto_sync()
    bank.schedule()
    (row,) = bank.rows(sync_table)
    outcome = bank.job(row.id, _good(approval_polls=0), at=MORNING + timedelta(seconds=5))
    assert outcome == "needs_approval"
    calls = bank.clients[0].calls
    assert "poll" not in calls and calls[-1] == "close"  # one request, the dialog ended
    assert bank.clock[0] == 0  # never waited for the phone
    waiting = bank.sync(row.id)
    assert (waiting.status, waiting.trigger) == (SyncStatus.NEEDS_APPROVAL, SyncTrigger.SCHEDULED)
    (approval,) = bank.events(EventType.ACCOUNT_SYNC_APPROVAL_NEEDED)
    assert approval == {"sync_id": str(row.id), "trigger": "scheduled"}

    (card,) = _approval_cards(bank)
    assert (card.account_id, card.sync_id) == (bank.account.id, row.id)
    assert APPROVAL_SENTENCE in card.reasoning

    # The card's button: a manual start restarts the row, and its job polls the approval.
    later = MORNING + timedelta(hours=1)
    restarted = bank.start(now=later)
    assert (restarted.id, restarted.trigger) == (row.id, SyncTrigger.MANUAL)
    assert _approval_cards(bank, later) == []
    assert bank.job(row.id, _good(approval_polls=1), at=later + timedelta(seconds=5)) == "done"
    assert "poll" in bank.clients[1].calls
    assert bank.connection_row().sca_confirmed_at == later + timedelta(seconds=5)
    assert _approval_cards(bank, later) == []


def test_the_next_morning_leaves_the_approval_alone_until_a_person_acts(bank):
    bank.auto_sync()
    bank.schedule()
    (row,) = bank.rows(sync_table)
    bank.job(row.id, _good(approval_polls=0), at=MORNING + timedelta(seconds=5))

    next_morning = MORNING + timedelta(days=1)
    assert dict(bank.schedule(at=next_morning).outcomes) == {"approval_needed": 1}
    assert len(bank.rows(sync_table)) == 1 and len(_bank_sync_jobs(bank)) == 1
    assert len(bank.clients) == 1  # no second dialog, so no second request on the phone
    assert len(_approval_cards(bank, next_morning)) == 1

    # A person acts: the manual sync is approved and imports; the schedule goes on.
    later = next_morning + timedelta(hours=1)
    bank.start(now=later)
    assert bank.job(row.id, _good(approval_polls=0), at=later + timedelta(seconds=5)) == "done"
    third = MORNING + timedelta(days=2)
    assert dict(bank.schedule(at=third).outcomes) == {"queued": 1}
    (new,) = bank.rows(sync_table, sync_table.c.status == "queued")
    assert new.id != row.id and new.trigger == "scheduled"
    # The stored state lets the bank serve the read without a request (Q-O35).
    assert bank.job(new.id, _good(), at=third + timedelta(seconds=5)) == "nothing_new"
    assert "poll" not in bank.clients[-1].calls


def test_a_refused_run_is_not_tried_again_by_the_schedule(bank):
    bank.auto_sync()
    sync = bank.start()
    assert bank.job(sync.id, _good(refuse_at={"accounts": "9942"})) == "failed"
    assert dict(bank.schedule().outcomes) == {"failed": 1}
    assert len(bank.rows(sync_table)) == 1  # a wrong PIN is not tried on the next morning


def test_an_unreachable_bank_is_tried_again_the_next_morning(bank):
    bank.auto_sync()
    sync = bank.start()
    script = _good(fail_at={"open": FinTSConnectionError("down")})
    assert bank.job(sync.id, script, attempt=job.MAX_UNREACHABLE_RETRIES) == "failed"
    assert dict(bank.schedule().outcomes) == {"queued": 1}


def _unreachable(bank, at):
    """A manual start whose bank is down on its last attempt: failed bank.unreachable."""
    sync = bank.start(now=at)
    script = _good(fail_at={"open": FinTSConnectionError("down")})
    outcome = bank.job(
        sync.id, script, attempt=job.MAX_UNREACHABLE_RETRIES, at=at + timedelta(seconds=5)
    )
    assert outcome == "failed" and bank.sync(sync.id).error_code == E.BANK_UNREACHABLE


def test_an_outage_after_a_refusal_still_waits_for_a_person(bank):
    # A wrong PIN, pressed again while the bank was down: the outage must not hide the
    # refusal, or the next morning would try the PIN unattended (PR #200 review, F1).
    bank.auto_sync()
    refused = bank.start()
    assert bank.job(refused.id, _good(refuse_at={"accounts": "9942"})) == "failed"
    _unreachable(bank, T1 + timedelta(minutes=1))
    assert dict(bank.schedule().outcomes) == {"failed": 1}
    assert len(bank.rows(sync_table)) == 2


def test_an_outage_after_a_good_run_is_tried_again_the_next_morning(bank):
    bank.auto_sync()
    refused = bank.start()
    assert bank.job(refused.id, _good(refuse_at={"accounts": "9942"})) == "failed"
    fixed = bank.start(now=T1 + timedelta(minutes=1))
    assert bank.job(fixed.id, _good(), at=T1 + timedelta(minutes=2)) == "done"
    _unreachable(bank, T1 + timedelta(minutes=3))
    assert dict(bank.schedule().outcomes) == {"queued": 1}


def test_a_switched_off_connection_is_left_alone(bank):
    bank.auto_sync()
    bank.auto_sync(on=False)
    assert dict(bank.schedule().outcomes) == {}
    assert bank.connection_row().auto_sync is False


# --- Secrets ----------------------------------------------------------------------------------


def test_no_secret_reaches_a_sync_row_an_event_or_a_document_row(bank):
    sync = bank.start()
    bank.job(sync.id, _good(approval_polls=0))
    second = bank.start(now=T1 + timedelta(minutes=1))
    bank.job(second.id, _good(refuse_at={"balance": "9800"}), at=T1 + timedelta(minutes=2))

    rows = [
        *bank.rows(sync_table),
        *bank.rows(event_table),
        *bank.rows(document_table),
        *bank.rows(import_table),
    ]
    text_of = repr([tuple(r) for r in rows])
    assert not [s for s in SECRETS if s in text_of]
    connection = bank.connection_row()
    assert not [s for s in (PIN, PRODUCT_ID) if s.encode() in bytes(connection.client_data)]
    with workspace_transaction(bank.engine, bank.ws) as conn:
        args = conn.execute(text("SELECT args FROM procrastinate_jobs")).scalars().all()
    assert not [s for s in SECRETS if s in repr(args)]
    assert (
        bank.read(
            bank.ws,
            lambda c: c.execute(select(func.count()).select_from(sync_table)).scalar_one(),
        )
        == 2
    )


# --- The bank's PDF statements (D154, step 2b) -------------------------------------------------

PDF_1 = b"%PDF-1.4\n% invented statement one\n%%EOF\n"
PDF_2 = b"%PDF-1.4\n% invented statement two\n%%EOF\n"


def _with_statements(**overrides) -> Script:
    statements = {
        (2026, 1): [base64.b64encode(PDF_1)],
        (2026, 2): [base64.b64encode(PDF_2)],
    }
    return _good(**({"statements_offered": True, "statements": statements} | overrides))


def _fetch_statements(bank, on=True):
    return bank.run(
        bank.ws,
        service.update_connection,
        bank.account.id,
        BankConnectionPatch(fetch_statements=on),
        now=T0,
    )


def test_the_statement_switch_starts_off_and_is_recorded_like_auto_sync(bank):
    assert bank.connection.fetch_statements is False
    assert _fetch_statements(bank).fetch_statements is True
    updated = bank.events(EventType.ACCOUNT_CONNECTION_UPDATED)[-1]
    assert updated["changed"] == ["fetch_statements"]
    assert updated["fetch_statements"] is True


def test_with_the_switch_off_no_statement_is_asked_for(bank):
    sync = bank.start()
    assert bank.job(sync.id, _with_statements()) == "done"
    assert "statement" not in bank.clients[0].calls
    assert bank.rows(statement_table) == []


def test_with_the_switch_on_the_statements_are_stored_after_the_camt_part(bank):
    """D154: the camt result is the same as without the switch; each statement is a typed
    document of its year with one bank_statement row and one event, and is never imported,
    classified or read (no statement import, no proposal)."""
    _fetch_statements(bank)
    sync = bank.start()
    assert bank.job(sync.id, _with_statements()) == "done"
    done = bank.sync(sync.id)
    assert (done.status, done.lines_new) == (SyncStatus.DONE, 22)

    client = bank.clients[0]
    assert client.statement_requests == [(2026, 1, None), (2026, 2, None), (2026, 3, None)]
    calls = client.calls
    assert calls.index("balance") < calls.index("statement") < calls.index("close")

    rows = sorted(bank.rows(statement_table), key=lambda r: r.number)
    assert [(r.year, r.number, r.sync_id) for r in rows] == [
        (2026, 1, sync.id),
        (2026, 2, sync.id),
    ]
    for row, pdf in zip(rows, (PDF_1, PDF_2), strict=True):
        (document,) = bank.rows(document_table, document_table.c.id == row.document_id)
        assert document.source == DocumentSource.BANK_SYNC.value
        assert (document.type, document.title, document.tax_year) == (
            "statement",
            f"Kontoauszug {row.number}/2026",
            2026,
        )
        assert document.original_filename == f"Kontoauszug-2026-{row.number}.pdf"
        assert bank.rows(import_table, import_table.c.document_id == row.document_id) == []
        assert bank.rows(proposal_table, proposal_table.c.document_id == row.document_id) == []
        key = f"{bank.ws}/sha256/{bytes(document.file_sha256).hex()}"
        assert bank.deps.blob_store.get(key) == pdf
    fetched = bank.events(EventType.ACCOUNT_STATEMENT_FETCHED)
    assert sorted((e["year"], e["number"]) for e in fetched) == [(2026, 1), (2026, 2)]
    assert all(e["sync_id"] == str(sync.id) for e in fetched)
    listed = bank.read(bank.ws, service.list_statements, bank.account.id)
    assert [(s.year, s.number) for s in listed] == [(2026, 2), (2026, 1)]


def test_the_next_run_asks_after_the_highest_stored_number(bank):
    _fetch_statements(bank)
    first = bank.start()
    bank.job(first.id, _with_statements())
    later = T1 + timedelta(minutes=1)
    second = bank.start(now=later)
    assert bank.job(second.id, _with_statements(), at=later) == "nothing_new"
    assert bank.clients[1].statement_requests == [(2026, 3, None)]
    assert len(bank.rows(statement_table)) == 2


def test_a_statement_failure_leaves_the_run_done_and_shows_no_card(bank):
    _fetch_statements(bank)
    sync = bank.start()
    script = _with_statements(statement_fail_at={(2026, 1): FinTSConnectionError("down")})
    assert bank.job(sync.id, script) == "done"
    assert bank.sync(sync.id).lines_new == 22
    assert [i for i in bank.read(bank.ws, review.inbox, now=T1) if i.sync_id == sync.id] == []
    assert bank.events(EventType.ACCOUNT_SYNC_FAILED) == []
    assert bank.rows(statement_table) == []


def test_a_refused_number_that_ends_the_dialog_leaves_the_run_done(bank):
    _fetch_statements(bank)
    sync = bank.start()
    script = _with_statements(statement_codes={(2026, 3): "m9050,m9800,9210"})
    assert bank.job(sync.id, script) == "done"
    assert len(bank.rows(statement_table)) == 2
    assert bank.events(EventType.ACCOUNT_SYNC_FAILED) == []


def test_a_lock_code_on_a_statement_fails_the_run_as_d151_says(bank):
    _fetch_statements(bank)
    sync = bank.start()
    script = _with_statements(statement_codes={(2026, 1): "m9050,9930"}, statements={})
    assert bank.job(sync.id, script) == "failed"
    _failed(bank, sync.id, E.BANK_REFUSED)
    (failed,) = bank.events(EventType.ACCOUNT_SYNC_FAILED)
    assert failed["response_code"] == "9930"
    assert bank.rows(import_table, import_table.c.account_id == bank.account.id) == []


# --- 14 Nov 2026: the bank refuses the statement fetch for lack of permission (9370) --------

NOT_PERMITTED = "m9050,9010,9370"  # the first real fetch's answer, as the trace logged it


def _refusing(**overrides) -> Script:
    return _with_statements(**({"statements": {}, "statement_refusal": NOT_PERMITTED} | overrides))


def _refused_cards(bank, at):
    return [
        i
        for i in bank.read(bank.ws, review.inbox, now=at)
        if i.kind is InboxItemKind.BANK_STATEMENTS_REFUSED
    ]


def _refusal(bank):
    return bank.read(bank.ws, service.get_connection, bank.account.id).statements_refusal


def _sync_at(bank, script, at):
    sync = bank.start(now=at)
    outcome = bank.job(sync.id, script, at=at)
    return sync, outcome


def test_a_permission_refusal_is_recorded_once_shown_and_never_fails_the_run(bank):
    _fetch_statements(bank)
    first, outcome = _sync_at(bank, _refusing(), T1)
    assert outcome == "done"  # the camt part decides the run
    assert bank.sync(first.id).lines_new == 22
    assert bank.events(EventType.ACCOUNT_SYNC_FAILED) == []
    assert bank.clients[0].statement_requests == [(2026, 1, None)]

    later = T1 + timedelta(days=1)
    _, outcome = _sync_at(bank, _refusing(), later)
    assert outcome == "nothing_new"
    assert bank.clients[1].statement_requests == [(2026, 1, None)]  # asked again

    (refused,) = bank.events(EventType.ACCOUNT_STATEMENTS_REFUSED)  # no daily duplicate
    assert refused == {
        "connection_id": str(bank.connection.id),
        "sync_id": str(first.id),
        "code": "9370",
        "year": 2026,
        "number": 1,
    }
    refusal = _refusal(bank)
    assert (refusal.code, refusal.year, refusal.number, refusal.at) == ("9370", 2026, 1, T1)
    (card,) = _refused_cards(bank, later)
    assert card.account_id == bank.account.id and card.sync_id is None
    assert card.reasoning == (
        "Die Testbank erlaubt diesem Online-Banking-Zugang den Abruf der Kontoauszüge als PDF"
        " nicht (Code 9370: Signaturberechtigung reicht nicht aus)."
    )
    assert "(HKEKP)" in card.question
    assert "„Kontoauszüge als PDF abrufen“ ausschalten" in card.question
    assert bank.rows(statement_table) == []


def test_a_later_answer_ends_the_refusal_and_a_new_refusal_is_recorded_again(bank):
    _fetch_statements(bank)
    _sync_at(bank, _refusing(), T1)
    second = T1 + timedelta(days=1)
    sync, outcome = _sync_at(bank, _with_statements(), second)
    assert outcome == "nothing_new"
    assert len(bank.rows(statement_table)) == 2
    assert _refusal(bank) is None
    assert _refused_cards(bank, second) == []
    (answered,) = bank.events(EventType.ACCOUNT_STATEMENTS_ANSWERED)
    assert answered == {"connection_id": str(bank.connection.id), "sync_id": str(sync.id)}

    third = second + timedelta(days=1)
    _sync_at(bank, _refusing(), third)
    assert len(bank.events(EventType.ACCOUNT_STATEMENTS_REFUSED)) == 2
    assert _refusal(bank).number == 3  # 1 and 2 are stored
    assert len(_refused_cards(bank, third)) == 1


def test_a_missing_number_after_a_refusal_ends_it_too(bank):
    """Once permitted, the bank may still have no statement for the number asked (3010): it
    answered the order, so the notice that it is not permitted ends."""
    _fetch_statements(bank)
    _sync_at(bank, _refusing(), T1)
    later = T1 + timedelta(days=1)
    _sync_at(bank, _refusing(statement_refusal="3010"), later)
    assert _refusal(bank) is None
    assert len(bank.events(EventType.ACCOUNT_STATEMENTS_ANSWERED)) == 1


def test_no_answer_event_without_an_earlier_refusal(bank):
    _fetch_statements(bank)
    _sync_at(bank, _with_statements(), T1)
    assert bank.events(EventType.ACCOUNT_STATEMENTS_ANSWERED) == []
    assert bank.events(EventType.ACCOUNT_STATEMENTS_REFUSED) == []


def test_switching_the_fetch_off_hides_the_refusal(bank):
    _fetch_statements(bank)
    _sync_at(bank, _refusing(), T1)
    assert _refusal(bank) is not None
    _fetch_statements(bank, on=False)
    assert _refusal(bank) is None
    assert _refused_cards(bank, T1 + timedelta(minutes=1)) == []
