"""The goal's path (docs/phases/phase-10.md §7.11 (i), D311), pinned by
tests/fixtures/phase10/goal_path.yaml: the ceiling, the room, step 1 at the typed rates,
step 2 against the invested mix's history, the word, the why, the wants budget, and the
tracking rows since the day adopted."""

from decimal import Decimal

import fixture_models_phase10 as phase10
import pytest
from test_phase10_goal import _route_inputs

from hausbuch.rules.goal import household_goal
from hausbuch.rules.goal import path as PA
from hausbuch.schemas.goal import GoalWord
from hausbuch.schemas.plan import GoalPathState

FIXTURE = phase10.load("goal_path")


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_goal_path(case) -> None:
    inputs, _, _ = _route_inputs(case)
    got = household_goal(inputs)
    want = case.expect
    assert got.word.value == want.word
    path, expect = got.goal_path, want.path
    assert path is not None
    assert path.state.value == expect.state
    c, ce = path.ceiling, expect.ceiling
    assert (
        c.income,
        c.need,
        c.wants_kept,
        c.other_spending,
        c.suggested,
        c.buffer,
        c.ceiling,
    ) == (
        ce.income,
        ce.need,
        ce.wants_kept,
        ce.other_spending,
        ce.suggested,
        ce.buffer,
        ce.ceiling,
    )
    rows = c.income + c.rental_cash - c.need - c.wants_kept - c.other_spending - c.buffer
    assert rows == c.ceiling, "the ceiling's rows add up to it"
    for name in (
        "anlegbar",
        "room",
        "extra",
        "monthly",
        "rate",
        "return_needed",
        "window_years",
        "windows",
        "reached_windows",
        "short_history",
        "wants_budget_total",
        "adoptable",
    ):
        assert getattr(path, name) == getattr(expect, name), name
    assert (tuple(path.years) if path.years else None) == expect.years
    assert {b.code: b.budget for b in path.wants_budget} == expect.wants_budget
    if expect.why is None:
        assert path.why is None
    else:
        assert (path.why.due_value, path.why.reached) == (expect.why.due_value, expect.why.reached)


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_the_word_is_the_paths_state(case) -> None:
    got = household_goal(_route_inputs(case)[0])
    assert got.word is PA_WORD[got.goal_path.state]
    if got.goal_path.state is GoalPathState.HARDLY:
        assert got.unreachable is not None  # the existing card keeps its fields


PA_WORD = {
    GoalPathState.ON_TRACK: GoalWord.ON_TRACK,
    GoalPathState.DOABLE_RATES: GoalWord.FEASIBLE,
    GoalPathState.DOABLE_RETURN: GoalWord.FEASIBLE,
    GoalPathState.AMBITIOUS: GoalWord.AMBITIOUS,
    GoalPathState.HARDLY: GoalWord.HARDLY,
}


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_a_paths_cut_is_a_valid_budget(case) -> None:
    """The adopted cut is stored as spending changes (D263): one per cut want, the budget it
    leaves, from the first month of the walk."""
    got = household_goal(_route_inputs(case)[0]).goal_path
    budgets = {b.code: b.budget for b in got.wants_budget}
    assert {s.code: s.monthly for s in got.spending_changes} == budgets


def test_generic_mode_never_names_a_return() -> None:
    for case in FIXTURE.cases:
        if case.mode.value != "generic":
            continue
        path = household_goal(_route_inputs(case)[0]).goal_path
        assert path.state in (GoalPathState.ON_TRACK, GoalPathState.DOABLE_RATES) or (
            path.state is GoalPathState.HARDLY
        )
        assert (path.return_needed, path.windows, path.years) == (None, None, None)


@pytest.mark.parametrize("case", FIXTURE.tracking, ids=lambda c: c.id)
def test_path_tracking(case) -> None:
    a = case.adopted
    months = [
        PA.PathMonth(
            month_end=m.month_end,
            saved=m.saved,
            invested=m.invested,
            crypto_bought=m.crypto_bought,
            wants=m.wants,
            net_worth=m.net_worth,
            path_net_worth=m.path_net_worth,
            value_now=m.value_now,
            flows=m.flows,
        )
        for m in case.months
    ]
    rows, points = PA.track(
        a.day, a.monthly, a.wants_budget, a.rate, a.crypto_percent, case.value_then, months
    )
    assert len(rows) == len(points) == len(case.expect)
    for row, point, want in zip(rows, points, case.expect, strict=True):
        assert row.month_end == want.month_end
        for name in (
            "waiting",
            "invested_gap",
            "wants_gap",
            "net_worth_gap",
            "value_change",
            "implied_change",
            "actual_percent",
            "needed_percent",
            "value_behind",
        ):
            assert getattr(row, name) == getattr(want, name), (want.month_end, name)
        assert row.word.value == want.word
        assert (point.needed, point.actual, point.behind) == (
            want.needed_percent,
            want.actual_percent,
            want.value_behind,
        )


def test_moved_when_anlegbar_drifts_past_the_room() -> None:
    from datetime import date

    from hausbuch.schemas.plan import Goal, GoalKind, GoalPathLever, ValueChange

    goal = Goal(
        kind=GoalKind.NET_WORTH,
        target=Decimal("100000.00"),
        due_month="2030-12",
        set_on=date(2026, 11, 1),
    )
    vc = ValueChange(depot=Decimal("0.06"))
    lever = GoalPathLever(
        adopted_on=date(2026, 11, 27),
        state=GoalPathState.DOABLE_RATES,
        monthly=Decimal("2234.00"),
        extra=Decimal("234.00"),
        rate=Decimal("0.060"),
        wants_budget_total=Decimal("666.00"),
        goal=goal,
        value_change=vc,
        anlegbar=Decimal("2000.00"),
        room=Decimal("450.00"),
    )
    assert not PA.moved(lever, goal, vc, None, Decimal("2450.00"))
    assert PA.moved(lever, goal, vc, None, Decimal("2450.01"))
    assert PA.moved(lever, goal, ValueChange(depot=Decimal("0.07")), None, Decimal("2000.00"))
    assert PA.moved(lever, goal, vc, Decimal("20.0"), Decimal("2000.00"))
