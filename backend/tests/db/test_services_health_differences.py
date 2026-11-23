"""GET /health's „Was diesen Monat anders ist“ under hausbuch_app and RLS
(docs/phases/phase-10.md §7.6 Haushalt (3), build decision (a)): the newest closed month's
spending groups against their mean over the closed months before it, each named group with the
payments behind it. The thresholds are held by tests/rules/test_phase10_differences.py; these
tests hold the read. Invented amounts only."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_budget import NOW, TODAY, _booked, _private
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import statement_import as import_table
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import DifferenceWords
from hausbuch.schemas.household import HouseholdGroup as G
from hausbuch.services import health

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


def _months(world: World, private, months, restaurant: dict[tuple[int, int], list[str]]):
    """Each (year, month): the rent in, the salary, the rent out and groceries of 400 €, and
    the restaurant payments of `restaurant` for that month."""
    for year, month in months:
        _booked(world, date(year, month, 3), "950.00", C.RENT_COLD)
        _booked(world, date(year, month, 27), "3100.00", C.HH_SALARY, on=private)
        _booked(world, date(year, month, 1), "-1100.00", C.HH_RENT, on=private)
        _booked(world, date(year, month, 9), "-400.00", C.HH_GROCERIES, on=private)
        for day, amount in enumerate(restaurant.get((year, month), []), start=10):
            _booked(world, date(year, month, day), amount, C.HH_RESTAURANT, on=private)


ALL = [(2026, m) for m in range(1, 13)] + [(2027, m) for m in range(1, 4)]


def test_the_newest_closed_month_names_a_group_with_its_payments(world):
    """Every month from January 2026 to March 2027 is closed with the same rent, salary and
    groceries; the restaurant's 300 € over the twelve months before March make a mean of
    25 €, so March's 180 € is named, with its two payments. Nothing else differs."""
    private = _private(world)
    _months(
        world,
        private,
        ALL,
        {
            (2026, 12): ["-100.00"],
            (2027, 1): ["-80.00", "-20.00"],
            (2027, 2): ["-100.00"],
            (2027, 3): ["-120.00", "-60.00"],
        },
    )
    got = world.query(health.health, TODAY)
    assert got.newest is not None and got.newest.month == "2027-03"
    found = got.differences
    assert found is not None and found.month == "2027-03"
    assert found.baseline[0] == "2026-03" and found.baseline[-1] == "2027-02"
    assert len(found.baseline) == 12  # the closed months before March, cut to 12
    [one] = found.groups
    assert (one.group, one.amount, one.words) == (G.DINING, Decimal("180.00"), DifferenceWords.MORE)
    assert (one.mean, one.difference) == (Decimal("25.00"), Decimal("155.00"))
    assert [(p.booked_on, p.amount, p.category) for p in one.payments] == [
        (date(2027, 3, 10), Decimal("120.00"), C.HH_RESTAURANT),
        (date(2027, 3, 11), Decimal("60.00"), C.HH_RESTAURANT),
    ]
    assert sum(p.amount for p in one.payments) == one.amount
    assert {p.account_id for p in one.payments} == {private.account_id}


def test_nothing_with_fewer_than_three_closed_months_before(world):
    """A line waiting in December 2026 leaves January to March as the window: March has two
    closed months before it, so nothing is compared, however much it differs."""
    private = _private(world)
    _months(world, private, ALL, {(2027, 3): ["-500.00"]})
    private.line(date(2026, 12, 12), "-30.00")  # waiting in Zu prüfen
    got = world.query(health.health, TODAY)
    assert got.window.months == ["2027-01", "2027-02", "2027-03"]
    assert got.differences is None


def test_no_group_differs_enough(world):
    private = _private(world)
    _months(world, private, ALL, {})
    got = world.query(health.health, TODAY)
    assert got.differences is not None and got.differences.groups == []
