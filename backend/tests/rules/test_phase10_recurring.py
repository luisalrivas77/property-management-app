"""rules/recurring against tests/fixtures/phase10/recurring.yaml and one_off_marks.yaml
(docs/phases/phase-10.md §7.9 (b), (c); D296, D297)."""

from datetime import date
from decimal import Decimal

import fixture_models_phase10 as phase10
import pytest

from hausbuch.rules.recurring import (
    SeriesMark,
    candidates,
    newest_allocation_marks,
    recurring,
)
from hausbuch.schemas.recurring import RecurringInterval, RecurringState, SpendingMarkAnswer

RECURRING = phase10.load("recurring").cases
ONE_OFF = phase10.load("one_off_marks").cases


def _row(series) -> dict:
    return {
        "key": series.key,
        "interval": series.interval,
        "state": series.state,
        "last_amount": series.last_amount,
        "yearly_total": series.yearly_total,
        "change": series.change,
        "since": series.since,
        "payments": series.payments,
    }


def _rows(rows) -> list[dict]:
    return sorted((_row(r) for r in rows), key=lambda r: (r["key"], r["interval"].value))


@pytest.mark.parametrize("case", RECURRING, ids=lambda c: c.id)
def test_recurring(case: phase10.RecurringCase):
    view = recurring(
        phase10.spending_lines(case.lines, case.id),
        [SeriesMark(m.key, m.interval, m.answer) for m in case.marks],
        today=case.today,
        history_start=case.history_start,
    )
    assert view.yearly_detectable is case.expect.yearly_detectable
    assert _rows(view.series) == _rows(case.expect.series)
    assert _rows(view.hidden) == _rows(case.expect.hidden)
    for series in [*view.series, *view.hidden]:
        assert series.payments == len(series.sources) == len(series.lines)
        assert sum(li.amount for li in series.lines) > 0


def test_price_changes_come_first_and_totals_count_active_series():
    case = next(c for c in RECURRING if c.id == "monthly_price_changes")
    view = recurring(
        phase10.spending_lines(case.lines, case.id),
        today=case.today,
        history_start=case.history_start,
    )
    assert [s.change is not None for s in view.series] == [True, True, False]
    assert view.count == 3
    assert view.yearly_total == Decimal("191.88") + Decimal("478.80") + Decimal("1008.00")


def test_a_maybe_ended_series_is_not_counted():
    case = next(c for c in RECURRING if c.id == "stopped")
    view = recurring(
        phase10.spending_lines(case.lines, case.id),
        today=case.today,
        history_start=case.history_start,
    )
    assert [s.state for s in view.series] == [RecurringState.MAYBE_ENDED]
    assert view.count == 0 and view.yearly_total == 0


def test_a_rise_beyond_the_band_stays_a_price_change():
    """D296: every amount within 25 % of the median „apart from a price change“."""
    rows = [
        phase10.SpendingLineIn(
            id=f"X_{m:02d}",
            date=date(2026, m, 5),
            payee="Fitness Studio GmbH",
            category="hh_sport",
            amount=Decimal("-20.00") if m < 9 else Decimal("-30.00"),
        )
        for m in range(1, 12)
    ]
    view = recurring(
        phase10.spending_lines(rows, "rise"), today=date(2026, 11, 24), history_start=None
    )
    (series,) = view.series
    assert series.interval is RecurringInterval.MONTHLY
    assert (series.change, series.since, series.payments) == (Decimal("10.00"), "2026-09", 11)
    assert view.yearly_detectable is False


def _window(today: date) -> list[str]:
    """The fixture's closed window: every ended month of 2026 before today's, at most 12."""
    months = [f"2026-{m:02d}" for m in range(1, today.month)] if today.year == 2026 else []
    return months[-12:]


@pytest.mark.parametrize("case", ONE_OFF, ids=lambda c: c.id)
def test_one_off_candidates(case: phase10.OneOffCase):
    lines = phase10.spending_lines(case.lines, case.id)
    answers = newest_allocation_marks(
        (phase10.fixture_id(m.allocation), m.answer) for m in case.marks
    )
    found = candidates(lines, _window(case.today), answers, today=case.today)
    by_id = {phase10.fixture_id(row.id): row.id for row in case.lines}
    assert sorted(by_id[c.line.allocation_id] for c in found) == sorted(case.expect.candidates)


def test_newest_answer_counts():
    a = phase10.fixture_id("A")
    answers = newest_allocation_marks(
        [(a, SpendingMarkAnswer.ONE_OFF), (a, SpendingMarkAnswer.REGULAR)]
    )
    assert answers == {a: SpendingMarkAnswer.REGULAR}
