"""GET /household-budget's fixed payments inside Notwendiges and Wünsche on the database
(docs/phases/phase-10.md §7.10 (c), D300): each class's fixed means and variable rest add up
to its line's monthly figure, a fixed row is a series GET /household/recurring lists and is
keyed by its oldest payment's id, a series hidden as „kein Abo“ moves into the variable part,
and the kept read equals the computation with the store bypassed, cold and warm. The
arithmetic is tests/fixtures/phase10/budget_fixed.yaml; invented payees and amounts only."""

from datetime import date
from decimal import Decimal

import pytest
from db.test_services_recurring import (  # noqa: F401  (fixtures)
    STREAMING,
    TODAY,
    _booked,
    _household,
    private,
    world,
)

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.budget import BudgetLineKey as L
from hausbuch.schemas.recurring import SeriesMarkRequest, SpendingMarkAnswer
from hausbuch.services import plan_inputs, recurring, spending_marks
from hausbuch.services.budget import household_budget

pytestmark = pytest.mark.db

C = AllocationCategory


@pytest.fixture(autouse=True)
def _fresh_store():
    plan_inputs.clear()
    yield
    plan_inputs.clear()


def _lines(view):
    return {line.key: line for line in view.lines}


def _uncached(world):  # noqa: F811
    with plan_inputs.bypass():
        return world.query(household_budget, TODAY)


def test_fixed_and_variable_add_up_to_each_class(world, private):  # noqa: F811
    _household(world, private)
    view = world.query(household_budget, TODAY)
    lines = _lines(view)
    assert view.window.months[-1] == "2027-03"
    for key in (L.NEED, L.WANT):
        line = lines[key]
        assert line.fixed is not None and line.variable is not None
        assert sum((f.mean for f in line.fixed), line.variable) == line.monthly
    for key in lines.keys() - {L.NEED, L.WANT}:
        assert lines[key].fixed is None and lines[key].variable is None

    listed = {s.sources[0]: s for s in world.query(recurring.recurring_payments, TODAY).series}
    (streaming,) = lines[L.WANT].fixed
    assert streaming.key in listed and listed[streaming.key].label == STREAMING
    assert (streaming.monthly, streaming.mean) == (Decimal("12.99"), Decimal("12.99"))
    assert streaming.category is C.HH_STREAMING
    (groceries,) = lines[L.NEED].fixed  # 180 € a month at one market: a series too
    assert (groceries.monthly, groceries.mean) == (Decimal("180.00"), Decimal("180.00"))
    assert groceries.key in listed
    # the workshop bill is no series: it stays in the variable part of Notwendiges
    assert lines[L.NEED].variable == lines[L.NEED].monthly - Decimal("180.00")
    assert lines[L.WANT].variable == lines[L.WANT].monthly - Decimal("12.99")


def test_a_hidden_series_moves_to_the_variable_part(world, private):  # noqa: F811
    _household(world, private)
    before = _lines(world.query(household_budget, TODAY))[L.WANT]
    (row,) = before.fixed
    (series,) = [
        s
        for s in world.query(recurring.recurring_payments, TODAY).series
        if s.sources[0] == row.key
    ]
    world.cmd(
        spending_marks.record_series,
        SeriesMarkRequest(
            key=series.key, interval=series.interval, answer=SpendingMarkAnswer.HIDDEN
        ),
    )
    after = _lines(world.query(household_budget, TODAY))[L.WANT]  # the kept parts moved on
    assert after.fixed == []
    assert after.monthly == before.monthly  # the class figure is the Budget's, unchanged
    assert after.variable == before.monthly
    assert world.query(household_budget, TODAY) == _uncached(world)


def test_the_read_equals_the_uncached_computation_cold_and_warm(world, private):  # noqa: F811
    _household(world, private)
    want = _uncached(world)
    assert any(line.fixed for line in want.lines)
    plan_inputs.clear()
    assert world.query(household_budget, TODAY) == want  # cold
    assert world.query(household_budget, TODAY) == want  # warm
    _booked(
        world,
        private.line(date(2027, 3, 21), "-9.99", name="Neuer Dienst"),
        "-9.99",
        C.HH_STREAMING,
    )
    changed = world.query(household_budget, TODAY)
    assert changed != want
    assert changed == _uncached(world)
