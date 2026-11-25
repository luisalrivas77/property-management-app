"""services.recurring and services.spending_marks under hausbuch_app and RLS (docs/phases/
phase-10.md §7.9 (b), (c), (f), §7.12 (b); D296, D297, D307; Q-O183 (a)): the recurring
payments read from the confirmed private lines, the answers stored with their event, and a
payment marked one-off from its detail panel moving the four places that read it (the
household month, the plan's means, the Budget's „Einmalig im Zeitraum“, Haushalt's
differences) while a stored month stays as stored. The candidate search of D297 went with
D307. The arithmetic is the phase 10 fixtures; invented payees and amounts only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_health import _book
from db.test_services_private_matching import Private
from sqlalchemy import select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import health_snapshot as health_snapshot_table
from hausbuch.db.tables import spending_mark as mark_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.budget import BudgetLineKey as L
from hausbuch.schemas.enums import AcquisitionCostKind
from hausbuch.schemas.household import HouseholdGroup
from hausbuch.schemas.property import AcquisitionCostCreate
from hausbuch.schemas.recurring import (
    OneOffMarkRequest,
    RecurringInterval,
    RecurringState,
    SeriesMarkRequest,
    SpendingMarkAnswer,
)
from hausbuch.services import allocations, health, net_worth, plan, recurring, spending_marks
from hausbuch.services.budget import household_budget
from hausbuch.services.errors import NotFoundError
from hausbuch.services.properties import add_acquisition_cost

pytestmark = pytest.mark.db

C = AllocationCategory
A = SpendingMarkAnswer
NOW = datetime(2027, 4, 10, 8, 0, tzinfo=UTC)
TODAY = date(2027, 4, 10)
STREAMING = "Streamdienst Muster"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    w = World(app_engine, fresh_workspaces[0], now=NOW)
    with workspace_transaction(w.engine, w.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == w.import_id)
            .values(first_booking_on=date(2026, 1, 1), last_booking_on=date(2027, 3, 31))
        )
    return w


@pytest.fixture
def private(world) -> Private:
    private = Private(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == private.import_id)
            .values(first_booking_on=date(2026, 1, 1), last_booking_on=date(2027, 3, 31))
        )
    return private


def _booked(world: World, tx, amount: str, category: C):
    """The line `tx` booked and confirmed as one allocation; its allocation id."""
    (row,) = world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
    )
    return row.id


def _household(world: World, private: Private) -> dict[str, object]:
    """A year of household lines from April 2026: the flat's rent on the rental account, and
    on the private account the salary, groceries and a streaming plan each month, and one
    workshop bill of 870 € in March 2027. Returns the allocation ids by name (the streaming
    and groceries ids March 2027's)."""
    ids: dict[str, object] = {}
    for year, month in [(2026, m) for m in range(4, 13)] + [(2027, m) for m in range(1, 4)]:
        _booked(world, world.line(date(year, month, 3), "950.00"), "950.00", C.RENT_COLD)
        _booked(
            world,
            private.line(date(year, month, 27), "3100.00", name="Arbeitgeber"),
            "3100.00",
            C.HH_SALARY,
        )
        ids["groceries"] = _booked(
            world,
            private.line(date(year, month, 9), "-180.00", name="Markt"),
            "-180.00",
            C.HH_GROCERIES,
        )
        ids["streaming"] = _booked(
            world,
            private.line(date(year, month, 5), "-12.99", name=STREAMING),
            "-12.99",
            C.HH_STREAMING,
        )
    ids["workshop"] = _booked(
        world,
        private.line(date(2027, 3, 18), "-870.00", name="Werkstatt Beispiel", purpose="Inspektion"),
        "-870.00",
        C.HH_CAR_SERVICE,
    )
    return ids


def test_the_recurring_payments_and_a_series_answer(world, private):
    _household(world, private)
    got = world.query(recurring.recurring_payments, TODAY)
    assert got.as_of == TODAY and got.history_start == date(2026, 1, 1)
    (series,) = [s for s in got.series if s.last_amount == Decimal("12.99")]
    assert series.interval is RecurringInterval.MONTHLY and series.payments == 12
    assert series.lines and series.label

    world.cmd(
        spending_marks.record_series,
        SeriesMarkRequest(key=series.key, interval=series.interval, answer=A.HIDDEN),
    )
    hidden = world.query(recurring.recurring_payments, TODAY)
    assert series.key not in {s.key for s in hidden.series}
    assert series.key in {s.key for s in hidden.hidden}
    world.cmd(
        spending_marks.record_series,
        SeriesMarkRequest(key=series.key, interval=series.interval, answer=A.SHOWN),
    )
    assert series.key in {s.key for s in world.query(recurring.recurring_payments, TODAY).series}

    with workspace_transaction(world.engine, world.ws) as conn:
        payloads = [
            r.payload
            for r in conn.execute(
                select(event_table.c.payload).where(event_table.c.type == "household.mark_recorded")
            )
        ]
    assert payloads and all(STREAMING.lower() not in str(p).lower() for p in payloads)
    assert all(series.key not in str(p) for p in payloads)  # never the payee's words


def test_one_missed_payment_is_maybe_ended_once_the_account_reads_on(world, private):
    """D296 amended (§7.9 (b) 3): a monthly series last paid 12 Feb 2027 has missed one
    interval on 10 Apr (57 days). Active while the account's lines end before 19 Mar (the day
    the next payment was due at the latest); maybe ended once any line on the account is later,
    here one nobody has booked yet, which services.recurring reads by its booking date."""
    for year, month in [(2026, m) for m in range(6, 13)] + [(2027, 1), (2027, 2)]:
        _booked(
            world,
            private.line(date(year, month, 12), "-12.99", name=STREAMING),
            "-12.99",
            C.HH_STREAMING,
        )

    def state():
        got = world.query(recurring.recurring_payments, TODAY)
        (series,) = [s for s in got.series if s.last_amount == Decimal("12.99")]
        return series.state

    assert state() is RecurringState.ACTIVE
    private.line(date(2027, 4, 1), "25.00", name="Erstattung Muster")  # not booked
    assert world.query(recurring.accounts_through, TODAY)[private.account_id] == date(2027, 4, 1)
    assert state() is RecurringState.MAYBE_ENDED


def test_a_one_off_mark_moves_the_four_places_and_keeps_the_stored_month(world, private):
    ids = _household(world, private)
    before = world.query(health.health, TODAY)
    assert HouseholdGroup.MOBILITY in {g.group for g in before.differences.groups}
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    world.cmd(health.store_snapshot, "2027-03")
    with workspace_transaction(world.engine, world.ws) as conn:
        stored = conn.execute(select(health_snapshot_table)).all()
    march = world.query(health.read_months, ["2027-03"], TODAY)["2027-03"].household
    assert march.one_off_spending == 0

    recorded = world.cmd(
        spending_marks.record_one_off,
        OneOffMarkRequest(allocation_id=ids["workshop"], answer=A.ONE_OFF),
        TODAY,
    )
    assert recorded.answer is A.ONE_OFF

    # 1 the household month: the payment is one-off spending, spending itself unchanged
    reads = world.query(health.read_months, ["2027-03"], TODAY)
    after = reads["2027-03"].household
    assert after.one_off_spending == Decimal("870.00")
    assert after.private_spending == march.private_spending
    assert after.regular_spending == march.regular_spending - Decimal("870.00")
    # 2 the plan's means: the code is gone from March's regular spending
    (spent,) = plan.window_spending(reads, ["2027-03"])
    assert spent.get(C.HH_CAR_SERVICE, Decimal("0.00")) == 0
    # 3 the Budget's „Einmalig im Zeitraum“, beside the partition (D307)
    budget = world.query(household_budget, TODAY)
    assert budget.one_off is not None and budget.one_off.key is L.ONE_OFF
    assert budget.one_off.total == Decimal("870.00")
    assert L.ONE_OFF not in {li.key for li in budget.lines}
    # 4 Haushalt's differences: Mobilität no longer differs
    differences = world.query(health.health, TODAY).differences
    assert HouseholdGroup.MOBILITY not in {g.group for g in differences.groups}

    # the stored month stays as stored, and shows beside the month on read
    with workspace_transaction(world.engine, world.ws) as conn:
        assert conn.execute(select(health_snapshot_table)).all() == stored
    (view,) = world.query(health.health_months, "2027-03", "2027-03", TODAY)
    assert view.stored is not None and view.stored.one_off_spending == 0

    # the newest answer counts
    world.cmd(
        spending_marks.record_one_off,
        OneOffMarkRequest(allocation_id=ids["workshop"], answer=A.REGULAR),
        TODAY,
    )
    again = world.query(health.read_months, ["2027-03"], TODAY)["2027-03"].household
    assert again.one_off_spending == 0


def test_only_a_confirmed_private_spending_payment_can_be_marked(world, private, fresh_workspaces):
    ids = _household(world, private)
    rent = _booked(world, world.line(date(2027, 3, 3), "950.00"), "950.00", C.RENT_COLD)
    repair = _booked(world, world.line(date(2027, 3, 12), "-400.00"), "-400.00", C.REPAIRS)
    salary = _booked(world, private.line(date(2027, 3, 28), "50.00"), "50.00", C.HH_SALARY)
    other = World(world.engine, fresh_workspaces[1], now=NOW)
    foreign = _booked(
        other, Private(other).line(date(2027, 3, 1), "-900.00"), "-900.00", C.HH_CAR_SERVICE
    )
    for allocation in (rent, repair, salary, foreign):
        with pytest.raises(NotFoundError):
            world.cmd(
                spending_marks.record_one_off,
                OneOffMarkRequest(allocation_id=allocation, answer=A.ONE_OFF),
                TODAY,
            )
    world.cmd(
        spending_marks.record_one_off,
        OneOffMarkRequest(allocation_id=ids["workshop"], answer=A.ONE_OFF),
        TODAY,
    )
    with workspace_transaction(world.engine, world.ws) as conn:
        assert conn.execute(select(mark_table.c.allocation_id)).scalars().all() == [ids["workshop"]]
        (payload,) = conn.execute(
            select(event_table.c.payload).where(event_table.c.type == "household.mark_recorded")
        ).scalars()
    assert payload == {"allocation_id": str(ids["workshop"]), "interval": None, "answer": "one_off"}
    assert world.query(spending_marks.one_off_marked) == frozenset({ids["workshop"]})
    assert other.query(spending_marks.one_off_marked) == frozenset()


def test_a_privately_paid_purchase_cost_is_not_spending_and_cannot_be_marked(world, private):
    """D234: the notary's bill of the purchase, paid privately and booked hh_property_cost
    with the bill, is invested, not spent: 404 for a mark, as the household month counts it;
    a repair paid the same way is spending and can be marked."""
    _household(world, private)
    notary = world.document("invoice", {})
    world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.NOTARY,
            amount=Decimal("4200.00"),
            document_id=notary,
        ),
    )
    paid = _book(
        world,
        private.line(date(2027, 2, 14), "-4200.00", name="Notarin Beispiel"),
        "-4200.00",
        C.HH_PROPERTY_COST,
        property_id=world.property_id,
        document_id=notary,
    )[0].id
    fixed = _book(
        world,
        private.line(date(2027, 2, 22), "-300.00", name="Malerei Beispiel"),
        "-300.00",
        C.HH_PROPERTY_COST,
        property_id=world.property_id,
        document_id=world.document("invoice", {}),
    )[0].id
    with pytest.raises(NotFoundError):
        world.cmd(
            spending_marks.record_one_off,
            OneOffMarkRequest(allocation_id=paid, answer=A.ONE_OFF),
            TODAY,
        )
    world.cmd(
        spending_marks.record_one_off,
        OneOffMarkRequest(allocation_id=fixed, answer=A.ONE_OFF),
        TODAY,
    )


def test_a_re_booked_payment_is_asked_again(world, private):
    """§7.9 (c) 4: a correction gives the payment a new allocation; the old one's mark no
    longer counts, the old id cannot be marked, the new one can."""
    ids = _household(world, private)
    world.cmd(
        spending_marks.record_one_off,
        OneOffMarkRequest(allocation_id=ids["workshop"], answer=A.ONE_OFF),
        TODAY,
    )
    with workspace_transaction(world.engine, world.ws) as conn:
        tx = conn.execute(
            select(allocation_table.c.transaction_id).where(
                allocation_table.c.id == ids["workshop"]
            )
        ).scalar_one()
    rebooked = _booked(world, tx, "-870.00", C.HH_CAR_SERVICE)
    assert rebooked != ids["workshop"]
    march = world.query(health.read_months, ["2027-03"], TODAY)["2027-03"].household
    assert march.one_off_spending == 0
    with pytest.raises(NotFoundError):
        world.cmd(
            spending_marks.record_one_off,
            OneOffMarkRequest(allocation_id=ids["workshop"], answer=A.REGULAR),
            TODAY,
        )
    world.cmd(
        spending_marks.record_one_off,
        OneOffMarkRequest(allocation_id=rebooked, answer=A.ONE_OFF),
        TODAY,
    )


def test_any_counted_spending_payment_up_to_today_can_be_marked(world, private):
    """Phase 10 §7.12 (b), D307: a payment is marked from its detail panel, so the POST takes
    any confirmed private payment the household month counts as spending up to today: one in
    a recurring series and one in a code one-off in force too (that mark counts only while a
    switch makes the code regular). A cash withdrawal and a payment dated after today are a
    404, as an unknown id is."""
    _household(world, private)
    cash = _booked(
        world, private.line(date(2027, 3, 20), "-900.00", name="Geldautomat"), "-900.00", C.HH_CASH
    )
    insurance = [
        _booked(
            world,
            private.line(date(year, 3, 1), "-600.00", name="Versicherung Beispiel"),
            "-600.00",
            C.HH_INSURANCE,
        )
        for year in (2026, 2027)
    ]
    small = _booked(
        world,
        private.line(date(2027, 3, 24), "-120.00", name="Kabelhandel Beispiel"),
        "-120.00",
        C.HH_ELECTRONICS,
    )
    later = _booked(
        world,
        private.line(date(2027, 4, 20), "-900.00", name="Elektromarkt Beispiel"),
        "-900.00",
        C.HH_ELECTRONICS,
    )
    (yearly,) = [
        s for s in world.query(recurring.recurring_payments, TODAY).series if s.last_amount == 600
    ]
    assert yearly.interval is RecurringInterval.YEARLY
    refused = {"a cash withdrawal": cash, "dated after today": later}
    for why, allocation in refused.items():
        with pytest.raises(NotFoundError):
            world.cmd(
                spending_marks.record_one_off,
                OneOffMarkRequest(allocation_id=allocation, answer=A.ONE_OFF),
                TODAY,
            )
        assert allocation not in world.query(spending_marks.one_off_marked), why
    for allocation in (insurance[1], small):  # in a series; in a one-off code (Elektronik)
        world.cmd(
            spending_marks.record_one_off,
            OneOffMarkRequest(allocation_id=allocation, answer=A.ONE_OFF),
            TODAY,
        )
    with workspace_transaction(world.engine, world.ws) as conn:
        marked = set(conn.execute(select(mark_table.c.allocation_id)).scalars())
    assert marked == {insurance[1], small}
