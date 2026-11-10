"""Phase 5 step 0 schema guards (docs/phases/phase-5.md §8, §9, D119, D121, D122, D126): no
secret in a model or an event payload, the login only masked, https only, the Grundsteuer
schedule's checks, a rejected mail fetches nothing, a reminder's id shape."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hausbuch.schemas import bank_sync as B
from hausbuch.schemas import inbound_mail as M
from hausbuch.schemas import reminders as R
from hausbuch.schemas.events import (
    EVENT_CATALOG,
    BankConnectionRecorded,
    BankSyncCompleted,
    BankSyncFailed,
    EventType,
    TaxInputRecorded,
)
from hausbuch.schemas.tax import (
    DOCUMENT_REQUIRED,
    YEAR_SCOPED,
    GrundsteuerSchedule,
    TaxInputCreate,
    TaxInputKind,
)

# D119: a field named like a credential is never part of a phase 5 model or payload.
SECRET_SHAPED = ("pin", "tan", "password", "token", "secret", "imap_user", "product")
PHASE5_MODELS = [
    B.BankConnectionCreate,
    B.BankConnectionPatch,
    B.BankConnection,
    B.BankSync,
    M.InboundMail,
    M.MailDecision,
    R.Reminder,
    R.ReminderAckCreate,
    R.NotificationDue,
]
PHASE5_EVENTS = [
    EventType.ACCOUNT_CONNECTION_CREATED,
    EventType.ACCOUNT_CONNECTION_UPDATED,
    EventType.ACCOUNT_SYNC_STARTED,
    EventType.ACCOUNT_SYNC_APPROVAL_NEEDED,
    EventType.ACCOUNT_SYNC_COMPLETED,
    EventType.ACCOUNT_SYNC_FAILED,
    EventType.DOCUMENT_RECEIVED_BY_EMAIL,
    EventType.WORKSPACE_REMINDER_ACKNOWLEDGED,
    EventType.WORKSPACE_NOTIFICATION_SENT,
]


def _secret_shaped(name: str) -> bool:
    # tan_mechanism is the bank's security-function code (e.g. 946), not a TAN
    return name != "tan_mechanism" and any(word in name for word in SECRET_SHAPED)


@pytest.mark.parametrize(
    "model", PHASE5_MODELS + [EVENT_CATALOG[e].payload for e in PHASE5_EVENTS], ids=str
)
def test_no_secret_shaped_field(model):
    assert not [name for name in model.model_fields if _secret_shaped(name)]


def test_no_login_and_no_url_in_any_read_model_or_payload():
    for model in [B.BankConnection, BankConnectionRecorded]:
        assert "login_name" not in model.model_fields
        assert "client_data" not in model.model_fields
    assert "fints_url" not in BankConnectionRecorded.model_fields


def test_phase5_events_are_on_existing_entities_and_forbid_an_effective_date():
    for event_type in PHASE5_EVENTS:
        assert event_type.value.split(".")[0] in {"account", "document", "workspace"}
        assert EVENT_CATALOG[event_type].effective_date == "forbidden"


@pytest.mark.parametrize(
    ("login", "masked"),
    [("anmeldename42", "an***42"), ("abcdef", "ab***ef"), ("abcde", "***"), ("x", "***")],
)
def test_mask_login(login, masked):
    assert B.mask_login(login) == masked
    assert login not in masked or len(login) < 3


def test_connection_create_is_https_only_and_never_echoes_the_url():
    base = {"account_id": uuid.uuid4(), "bank_code": "12345678", "login_name": "anmeldename42"}
    ok = B.BankConnectionCreate.model_validate(base | {"fints_url": "https://banking.example/f"})
    assert ok.fints_url == "https://banking.example/f"
    with pytest.raises(ValidationError, match=B.BANK_URL_NOT_HTTPS) as caught:
        B.BankConnectionCreate.model_validate(base | {"fints_url": "http://banking.example/f"})
    assert "banking.example" not in str(caught.value).split("input_value")[0]
    with pytest.raises(ValidationError):
        B.BankConnectionCreate.model_validate(base | {"bank_code": "1234567", "fints_url": "x"})
    with pytest.raises(ValidationError, match=B.BANK_URL_NOT_HTTPS):
        B.BankConnectionPatch.model_validate({"fints_url": "ftp://banking.example"})
    with pytest.raises(ValidationError, match=B.BANK_URL_NOT_HTTPS):  # the CHECK's LIKE is exact
        B.BankConnectionCreate.model_validate(base | {"fints_url": "HTTPS://banking.example/f"})
    assert B.BankConnectionPatch.model_validate({"auto_sync": True}).changes() == {
        "auto_sync": True
    }


def test_sync_completed_import_only_for_done():
    sync_id = uuid.uuid4()
    common = {"sync_id": sync_id, "lines_total": 3, "balance_checked": True}
    BankSyncCompleted.model_validate(
        common | {"status": "done", "import_id": uuid.uuid4(), "lines_new": 1}
    )
    BankSyncCompleted.model_validate(common | {"status": "nothing_new", "lines_new": 0})
    with pytest.raises(ValidationError, match="import_id"):
        BankSyncCompleted.model_validate(common | {"status": "done", "lines_new": 1})
    with pytest.raises(ValidationError, match="nothing_new"):
        BankSyncCompleted.model_validate(common | {"status": "nothing_new", "lines_new": 2})
    with pytest.raises(ValidationError):
        BankSyncCompleted.model_validate(common | {"status": "failed", "lines_new": 0})
    BankSyncFailed(sync_id=sync_id, error_code="bank.refused")
    with pytest.raises(ValidationError):
        BankSyncFailed(sync_id=sync_id, error_code="Login fehlgeschlagen für anmeldename42")


def _schedule(*instalments, annual="400.00", **extra):
    return {
        "annual_amount": annual,
        "instalments": [{"due_on": d, "amount": a} for d, a in instalments],
    } | extra


QUARTERS = [
    ("2027-02-15", "100.01"),
    ("2027-05-15", "100.01"),
    ("2027-08-15", "100.01"),
    ("2027-11-15", "100.01"),
]


def test_grundsteuer_schedule_sums_within_four_cents():
    value = GrundsteuerSchedule.model_validate(_schedule(*QUARTERS, rhythm="quarterly"))
    assert sum(i.amount for i in value.instalments) == Decimal("400.04")
    GrundsteuerSchedule.model_validate(_schedule(("2027-08-15", "400.00"), rhythm="yearly_august"))


@pytest.mark.parametrize(
    "value",
    [
        _schedule(*QUARTERS, annual="399.99"),  # 0.05 apart
        _schedule(("2027-02-15", "200.00"), ("2028-02-15", "200.00")),  # two years
        _schedule(("2027-05-15", "200.00"), ("2027-02-15", "200.00")),  # out of order
        _schedule(("2027-02-15", "200.00"), ("2027-02-15", "200.00")),  # same day twice
        _schedule(*QUARTERS, ("2027-12-15", "0.01"), annual="400.05"),  # five instalments
        _schedule(),  # none
        _schedule(("2027-02-15", "0.00"), ("2027-05-15", "400.00")),  # a zero instalment
        _schedule(*QUARTERS, payment_mode="auto"),
    ],
)
def test_grundsteuer_schedule_refused(value):
    with pytest.raises(ValidationError):
        GrundsteuerSchedule.model_validate(value)


def test_grundsteuer_schedule_tax_input_scope():
    kind = TaxInputKind.GRUNDSTEUER_SCHEDULE
    assert kind in YEAR_SCOPED and kind in DOCUMENT_REQUIRED
    row = {
        "property_id": uuid.uuid4(),
        "kind": "grundsteuer_schedule",
        "tax_year": 2027,
        "document_id": uuid.uuid4(),
        "value": _schedule(*QUARTERS),
    }
    created = TaxInputCreate.model_validate(row)
    assert isinstance(created.value, GrundsteuerSchedule)
    payload = TaxInputRecorded.model_validate(
        {"tax_input_id": uuid.uuid4()}
        | {k: v for k, v in row.items() if k != "property_id"}
        | {"value": created.value.model_dump(mode="json")}
    )
    assert isinstance(payload.value, GrundsteuerSchedule)
    with pytest.raises(ValidationError, match="tax_input.scope_invalid"):
        TaxInputCreate.model_validate(row | {"document_id": None})
    with pytest.raises(ValidationError, match="tax_input.scope_invalid"):
        TaxInputCreate.model_validate(row | {"tax_year": None})
    with pytest.raises(ValidationError, match="tax_input.grundsteuer_schedule_invalid"):
        TaxInputCreate.model_validate(row | {"tax_year": 2028})


def test_a_rejected_mail_fetches_and_counts_nothing():
    M.MailDecision(accept=True, code="email.accepted", attachments=["2", "3.2"], skipped=1)
    with pytest.raises(ValidationError):
        M.MailDecision(accept=False, code="email.auth_failed", attachments=["2"])
    with pytest.raises(ValidationError):
        M.MailDecision(accept=False, code="email.auth_failed", skipped=1)
    with pytest.raises(ValidationError):
        M.MailDecision(accept=True, code="email.accepted")
    with pytest.raises(ValidationError):
        M.MailPart(index="2.0", media_type="application/pdf", size_bytes=1)


def test_inbound_mail_row_has_no_address_subject_or_body():
    fields = set(M.InboundMail.model_fields)
    for word in ("address", "from", "to", "subject", "body", "sender", "header"):
        assert not [f for f in fields if word in f.split("_")], word


def test_reminder_id_and_scope_shape():
    property_id = uuid.uuid4()
    reminder = {
        "id": f"grundsteuer.instalment:{property_id}:2027-02-15",
        "kind": "grundsteuer.instalment",
        "scope": "property",
        "scope_id": property_id,
        "due_on": date(2027, 2, 15),
        "visible_from": date(2027, 2, 8),
        "severity": "action",
        "cleared_by": "auto",
        "label_code": "grundsteuer.instalment.remind",
    }
    R.Reminder.model_validate(reminder)
    with pytest.raises(ValidationError, match="id is"):
        R.Reminder.model_validate(reminder | {"id": "grundsteuer.instalment:x:2027-02-15"})
    with pytest.raises(ValidationError, match="scope_id"):
        R.Reminder.model_validate(reminder | {"scope": "workspace"})
    with pytest.raises(ValidationError, match="visible_from"):
        R.Reminder.model_validate(reminder | {"visible_from": date(2027, 2, 16)})
    R.Reminder.model_validate(
        reminder
        | {
            "id": "tax.return_deadline:workspace:2027-08-02",
            "kind": "tax.return_deadline",
            "scope": "workspace",
            "scope_id": None,
            "due_on": date(2027, 8, 2),
            "visible_from": date(2027, 5, 4),
            "cleared_by": "ack",
        }
    )


def test_reminder_ack_shapes():
    R.ReminderAckCreate(action="snoozed", snoozed_until=date(2027, 3, 1))
    with pytest.raises(ValidationError):
        R.ReminderAckCreate(action="snoozed")
    with pytest.raises(ValidationError):
        R.ReminderAckCreate(action="done", snoozed_until=date(2027, 3, 1))
    common = {"reminder_key": "loan.fixed_rate_end", "scope_type": "loan"}
    common |= {"scope_id": uuid.uuid4(), "recorded_at": "2027-01-02T08:00:00Z"}
    R.ReminderAck.model_validate(common | {"action": "muted"})
    with pytest.raises(ValidationError, match="due_on"):
        R.ReminderAck.model_validate(common | {"action": "muted", "due_on": "2027-01-31"})
    with pytest.raises(ValidationError, match="due_on"):
        R.ReminderAck.model_validate(common | {"action": "done"})
