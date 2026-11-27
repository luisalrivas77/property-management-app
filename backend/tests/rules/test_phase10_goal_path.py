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
        "mix_today_share",
        "mix_future_share",
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
            principal=m.principal,
            invested_property=m.invested_property,
            invested=m.invested,
            crypto_bought=m.crypto_bought,
            wants=m.wants,
            net_worth=m.net_worth,
            path_net_worth=m.path_net_worth,
            value_now=m.value_now,
            flows=m.flows,
            invested_since=m.invested_since,
            crypto_since=m.crypto_since,
        )
        for m in case.months
    ]
    now = None if a.now is None else (a.now, a.now_crypto or Decimal("0.00"))
    rows, points = PA.track(
        a.day, a.monthly, a.wants_budget, a.rate, a.crypto_percent, case.value_then, months, now
    )
    assert len(rows) == len(points) == len(case.expect)
    for row, point, want in zip(rows, points, case.expect, strict=True):
        assert row.month_end == want.month_end
        assert row.planned == a.monthly
        for name in (
            "invest_now",
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
    assert not PA.moved(lever, goal, vc, None, Decimal("2450.00"), None)
    assert PA.moved(lever, goal, vc, None, Decimal("2450.01"), None)
    assert PA.moved(lever, goal, ValueChange(depot=Decimal("0.07")), None, Decimal("2000.00"), None)
    assert PA.moved(lever, goal, vc, Decimal("20.0"), Decimal("2000.00"), None)


def _lever(room: str, split=None):
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
        monthly=Decimal("2000.00"),
        extra=Decimal("0.00"),
        rate=Decimal("0.060"),
        wants_budget_total=Decimal("666.00"),
        goal=goal,
        value_change=vc,
        anlegbar=Decimal("2000.00"),
        room=Decimal(room),
        investment_split=split,
    )
    return lever, goal, vc


def test_moved_with_no_room_tolerates_five_percent_of_the_monthly_amount() -> None:
    """Q-O192: with room 0 the tolerance is 5 % of the stored monthly amount (100 of 2,000):
    a 1 % drift (20) raises no to-do, a 6 % drift (120) does, either way."""
    lever, goal, vc = _lever("0.00")
    for anlegbar, want in (
        ("2020.00", False),
        ("1980.00", False),
        ("2100.00", False),
        ("2100.01", True),
        ("2120.00", True),
        ("1880.00", True),
    ):
        assert PA.moved(lever, goal, vc, None, Decimal(anlegbar), None) is want, anlegbar
    # the room still wins when it is larger than 5 %
    lever, goal, vc = _lever("300.00")
    assert not PA.moved(lever, goal, vc, None, Decimal("2300.00"), None)
    assert PA.moved(lever, goal, vc, None, Decimal("2300.01"), None)


def test_moved_when_the_investment_split_changes() -> None:
    """Q-O195: the split lever against the stored split, by value; None equals None."""
    from hausbuch.schemas.plan import InvestmentSplit

    split = InvestmentSplit(depot=Decimal("80"), crypto=Decimal("20"))
    lever, goal, vc = _lever("0.00", split)
    same = InvestmentSplit.model_validate({"depot": "80.0", "crypto": "20.0"})
    assert not PA.moved(lever, goal, vc, None, Decimal("2000.00"), same)
    assert not PA.moved(lever, goal, vc, None, Decimal("2000.00"), split)
    other = InvestmentSplit(depot=Decimal("75"), crypto=Decimal("25"))
    assert PA.moved(lever, goal, vc, None, Decimal("2000.00"), other)
    assert PA.moved(lever, goal, vc, None, Decimal("2000.00"), None)
    lever, goal, vc = _lever("0.00", None)
    assert not PA.moved(lever, goal, vc, None, Decimal("2000.00"), None)
    assert PA.moved(lever, goal, vc, None, Decimal("2000.00"), split)


def test_jetzt_anlegen_split_to_the_cent_adding_up() -> None:
    """„Jetzt anlegen“: 1,000.05 € above the target at 33.3 / 66.7: funds 333.016… → 333.02
    half-up, crypto the rest 667.03, so the two add up; nothing without an amount above 0, a
    split or a target (the walk then invests none of it)."""
    from hausbuch.schemas.plan import InvestmentSplit

    split = InvestmentSplit(depot=Decimal("33.3"), crypto=Decimal("66.7"))
    target = Decimal("9000.00")
    assert PA.above_target_split(Decimal("1000.05"), split, target) == (
        Decimal("333.02"),
        Decimal("667.03"),
    )
    assert PA.above_target_split(Decimal("0.00"), split, target) is None
    assert PA.above_target_split(None, split, target) is None
    assert PA.above_target_split(Decimal("1000.05"), None, target) is None
    assert PA.above_target_split(Decimal("1000.05"), split, None) is None


# „Was ihr tun müsst“ (the plan panel's steps): pinned per state over the fixture's cases,
# and hand-worked on sample numbers below.
_ORDER = ["save_monthly", "invest_now", "keep_reserve", "wants_cut", "wants_none", "return_needed"]


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_the_plans_steps_per_state(case) -> None:
    inputs, _, _ = _route_inputs(case)
    path = household_goal(inputs).goal_path
    kinds = [a.kind.value for a in path.actions]
    assert kinds == sorted(kinds, key=_ORDER.index), "the steps keep their order"
    by = {k: [a for a in path.actions if a.kind.value == k] for k in _ORDER}
    # save a month: the path's monthly amount, else Anlegbar + the whole room; only above 0
    monthly = path.monthly if path.adoptable else path.anlegbar + path.room
    assert [a.amount for a in by["save_monthly"]] == ([monthly] if monthly > 0 else [])
    for a in path.actions:
        if a.amount_depot is not None:
            assert a.amount_depot + a.amount_crypto == a.amount, "the parts add up"
    # the reserve kept only when the walk invests above it (a net-worth goal with a split);
    # below its target the reserve is filled first, so the monthly saving names no parts
    p = inputs.projection
    walks = p is not None and p.split is not None and p.target is not None
    assert len(by["keep_reserve"]) == (1 if walks else 0)
    full = walks and p.reserve_start >= p.target
    for a in by["keep_reserve"]:
        assert a.within_target == full
    if not full:
        assert all(a.amount_depot is None for a in by["save_monthly"])
    assert by["invest_now"] == [], "no money above the target is given here"
    # the wants: one row per cut (on a path the wants budget), else one row that nothing is cut
    if path.adoptable:
        assert [(a.code, a.amount, a.mean) for a in by["wants_cut"]] == [
            (b.code, b.budget, b.mean) for b in path.wants_budget
        ]
    for a in by["wants_cut"]:  # every state: a real cut of a want, never „400 → 400“
        assert inputs.classes[a.code].value == "want"
        assert a.mean > 0 and 0 <= a.amount < a.mean
    assert len({a.code for a in by["wants_cut"]}) == len(by["wants_cut"])
    assert (len(by["wants_cut"]) > 0) != (len(by["wants_none"]) == 1)
    for a in by["wants_none"]:
        assert a.within_target == (a.amount <= path.ceiling.wants_kept)
        assert (a.wants_percent is None) == (inputs.wants_max is None)
    # the return needed only in step 2's states, as the path names it
    assert [a.rate for a in by["return_needed"]] == (
        [] if path.return_needed is None else [path.return_needed]
    )
    # the month the plan reaches the goal: on a path and at a return needed by the due month
    # (the return needed is the one that reaches it then), else the why's at the typed rates
    if path.adoptable or path.return_needed is not None:
        assert path.plan_reached is not None
        assert path.plan_reached <= inputs.goal.due_month
    else:
        assert path.plan_reached == path.why.reached


def test_the_plans_steps_on_sample_numbers() -> None:
    """1,000.00 € a month at 60 / 40 with savings plans of 100.00 into funds and 50.00 into
    crypto: the rest 850.00 at the split gives funds 100.00 + 510.00 = 610.00, crypto 390.00;
    2,500.00 € above the reserve target of 12,000.00: funds 1,500.00, crypto 1,000.00; one want
    cut from 300.00 to 180.00; the return needed 6.500 %."""
    from dataclasses import replace

    from hausbuch.schemas.allocation import AllocationCategory
    from hausbuch.schemas.goal import GoalBudget, GoalPathActionKind, GoalPathCeiling
    from hausbuch.schemas.household import BudgetClass
    from hausbuch.schemas.plan import InvestmentSplit

    d = Decimal
    code = AllocationCategory.HH_RESTAURANT
    ceiling = GoalPathCeiling(
        income=d("4000.00"),
        rental_cash=d("0.00"),
        need=d("2000.00"),
        wants_kept=d("400.00"),
        other_spending=d("0.00"),
        suggested=False,
        buffer=d("100.00"),
        ceiling=d("1500.00"),
    )
    cut = GoalBudget(code=code, budget_class=BudgetClass.WANT, mean=d("300.00"), budget=d("180.00"))
    figures = PA.PlanFigures(
        monthly=d("1000.00"),
        split=InvestmentSplit(depot=d("60"), crypto=d("40")),
        target=d("12000.00"),
        plans_depot=d("100.00"),
        plans_crypto=d("50.00"),
        above_target=d("2500.00"),
        cuts=[cut],
        wants_net=d("300.00"),
        ceiling=ceiling,
        wants_max=d("10"),
        return_needed=d("6.500"),
        reserve_start=d("14500.00"),  # the target plus the 2,500.00 above it
    )
    K = GoalPathActionKind
    got = [
        (a.kind, a.amount, a.amount_depot, a.amount_crypto, a.code, a.mean, a.rate)
        for a in PA.actions_of(figures)
    ]
    assert got == [
        (K.SAVE_MONTHLY, d("1000.00"), d("610.00"), d("390.00"), None, None, None),
        (K.INVEST_NOW, d("2500.00"), d("1500.00"), d("1000.00"), None, None, None),
        (K.KEEP_RESERVE, d("12000.00"), None, None, None, None, None),
        (K.WANTS_CUT, d("180.00"), None, None, code, d("300.00"), None),
        (K.RETURN_NEEDED, None, None, None, None, None, d("6.500")),
    ]
    # nothing cut: one row with the wants' net mean 300.00, within the 400.00 kept at 10 %
    none = PA.actions_of(replace(figures, cuts=[], return_needed=None))
    last = none[-1]
    assert (last.kind, last.amount, last.within_target, last.wants_percent) == (
        K.WANTS_NONE,
        d("300.00"),
        True,
        d("10"),
    )
    # wants of 450.00 above the 400.00 kept and nothing cut (on track), at the suggestion
    above = PA.actions_of(
        replace(figures, cuts=[], wants_net=d("450.00"), wants_max=None, return_needed=None)
    )[-1]
    assert (above.amount, above.within_target, above.wants_percent) == (d("450.00"), False, None)
    # no split (a goal of a sum or the reserve): no parts, nothing invested now, no reserve row
    kept = PA.actions_of(replace(figures, split=None))
    assert [(a.kind, a.amount_depot) for a in kept] == [
        (K.SAVE_MONTHLY, None),
        (K.WANTS_CUT, None),
        (K.RETURN_NEEDED, None),
    ]
    # the reserve 3,000.00 below its target: the walk fills it first, so the monthly saving
    # names no parts, nothing is invested now and the reserve row says it is not full yet
    low = PA.actions_of(replace(figures, reserve_start=d("9000.00"), above_target=None))
    assert [(a.kind, a.amount_depot, a.within_target) for a in low][:2] == [
        (K.SAVE_MONTHLY, None, None),
        (K.KEEP_RESERVE, None, False),
    ]
    assert next(a for a in PA.actions_of(figures) if a.kind is K.KEEP_RESERVE).within_target is True
    # on track with an adopted budget: the wants row says the plan counts on it
    budget = PA.actions_of(replace(figures, cuts=[], return_needed=None, adopted_budget=True))
    assert budget[-1].kind is K.WANTS_NONE and budget[-1].adopted_budget is True
    # the plans alone above the monthly amount: no parts rather than a negative rest
    plans_above = PA.monthly_split(d("120.00"), figures.split, figures.target, d("100"), d("50"))
    assert plans_above is None
