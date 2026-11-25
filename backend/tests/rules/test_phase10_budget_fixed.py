"""rules/budget/fixed against tests/fixtures/phase10/budget_fixed.yaml (docs/phases/phase-10.md
§7.10 (c), D300): the series come from the shipped rules.recurring over the case's lines and
marks, then fixed_split places them per class."""

from decimal import Decimal

import fixture_models_phase10 as phase10
import pytest

from hausbuch.rules.budget.fixed import fixed_split
from hausbuch.rules.recurring import SeriesMark, recurring
from hausbuch.schemas.budget import BudgetLineKey
from hausbuch.schemas.household import BudgetClass

CASES = phase10.load("budget_fixed").cases


def _series(case: phase10.FixedCase):
    return recurring(
        phase10.spending_lines(case.lines, case.id),
        [SeriesMark(m.key, m.interval, m.answer) for m in case.marks],
        today=case.today,
        history_start=case.history_start,
        accounts_through=phase10.accounts_through(case.accounts_through, case.id),
    ).series


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("budget_class", [BudgetClass.NEED, BudgetClass.WANT])
def test_fixed_split(case: phase10.FixedCase, budget_class: BudgetClass):
    names = {phase10.fixture_id(line.id): line.id for line in case.lines}
    line = BudgetLineKey.NEED if budget_class is BudgetClass.NEED else BudgetLineKey.WANT
    split = fixed_split(
        case.class_means[budget_class], line, _series(case), case.window, case.overrides
    )
    expect = getattr(case.expect, budget_class.value)
    got = [
        {
            "key": names[row.key],
            "label": row.label,
            "interval": row.interval,
            "state": row.state,
            "monthly": row.monthly,
            "mean": row.mean,
            "change": row.change,
            "since": row.since,
        }
        for row in split.fixed
    ]
    assert got == [row.model_dump() for row in expect.fixed]
    assert split.variable == expect.variable
    assert sum((r.mean for r in split.fixed), split.variable) == case.class_means[budget_class]


def test_one_off_payment_leaves_the_mean():
    """A payment marked one-off (D297) left its class, so it leaves the series' mean too and
    the variable part keeps the class mean."""
    case = next(c for c in CASES if c.id == "kein_abo_counts_variable")
    marked = phase10.fixture_id("NFX_04")
    split = fixed_split(
        Decimal("20.32"),  # the want mean with NFX_04's 13.99 gone: 60.95 ÷ 3
        BudgetLineKey.WANT,
        _series(case),
        case.window,
        {},
        one_off_ids={marked},
    )
    assert [r.mean for r in split.fixed] == [Decimal("9.33")]  # 27.98 ÷ 3 = 9.3266…
    assert split.variable == Decimal("10.99")


def test_only_need_and_want():
    with pytest.raises(ValueError):
        fixed_split(Decimal("0"), BudgetLineKey.SAVING, [], ["2026-11"], {})
