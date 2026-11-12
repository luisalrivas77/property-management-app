"""jobs/notify.py, the daily reminder digest (docs/phases/phase-5.md D123, §12.1): one digest
per enrolled member through a fake mailer, the `notification` rows it records, the same day's
second run, the retries of a failed send, the switch and the settings that keep it off, what
the mail never carries, and the lock that keeps two runs from sending one digest twice.
Synthetic data only (World's flat, an invented tenant, invented members)."""

import logging
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import _admin
from db.matching_support import TENANT_IBAN, World
from sqlalchemy import func, select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import notification as notification_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.notify import lock_key, run_send_reminders
from hausbuch.rules.reminders import due_notifications
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.services import allocations
from hausbuch.services.mail import MailSendError, OutgoingMail
from hausbuch.services.reminders import list_reminders
from hausbuch.services.workspace_settings import update_settings
from hausbuch.settings import Settings
from hausbuch.storage import LocalBlobStore

pytestmark = pytest.mark.db

TODAY = date(2026, 5, 10)
NOW = datetime(2026, 5, 10, 6, 0, tzinfo=UTC)
ORIGIN = "https://testserver"
TENANT_NAME = "Erika Mustermieterin"
PROPERTY_LABEL = "Wohnung A"


class FakeMailer:
    """Records every mail; fails with `fail` (a code) when set; can hold a send until
    `release` is set, after signalling `entered`."""

    def __init__(self, fail: str | None = None, hold: bool = False) -> None:
        self.fail = fail
        self.sent: list[OutgoingMail] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.hold = hold

    def send(self, mail: OutgoingMail) -> None:
        if self.hold:
            self.entered.set()
            assert self.release.wait(30), "the test never released the mail"
        if self.fail is not None:
            raise MailSendError(self.fail)
        self.sent.append(mail)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": bytes(range(32)),
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": ORIGIN,
        "blob_store": "local",
        "blob_root": tmp_path / "blobs",
    } | overrides
    return Settings(**values)


def _deps(app_engine, tmp_path, at: datetime = NOW, **settings) -> JobDeps:
    return JobDeps(
        engine=app_engine,
        settings=_settings(tmp_path, **settings),
        blob_store=LocalBlobStore(tmp_path / "blobs"),
        model_client=None,
        clock=lambda: at,
    )


def _member(dbname, ws: uuid.UUID, *, enrolled: bool = True) -> tuple[uuid.UUID, str]:
    """An app_user (emails are unique across workspaces) and, when enrolled, a passkey."""
    user_id = uuid.uuid4()
    email = f"member-{user_id.hex[:12]}@example.org"
    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name) VALUES (%s, %s, %s, %s)",
            (user_id, ws, email, "Mitglied"),
        )
        if enrolled:
            conn.execute(
                "INSERT INTO auth_passkey (workspace_id, user_id, credential_id, public_key,"
                " backup_eligible, backed_up, label) VALUES (%s, %s, %s, %s, false, false, %s)",
                (ws, user_id, uuid.uuid4().bytes, b"\x01", "Testgerät"),
            )
    return user_id, email


def _book_rent(world: World, tenancy: uuid.UUID, month: date) -> None:
    tx = world.line(month.replace(day=3), "1050.00", iban=TENANT_IBAN)
    end = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    answer = AllocationSet(
        transaction_id=tx,
        allocations=[
            AllocationDraft(
                amount=Decimal("1050.00"),
                category=AllocationCategory.RENT_COLD,
                property_id=world.property_id,
                unit_id=world.unit_id,
                tenancy_id=tenancy,
                service_period_start=month,
                service_period_end=end,
            )
        ],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )
    world.cmd(allocations.record_set, answer, confirm=True, auto=False)


@pytest.fixture
def setup(app_engine, fresh_workspaces, dbname, tmp_path):
    """World's flat labelled "Wohnung A" (its address stays Lindenstraße 12, 10115 Berlin), a
    tenancy of an invented tenant at 1,050.00 from January 2026 whose March rent is missing,
    two enrolled members, one invited member who never enrolled, and the switch on."""
    world = World(app_engine, fresh_workspaces[0])
    with workspace_transaction(app_engine, world.ws) as conn:
        conn.execute(
            update(property_table)
            .where(property_table.c.id == world.property_id)
            .values(label=PROPERTY_LABEL)
        )
    tenancy = world.tenancy(name=TENANT_NAME, start=date(2026, 1, 1))
    for month in (1, 2, 4):
        _book_rent(world, tenancy, date(2026, month, 1))
    with workspace_transaction(app_engine, world.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == world.import_id)
            .values(first_booking_on=date(2026, 1, 1), last_booking_on=date(2026, 4, 30))
        )
    members = [_member(dbname, world.ws), _member(dbname, world.ws)]
    pending = _member(dbname, world.ws, enrolled=False)
    world.cmd(update_settings, reminder_emails=True)
    return world, tenancy, members, pending


def _rows(world: World) -> list:
    c = notification_table.c
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(
                c.reminder_id,
                c.lead_days,
                c.recipient_user_id,
                c.attempt,
                c.status,
                c.error_code,
                c.channel,
            ).order_by(c.recipient_user_id, c.reminder_id, c.attempt)
        ).all()


def _sent_events(world: World) -> list:
    """The workspace.notification_sent events (§8): type, entity, actor and payload."""
    c = event_table.c
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(c.entity_type, c.entity_id, c.actor_type, c.actor_id, c.payload)
            .where(c.type == "workspace.notification_sent")
            .order_by(c.seq)
        ).all()


def _expected_due(world: World, members, today: date = TODAY):
    with workspace_transaction(world.engine, world.ws) as conn:
        reminders = list_reminders(conn, today)
    return due_notifications(reminders, [], today, [m for m, _ in members])


def _run(world, app_engine, tmp_path, mailer, at: datetime = NOW, **kw):
    return run_send_reminders(
        _deps(app_engine, tmp_path, at=at),
        mailer=mailer,
        workspace_ids=kw.pop("workspace_ids", [world.ws]),
        **kw,
    )


# --- (a) one digest per member, recorded as sent ---------------------------------------------


def test_one_digest_per_enrolled_member_and_its_sent_rows(setup, app_engine, tmp_path):
    world, tenancy, members, pending = setup
    expected = _expected_due(world, members)
    ids = {d.reminder_id for d in expected}
    assert f"tenancy.rent_missing:{tenancy}:2026-03-08" in ids
    assert f"checklist.due:{world.property_id}:2026-01-15" in ids

    mailer = FakeMailer()
    # The task's own path: every provisioned workspace (other tests' workspaces too).
    result = run_send_reminders(_deps(app_engine, tmp_path), mailer=mailer)
    mine = [m for m in mailer.sent if m.to in {email for _, email in members}]
    assert sorted(m.to for m in mine) == sorted(email for _, email in members)
    assert pending[1] not in {m.to for m in mailer.sent}  # never enrolled: no mail
    assert result.status == "done" and result.failed_mails == 0
    for mail in mine:
        assert mail.subject == "Hausbuch: Erinnerungen"
        assert "Miete WE 3 für März 2026 fehlt" in mail.text
        assert f"erwartet ({PROPERTY_LABEL})" in mail.text  # the checklist line
        assert f"{ORIGIN}/" in mail.text and f'href="{ORIGIN}/"' in mail.html

    rows = _rows(world)
    assert len(rows) == len(expected)
    assert {(r.reminder_id, r.lead_days, r.recipient_user_id) for r in rows} == {
        (d.reminder_id, d.lead_days, d.recipient_user_id) for d in expected
    }
    assert {(r.status, r.attempt, r.error_code, r.channel) for r in rows} == {
        ("sent", 1, None, "email")
    }
    assert pending[0] not in {r.recipient_user_id for r in rows}

    # §8: one workspace.notification_sent per item and lead, counting the members it reached;
    # never an address or a member id.
    events = _sent_events(world)
    pairs = {(d.reminder_id, d.lead_days) for d in expected}
    assert sorted((e.payload["reminder_id"], e.payload["lead_days"]) for e in events) == sorted(
        pairs
    )
    for e in events:
        assert e.payload == {
            "reminder_id": e.payload["reminder_id"],
            "lead_days": e.payload["lead_days"],
            "channel": "email",
            "recipient_count": len(members),
        }
        assert (e.entity_type, e.entity_id) == ("workspace", world.ws)
        assert (e.actor_type, e.actor_id) == ("system", "send_reminders")
        for user_id, email in members:
            assert email not in str(e.payload) and str(user_id) not in str(e.payload)


# --- (b) the same day's second run sends nothing ---------------------------------------------


def test_the_same_days_second_run_sends_nothing(setup, app_engine, tmp_path):
    world, _, members, _ = setup
    first = FakeMailer()
    _run(world, app_engine, tmp_path, first)
    assert len(first.sent) == len(members)
    rows = _rows(world)

    second = FakeMailer()
    result = _run(world, app_engine, tmp_path, second, at=NOW + timedelta(hours=5))
    assert second.sent == [] and result.mails == 0 and result.items == 0
    assert _rows(world) == rows
    # Nor the next day: no lead was reached since.
    _run(world, app_engine, tmp_path, second, at=NOW + timedelta(days=1))
    assert second.sent == [] and _rows(world) == rows


# --- (c) a failed send is retried the next day, up to three attempts -------------------------


def test_a_failed_send_is_retried_next_day_and_given_up_after_three(setup, app_engine, tmp_path):
    world, tenancy, members, _ = setup
    rent = f"tenancy.rent_missing:{tenancy}:2026-03-08"
    failing = FakeMailer(fail="http_503")
    for day in range(3):
        result = _run(world, app_engine, tmp_path, failing, at=NOW + timedelta(days=day))
        assert result.failed_mails == len(members) and result.codes == {"http_503": 2}
        attempts = sorted(
            (r.recipient_user_id, r.attempt, r.status, r.error_code)
            for r in _rows(world)
            if r.reminder_id == rent
        )
        assert attempts == sorted(
            (m, a, "failed", "http_503") for m, _ in members for a in range(1, day + 2)
        )
        assert _sent_events(world) == []  # a failed send is no notification_sent event
    # Given up: a working mailer on the fourth day sends nothing for it.
    working = FakeMailer()
    _run(world, app_engine, tmp_path, working, at=NOW + timedelta(days=3))
    assert all("Miete WE 3" not in m.text for m in working.sent)
    assert max(r.attempt for r in _rows(world) if r.reminder_id == rent) == 3
    assert not [r for r in _rows(world) if r.status == "sent" and r.reminder_id == rent]
    # Only a sent row is reported: the failed attempts wrote no notification_sent event.
    assert not [e for e in _sent_events(world) if e.payload["reminder_id"] == rent]


def test_a_retry_that_succeeds_records_its_attempt(setup, app_engine, tmp_path):
    world, tenancy, members, _ = setup
    rent = f"tenancy.rent_missing:{tenancy}:2026-03-08"
    _run(world, app_engine, tmp_path, FakeMailer(fail="exc:TimeoutError"))
    working = FakeMailer()
    _run(world, app_engine, tmp_path, working, at=NOW + timedelta(days=1))
    assert sorted(m.to for m in working.sent) == sorted(e for _, e in members)
    mine = sorted(
        (r.attempt, r.status, r.error_code) for r in _rows(world) if r.reminder_id == rent
    )
    assert mine == [(1, "failed", "exc:TimeoutError")] * 2 + [(2, "sent", None)] * 2
    later = FakeMailer()
    _run(world, app_engine, tmp_path, later, at=NOW + timedelta(days=2))
    assert later.sent == []


# --- (d) off: the switch, or no TEM settings -------------------------------------------------


def test_the_switch_off_sends_nothing(setup, app_engine, tmp_path):
    world, _, _, _ = setup
    world.cmd(update_settings, reminder_emails=False)
    mailer = FakeMailer()
    result = _run(world, app_engine, tmp_path, mailer)
    assert mailer.sent == [] and _rows(world) == []
    assert result.switched_off == 1 and result.workspaces == 0


def test_without_tem_settings_the_run_is_inert(setup, app_engine, tmp_path, caplog):
    world, _, _, _ = setup
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.notify"):
        result = run_send_reminders(_deps(app_engine, tmp_path), workspace_ids=[world.ws])
    assert result.status == "not_configured"
    assert _rows(world) == []
    lines = [r for r in caplog.records if r.name == "hausbuch.jobs.notify"]
    assert len(lines) == 1 and lines[0].fields["status"] == "not_configured"


# --- (e) no amount, no tenant name, no address -----------------------------------------------


def test_the_mail_carries_no_amount_tenant_or_address(setup, app_engine, tmp_path, caplog):
    world, _, members, _ = setup
    mailer = FakeMailer()
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.notify"):
        _run(world, app_engine, tmp_path, mailer)
    assert [r for r in caplog.records if r.name == "hausbuch.jobs.notify"]
    assert len(mailer.sent) == len(members)
    for mail in mailer.sent:
        for body in (mail.subject, mail.text, mail.html):
            assert TENANT_NAME not in body
            assert "Mustermieterin" not in body
            for amount in ("1050", "1.050", "230,00", "230.00", "€", "EUR"):
                assert amount not in body
            # The address fields (D123): the label as typed may appear, the address never.
            for part in ("Lindenstraße", "10115", "Berlin"):
                assert part not in body
        assert "WE 3" in mail.text and PROPERTY_LABEL in mail.text
    # The log names counts and codes, never an address.
    for record in caplog.records:
        if record.name == "hausbuch.jobs.notify":
            assert all(e not in str(record.__dict__) for _, e in members)


# --- (f) overlapping runs --------------------------------------------------------------------


def test_a_run_skips_a_workspace_another_run_holds(setup, app_engine, tmp_path):
    world, _, members, _ = setup
    with app_engine.begin() as other:
        other.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key(world.ws), 0)))
        )
        mailer = FakeMailer()
        result = _run(world, app_engine, tmp_path, mailer)
        assert result.busy == 1 and mailer.sent == [] and _rows(world) == []
    # Released: the next run sends.
    mailer = FakeMailer()
    _run(world, app_engine, tmp_path, mailer)
    assert len(mailer.sent) == len(members)


def test_two_overlapping_runs_send_each_digest_once(setup, app_engine, tmp_path):
    world, _, members, _ = setup
    slow = FakeMailer(hold=True)
    results = {}

    def first_run():
        results["first"] = _run(world, app_engine, tmp_path, slow)

    thread = threading.Thread(target=first_run)
    thread.start()
    try:
        assert slow.entered.wait(30), "the first run never reached the mailer"
        # The first run is inside its first send, holding the workspace's lock.
        fast = FakeMailer()
        second = _run(world, app_engine, tmp_path, fast)
        assert second.busy == 1 and fast.sent == []
    finally:
        slow.release.set()
        thread.join(60)
    assert not thread.is_alive()
    assert results["first"].mails == len(members)
    assert sorted(m.to for m in slow.sent) == sorted(e for _, e in members)
    # And a third run after both: everything is recorded, nothing is sent again.
    third = FakeMailer()
    _run(world, app_engine, tmp_path, third)
    assert third.sent == []
    rows = _rows(world)
    assert len(rows) == len({(r.reminder_id, r.lead_days, r.recipient_user_id) for r in rows})
