"""GET /household/differences and GET /household/lines under hausbuch_app and RLS
(docs/phases/phase-10.md §7.13 (b) 2, 3, D314): any chosen month's spending codes against their
mean over the closed months before it, and the payments behind one code's month. The
thresholds are held by tests/rules/test_phase10_differences.py; these tests hold the read.
Invented amounts only."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_budget import NOW, TODAY, _private
from sqlalchemy import select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.rules.errors import HEALTH_RANGE_INVALID
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.health import DifferenceWords
from hausbuch.schemas.household import HouseholdGroup as G
from hausbuch.schemas.recurring import OneOffMarkRequest, SpendingMarkAnswer
from hausbuch.services import allocations, health, spending_marks
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.household_differences import category_lines, month_category_differences

pytestmark = pytest.mark.db

C = AllocationCategory


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


def _book(world: World, on, booked: date, amount: str, category: C):
    """A line of `on` (the rental world or a Private account), booked and confirmed; its
    allocation's id."""
    tx = on.line(booked, amount)
    world.cmd(
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
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(allocation_table.c.id).where(allocation_table.c.transaction_id == tx)
        ).scalar_one()


ALL = [(2026, m) for m in range(1, 13)] + [(2027, m) for m in range(1, 4)]


def _months(world: World, private, supermarket: dict[tuple[int, int], str]):
    """Each (year, month) of ALL: the rent in, the salary, the rent out and a supermarket
    payment of 300 € (or `supermarket`'s amount for that month)."""
    for year, month in ALL:
        _book(world, world, date(year, month, 3), "950.00", C.RENT_COLD)
        _book(world, private, date(year, month, 27), "3100.00", C.HH_SALARY)
        _book(world, private, date(year, month, 1), "-1100.00", C.HH_RENT)
        amount = supermarket.get((year, month), "-300.00")
        _book(world, private, date(year, month, 9), amount, C.HH_SUPERMARKET)


def test_a_chosen_closed_month_names_its_codes(world):
    """December 2026's supermarket 384 € against a mean of 300 € over the closed months before
    it (January to November, eleven): „Supermarkt +84 €“, the same as /health would say for a
    group but per code, and for a month that is not the newest."""
    private = _private(world)
    _months(world, private, {(2026, 12): "-384.00"})
    got = world.query(month_category_differences, "2026-12", TODAY)
    assert (got.month, got.final, got.enough) == ("2026-12", True, True)
    assert got.baseline[0] == "2026-01" and got.baseline[-1] == "2026-11"
    [one] = got.categories
    assert (one.category, one.group, one.words) == (
        C.HH_SUPERMARKET,
        G.GROCERIES,
        DifferenceWords.MORE,
    )
    assert (one.amount, one.mean, one.difference) == (
        Decimal("384.00"),
        Decimal("300.00"),
        Decimal("84.00"),
    )


def test_the_month_in_progress_names_only_what_is_above_its_mean(world):
    """April 2027 runs on TODAY: a supermarket payment of 400 € (above the mean of 300 €) is
    named, and the rent of 0 so far (below its mean of 1,100 €) is not."""
    private = _private(world)
    _months(world, private, {})
    _book(world, private, date(2027, 4, 5), "-400.00", C.HH_SUPERMARKET)
    got = world.query(month_category_differences, "2027-04", TODAY)
    assert (got.final, got.enough) == (False, True)
    assert len(got.baseline) == 12
    assert [(d.category, d.difference) for d in got.categories] == [
        (C.HH_SUPERMARKET, Decimal("100.00"))
    ]


def test_fewer_than_three_closed_months_before_says_so(world):
    private = _private(world)
    _months(world, private, {(2026, 3): "-900.00"})
    got = world.query(month_category_differences, "2026-03", TODAY)
    assert (got.enough, got.baseline, got.categories) == (False, ["2026-01", "2026-02"], [])


@pytest.mark.parametrize("month", ["2025-12", "2027-05"])
def test_a_month_outside_the_history_or_after_today_is_refused(world, month):
    with pytest.raises(RuleViolationError) as caught:
        world.query(month_category_differences, month, TODAY)
    assert list(caught.value.codes) == [HEALTH_RANGE_INVALID]
    with pytest.raises(RuleViolationError):
        world.query(category_lines, month, C.HH_SUPERMARKET, TODAY)


def test_the_lines_of_a_code_sum_to_its_month_and_a_one_off_is_left_out_of_regular(world):
    """March 2027: two supermarket payments, one marked one-off. The total is the code's
    figure in the month's spending by category (both), `regular` the one compared."""
    private = _private(world)
    _months(world, private, {})
    big = _book(world, private, date(2027, 3, 20), "-250.00", C.HH_SUPERMARKET)
    world.cmd(
        spending_marks.record_one_off,
        OneOffMarkRequest(allocation_id=big, answer=SpendingMarkAnswer.ONE_OFF),
        TODAY,
    )
    got = world.query(category_lines, "2027-03", C.HH_SUPERMARKET, TODAY)
    assert (got.group, got.total, got.regular) == (
        G.GROCERIES,
        Decimal("550.00"),
        Decimal("300.00"),
    )
    assert [(p.booked_on, p.amount, p.one_off) for p in got.payments] == [
        (date(2027, 3, 9), Decimal("300.00"), False),
        (date(2027, 3, 20), Decimal("250.00"), True),
    ]
    assert {p.account_id for p in got.payments} == {private.account_id}
    spent = world.query(health.health, TODAY)
    assert spent.newest is not None and spent.newest.month == "2027-03"
    # The marked payment leaves the comparison: March's regular 300 € equals the mean.
    march = world.query(month_category_differences, "2027-03", TODAY)
    assert march.categories == []


def test_a_code_that_is_no_spending_has_no_lines(world):
    private = _private(world)
    _months(world, private, {})
    got = world.query(category_lines, "2027-03", C.HH_SALARY, TODAY)
    assert (got.group, got.total, got.regular, got.payments) == (
        None,
        Decimal("0.00"),
        Decimal("0.00"),
        [],
    )
