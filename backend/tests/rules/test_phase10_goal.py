"""rules/goal against tests/fixtures/phase10/goal.yaml (docs/phases/phase-10.md §7.9 (d); D289,
D298), the monthly walk against phase 9's net-worth projection, and rule_version() unchanged."""

import fixture_models_phase10 as phase10
import pytest
from test_phase9_plan import PROJECTION, basis, run_case, scenario_of, start_of

from hausbuch.rules.budget import budget as B
from hausbuch.rules.goal import GoalInputs, Projection, household_goal, none_set, walk
from hausbuch.rules.plan import levers as L
from hausbuch.rules.plan import projection as N
from hausbuch.schemas.goal import GoalSpending, GoalWord
from hausbuch.schemas.plan import HouseholdLevers
from hausbuch.services.health import rule_version

GOAL = phase10.load("goal").cases

# services.health.rule_version() on main after PR #590 (§7.9 (c) changed rules/health and
# rules/plan once). rules/goal lies outside the hashed folders, so §7.9 (d) leaves it as it was;
# a change of rules/health or rules/plan must update this value on purpose (D242).
RULE_VERSION = "3d24e195585146a011f8ec677ef054a8d19cccd92998db4b178c357317f4551d"


def _projection(case) -> Projection:
    m = case.month
    month_basis = L.MonthBasis(
        newest_closed=case.newest_closed,
        mean_income=m.mean_income,
        mean_regular_spending=m.mean_regular_spending,
        mean_rental_cash=m.mean_rental_cash,
        buffer_percent=m.buffer_percent,
        spending_changes=tuple(case.levers.spending_changes),
        code_means=dict(case.means),
    )
    s = case.start
    start = N.ProjectionStart(
        month_end=s.month_end,
        bank=s.bank,
        depot=s.depot,
        crypto=s.crypto,
        property=s.property,
        loans=s.loans,
        other_liabilities=s.other_liabilities,
    )
    return Projection(
        basis=month_basis,
        start=start,
        reserve_start=case.reserve.amount,
        plans=(),
        flats={},
        levers=case.levers,
    )


def _answer(case):
    levers = case.levers
    if levers.goal is None:
        return none_set()
    classes = {
        code: cls
        for code in case.means
        if (cls := B.class_of(code, levers.budget_class_overrides)) is not None
    }
    value_today = (
        case.value_today.net_worth
        if levers.goal.kind.value == "net_worth"
        else case.value_today.reserve
    )
    return household_goal(
        GoalInputs(
            today=case.today,
            goal=levers.goal,
            choice=levers.goal_choice,
            heute=case.saved_mean,
            value_today=value_today,
            need=case.reserve.need,
            code_means=dict(case.means),
            classes=classes,
            household_changes=tuple(levers.spending_changes),
            projection=_projection(case),
        )
    )


@pytest.mark.parametrize("case", GOAL, ids=lambda c: c.id)
def test_goal(case) -> None:
    got = _answer(case)
    want = case.expect
    assert got.word.value == want.word
    for field in (
        "target",
        "value_today",
        "percent",
        "months_left",
        "heute",
        "projected",
        "noetig",
        "reached",
        "reached_with_budget",
    ):
        assert getattr(got, field) == getattr(want, field), field
    assert got.issue is None
    scenarios = {s.kind.value: s for s in got.scenarios}
    assert set(scenarios) == set(want.scenarios)
    for kind, expected in want.scenarios.items():
        s = scenarios[kind]
        assert s.closes is expected.closes, kind
        assert (s.cut, s.rest, s.saving) == (expected.cut, expected.rest, expected.saving), kind
        assert {b.code: b.budget for b in s.budgets} == expected.budgets, kind
        assert [(c.code, c.monthly, c.from_month, c.to_month) for c in s.spending_changes] == [
            (c.code, c.monthly, c.from_month, c.to_month) for c in expected.changes
        ], kind
    if want.unreachable is None:
        assert got.unreachable is None
    else:
        assert got.unreachable is not None
        assert got.unreachable.later == want.unreachable.later
        assert got.unreachable.reaches_by_due == want.unreachable.reaches_by_due


@pytest.mark.parametrize("case", [c for c in GOAL if c.expect.scenarios], ids=lambda c: c.id)
def test_a_scenario_is_a_valid_budget(case) -> None:
    """„Als Budget übernehmen“ stores a scenario's changes as the levers' spending changes; the
    levers' own checks (one code per group, a planned amount ≥ 0) accept every one."""
    for s in _answer(case).scenarios:
        levers = HouseholdLevers.model_validate(
            {
                **case.levers.model_dump(mode="json"),
                "spending_changes": [c.model_dump(mode="json") for c in s.spending_changes],
                "goal_choice": {"spending": s.kind.value, "spending_chosen_on": "2026-11-24"},
            }
        )
        assert levers.goal_choice.spending is GoalSpending(s.kind)


def test_without_a_goal_the_word_is_none() -> None:
    assert none_set().word is GoalWord.NONE


@pytest.mark.parametrize(
    "case",
    [c for c in PROJECTION.cases if c.levers is not None and not c.expect.todos],
    ids=lambda c: c.id,
)
def test_the_walk_equals_phase_9_at_every_december(case) -> None:
    """The monthly walk repeats phase 9's projection: at each 31 December its net worth is
    phase 9's point, and its reserve row phase 9's as_is row month by month."""
    run = run_case(case)
    levers = HouseholdLevers(
        value_change=case.levers.value_change,
        net_worth_years=case.levers.net_worth_years,
        income_changes=case.levers.income_changes,
        one_offs=case.levers.one_offs,
    )
    flats = {p: scenario_of(f) for p, f in case.flats.items()}
    p = Projection(
        basis=basis(case.newest_closed, case.month),
        start=start_of(case.start),
        reserve_start=case.reserve,
        plans=case.plans,
        flats={k: f for k, f in flats.items() if f is not None},
        levers=levers,
    )
    w = walk(p)
    assert run.projection.points, "a case with points"
    for point in run.projection.points:
        month = w.at(f"{point.on.year}-12")
        assert month is not None, point.on
        assert month.net_worth == point.total, point.on
    for ym, reserve in run.as_is.items():
        if w.used_up is not None and ym >= w.used_up:
            break
        assert w.at(ym).reserve == reserve, ym
    if run.projection.used_up is not None:  # phase 9 walks fewer years than the goal's walk
        assert w.used_up == run.projection.used_up


def test_rule_version_is_unchanged() -> None:
    rule_version.cache_clear()
    assert rule_version() == RULE_VERSION
