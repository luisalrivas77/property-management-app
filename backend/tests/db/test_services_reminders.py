"""services/reminders (docs/phases/phase-5.md §6.1, §6.3, D122, D126): the facts assembled
from the stored rows, the list on a given day, the append-only acknowledgements with their
event, row-level security, and the Zu prüfen cards. Synthetic data only (World's flat, bought
1 May 2024, with an invented tenant)."""

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.matching_support import TENANT_IBAN, World
from sqlalchemy import func, insert, select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import reminder_ack as ack_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.reminders import ReminderAckAction, ReminderAckCreate, ReminderKind
from hausbuch.schemas.tax import TaxInputCreate, TaxInputKind
from hausbuch.services import allocations, reminders, review
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.tax_inputs import create_tax_input

pytestmark = pytest.mark.db

TODAY = date(2026, 5, 10)  # horizon 8 Aug 2026, floor 1 Jan 2025
TENANT_NAME = "Erika Mustermieterin"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


@pytest.fixture
def other(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[1])


def _list(world: World, today: date = TODAY, **kw):
    return world.query(reminders.list_reminders, today, kw.get("from_"), kw.get("to"))


def _ids(world: World, today: date = TODAY) -> set[str]:
    return {r.id for r in _list(world, today)}


def _ack(world: World, reminder_id: str, action: str, *, at: datetime | None = None, **more):
    body = ReminderAckCreate(action=ReminderAckAction(action), **more)
    return world.cmd(reminders.acknowledge, reminder_id, body, now=at)


def _book(world: World, tx_id, **part):
    answer = AllocationSet(
        transaction_id=tx_id,
        allocations=[AllocationDraft(**part)],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )
    return world.cmd(allocations.record_set, answer, confirm=True, auto=False)


def _reconciled_through(world: World, last: date) -> None:
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == world.import_id)
            .values(first_booking_on=date(2024, 5, 1), last_booking_on=last)
        )


DIRECT_DEBIT = {"bank_transaction_code": {"domain": "PMNT", "family": "RDDT", "sub_family": "ESDD"}}


def _line(world: World, day: date, amount: str, *, raw: dict) -> uuid.UUID:
    """A bank line with its stored camt fields (transaction rows are immutable once in)."""
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            insert(transaction_table)
            .values(
                workspace_id=world.ws,
                account_id=world.account_id,
                import_id=world.import_id,
                line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                booking_date=day,
                value_date=day,
                amount=Decimal(amount),
                purpose="Grundsteuer",
                raw=raw,
                created_at=world.now,
            )
            .returning(transaction_table.c.id)
        ).scalar_one()


def _ack_rows(world: World) -> int:
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(select(func.count()).select_from(ack_table)).scalar_one()


# --- The list from the stored facts ---------------------------------------------------------


def test_the_list_reads_the_checklist_and_the_filing_deadline(world):
    p = world.property_id
    listed = _list(world)
    ids = [r.id for r in listed]
    assert ids == sorted(ids, key=lambda i: (next(r.due_on for r in listed if r.id == i), i))
    # The Grundsteuerbescheid is expected by 15 January from the first full year (2025), and
    # build_checklist itself decides it (D126): both years are missing here.
    assert f"checklist.due:{p}:2025-01-15" in ids
    assert f"checklist.due:{p}:2026-01-15" in ids
    # The 2025 return is due 31 Jul 2026 (filing_deadline), within the 90 days.
    deadline = next(r for r in listed if r.id == "tax.return_deadline:workspace:2026-07-31")
    assert deadline.label_args == {"year": 2025}
    assert deadline.visible_from == date(2026, 5, 2)
    # Nothing past the horizon: the 2026 return (2 Aug 2027) is not listed yet.
    assert "tax.return_deadline:workspace:2027-08-02" not in ids
    # from/to keep the due days in the range.
    window = _list(world, from_=date(2026, 1, 1), to=date(2026, 1, 31))
    assert window and all(date(2026, 1, 1) <= r.due_on <= date(2026, 1, 31) for r in window)


def test_the_loan_dates_come_from_the_loan_row(world):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    ids = _ids(world)
    assert f"loan.fixed_rate_end:{loan}:2026-01-31" in ids  # 12 months before, still open
    assert f"loan.fixed_rate_end:{loan}:2026-07-31" in ids  # 6 months before
    labels = {r.label_args.get("lender") for r in _list(world) if r.scope_id == loan}
    assert labels == {"Beispielbank AG"}


def test_rent_missing_names_the_unit_and_waits_for_the_statements(world):
    tenancy = world.tenancy(start=date(2024, 1, 1))
    # Rent booked for every month from January 2025 to April 2026 but March 2026; April's
    # line carries no service period and counts for its booking month.
    month = date(2025, 1, 1)
    while month <= date(2026, 4, 1):
        if month != date(2026, 3, 1):
            tx = world.line(month.replace(day=3), "1050.00", iban=TENANT_IBAN)
            period = (
                {}
                if month == date(2026, 4, 1)
                else {
                    "service_period_start": month,
                    "service_period_end": (month + timedelta(days=32)).replace(day=1)
                    - timedelta(days=1),
                }
            )
            _book(
                world,
                tx,
                amount=Decimal("1050.00"),
                category=AllocationCategory.RENT_COLD,
                property_id=world.property_id,
                unit_id=world.unit_id,
                tenancy_id=tenancy,
                **period,
            )
        month = (month + timedelta(days=32)).replace(day=1)

    # The import reaches no day yet: nothing is missing, the statement is not in.
    assert not [r for r in _list(world) if r.kind is ReminderKind.RENT_MISSING]

    _reconciled_through(world, date(2026, 4, 30))
    missing = [r for r in _list(world) if r.kind is ReminderKind.RENT_MISSING]
    assert [r.id for r in missing] == [f"tenancy.rent_missing:{tenancy}:2026-03-08"]
    assert missing[0].label_args == {"unit": "WE 3", "month": "2026-03"}
    # Not May (due 8 May, after the last reconciled day), and no tenant anywhere.
    facts = world.query(reminders.facts, TODAY)
    assert TENANT_NAME not in facts.model_dump_json()
    assert TENANT_NAME not in str([r.model_dump() for r in _list(world)])


def test_the_grundsteuer_facts_carry_the_schedule_and_the_direct_debit(world):
    doc = world.document("grundsteuerbescheid", {}, type="bescheid", subtype="grundsteuerbescheid")
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(document_table).where(document_table.c.id == doc).values(tax_year=2025))
    world.cmd(
        create_tax_input,
        world.property_id,
        TaxInputCreate(
            property_id=world.property_id,
            kind=TaxInputKind.GRUNDSTEUER_SCHEDULE,
            tax_year=2026,
            document_id=doc,
            value={
                "annual_amount": "400.00",
                "instalments": [
                    {"due_on": f"2026-{m:02d}-15", "amount": "100.00"} for m in (2, 5, 8, 11)
                ],
            },
        ),
    )
    debit = _line(world, date(2026, 2, 16), "-100.00", raw=DIRECT_DEBIT)
    transfer = _line(world, date(2025, 11, 14), "-100.00", raw={})
    for tx in (debit, transfer):
        _book(
            world,
            tx,
            amount=Decimal("-100.00"),
            category=AllocationCategory.GRUNDSTEUER,
            property_id=world.property_id,
        )

    facts = world.query(reminders.facts, TODAY)
    (prop,) = facts.properties
    (schedule,) = prop.grundsteuer_schedules
    assert (schedule.tax_year, len(schedule.instalments), schedule.payment_mode) == (2026, 4, None)
    assert {(p.booking_date, p.direct_debit) for p in prop.grundsteuer_payments} == {
        (date(2026, 2, 16), True),
        (date(2025, 11, 14), False),
    }
    ids = _ids(world)
    p = world.property_id
    assert f"grundsteuer.instalment:{p}:2026-02-15" not in ids  # cleared by the debit
    may = next(r for r in _list(world) if r.id == f"grundsteuer.instalment:{p}:2026-05-15")
    # A direct-debit line cleared the February instalment, so the mode is sepa_mandate: the
    # card shows on the day, and the email is the check three days after (D126).
    assert (may.visible_from, may.email_leads) == (date(2026, 5, 15), [-3])
    assert "amount" not in may.label_args


# --- Acknowledgements (§6.3) -----------------------------------------------------------------


def test_done_hides_only_that_instance_and_writes_a_row_and_an_event(world):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    first = f"loan.fixed_rate_end:{loan}:2026-01-31"
    second = f"loan.fixed_rate_end:{loan}:2026-07-31"
    ack = _ack(world, first, "done")
    assert (ack.reminder_key, ack.scope_type.value, ack.scope_id, ack.due_on) == (
        ReminderKind.LOAN_FIXED_RATE_END,
        "loan",
        loan,
        date(2026, 1, 31),
    )
    ids = _ids(world)
    assert first not in ids and second in ids
    assert _ack_rows(world) == 1
    with workspace_transaction(world.engine, world.ws) as conn:
        (event,) = conn.execute(
            select(event_table.c.entity_id, event_table.c.payload).where(
                event_table.c.type == "workspace.reminder_acknowledged"
            )
        ).all()
    assert event.entity_id == world.ws
    assert event.payload == {
        "reminder_key": "loan.fixed_rate_end",
        "scope_type": "loan",
        "scope_id": str(loan),
        "due_on": "2026-01-31",
        "action": "done",
        "snoozed_until": None,
    }


def test_muted_hides_every_instance_until_a_later_unmuted_row(world):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    first = f"loan.fixed_rate_end:{loan}:2026-01-31"
    t0 = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    muted = _ack(world, first, "muted", at=t0)
    assert muted.due_on is None  # a whole-key row has no due day
    assert not [r for r in _list(world) if r.kind is ReminderKind.LOAN_FIXED_RATE_END]
    _ack(world, first, "unmuted", at=t0 + timedelta(minutes=1))
    assert len([r for r in _list(world) if r.kind is ReminderKind.LOAN_FIXED_RATE_END]) == 2
    # Append-only: each answer is its own row.
    assert _ack_rows(world) == 2


def test_snoozed_hides_the_instance_until_its_day(world):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    second = f"loan.fixed_rate_end:{loan}:2026-07-31"
    _ack(world, second, "snoozed", snoozed_until=date(2026, 6, 1))
    assert second not in _ids(world, date(2026, 5, 31))
    assert second in _ids(world, date(2026, 6, 1))


@pytest.mark.parametrize(
    ("make_id", "code"),
    [
        (lambda w, loan: "not-a-reminder", "reminder.unknown"),
        (lambda w, loan: f"loan.unknown_kind:{loan}:2026-01-31", "reminder.unknown"),
        (lambda w, loan: f"loan.fixed_rate_end:{loan}:2026-02-30", "reminder.unknown"),
        (lambda w, loan: f"loan.fixed_rate_end:{str(loan).upper()}:2026-01-31", "reminder.unknown"),
        # A property id where the kind needs a loan, and the workspace where it needs a row.
        (
            lambda w, loan: f"loan.fixed_rate_end:{w.property_id}:2026-01-31",
            "reminder.scope_invalid",
        ),
        (lambda w, loan: "loan.fixed_rate_end:workspace:2026-01-31", "reminder.scope_invalid"),
        (lambda w, loan: f"tax.return_deadline:{loan}:2026-07-31", "reminder.scope_invalid"),
        (
            lambda w, loan: f"loan.fixed_rate_end:{uuid.uuid4()}:2026-01-31",
            "reminder.scope_invalid",
        ),
    ],
)
def test_an_id_that_is_not_a_reminder_of_yours_is_refused(world, make_id, code):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    with pytest.raises(RuleViolationError) as caught:
        _ack(world, make_id(world, loan), "done")
    assert caught.value.codes == (code,)
    assert _ack_rows(world) == 0


def test_checklist_due_takes_a_property_or_a_loan(world):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    assert (
        _ack(world, f"checklist.due:{world.property_id}:2026-01-15", "done").scope_type
        == "property"
    )
    assert _ack(world, f"checklist.due:{loan}:2027-02-28", "done").scope_type == "loan"


def test_another_workspace_can_neither_ack_nor_see(world, other):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    first = f"loan.fixed_rate_end:{loan}:2026-01-31"
    with pytest.raises(RuleViolationError) as caught:
        _ack(other, first, "done")
    assert caught.value.codes == ("reminder.scope_invalid",)
    _ack(world, first, "done")
    assert _ack_rows(other) == 0
    assert not [r for r in _list(other) if r.scope_id in (loan, world.property_id)]
    assert first not in _ids(world)


# --- Zu prüfen (§11) -----------------------------------------------------------------------


def test_the_inbox_shows_action_reminders_visible_today(world):
    loan = world.loan(fixed_until=date(2027, 1, 31))
    items = [i for i in world.query(review.inbox, TODAY) if i.kind is InboxItemKind.REMINDER]
    ids = {i.reminder_id for i in items}
    assert f"loan.fixed_rate_end:{loan}:2026-01-31" in ids  # due, open, ack-cleared
    assert "tax.return_deadline:workspace:2026-07-31" in ids  # visible from 2 May
    assert f"loan.fixed_rate_end:{loan}:2026-07-31" not in ids  # visible only on the day
    listed = {r.id: r for r in _list(world)}
    for item in items:
        r = listed[item.reminder_id]
        assert r.severity == "action" and r.visible_from <= TODAY
        assert r.kind is not ReminderKind.QUESTIONNAIRE_OPEN
        assert item.created_at == datetime.combine(r.visible_from, datetime.min.time(), UTC)
        assert item.reasoning is None and item.question is None
    # Without today the inbox has no reminder cards (the jobs call it so).
    assert not [i for i in world.query(review.inbox) if i.kind is InboxItemKind.REMINDER]
