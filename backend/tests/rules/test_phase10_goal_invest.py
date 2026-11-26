"""„Für euer Ziel“ (phase 10 §7.9 (e), D298 (5)–(7)) against the fixture goal_invest.yaml, and
the route followed read from the goal's answer."""

from datetime import date
from decimal import Decimal

import fixture_models_phase10 as F
import pytest
from test_phase10_goal import _route_inputs

from hausbuch.rules.goal import tracker as T
from hausbuch.schemas.goal import (
    GoalBalanced,
    GoalInvestment,
    GoalMoreReturn,
    GoalTrackingReason,
    GoalWord,
    HouseholdGoal,
)

FIXTURE = F.load("goal_invest")
CASES = {c.id: c for c in FIXTURE.cases}


def _followed(case) -> T.Followed | GoalTrackingReason | None:
    f = case.followed
    if f is None:
        return None
    if isinstance(f, str):
        return GoalTrackingReason(f)
    return T.Followed(
        route=GoalInvestment(f.route),
        rate=f.rate,
        spending_changes=tuple(f.spending_changes),
        extra=f.saving_extra,
    )


def _actual(case) -> T.Actual | None:
    a = case.actual
    if a is None:
        return None
    return T.Actual(
        value_then=a.value_then,
        flows=[(fl.day, fl.amount) for fl in a.flows],
        value_now=a.value_now,
        unvalued=a.unvalued,
    )


def _inputs(case) -> T.TrackerInputs:
    p = None
    if case.levers.goal is not None:
        _, p, _ = _route_inputs(case)
    return T.TrackerInputs(
        today=case.today,
        mode=case.mode,
        goal=case.levers.goal,
        choice=case.levers.goal_choice,
        followed=_followed(case),
        projection=p,
        actual=_actual(case),
    )


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_tracker_fixture(case_id):
    case = CASES[case_id]
    got = T.tracking(_inputs(case)).model_dump(mode="json")
    want = case.expect.model_dump(mode="json")
    for key, value in want.items():
        if key == "short":
            assert got["short"] == value, (case_id, key)
            continue
        if value is None:
            assert got.get(key) is None, (case_id, key, got.get(key))
            continue
        assert got[key] == value, (case_id, key, got[key], value)


def _answer(**kw) -> HouseholdGoal:
    return HouseholdGoal(word=GoalWord("feasible") if "word" not in kw else kw.pop("word"), **kw)


def test_followed_of_route_3():
    more = GoalMoreReturn.model_construct(available=True, needed_return=Decimal("0.081"))
    got = T.followed_of(_answer(more_return=more), GoalInvestment.MORE_RETURN)
    assert got == T.Followed(route=GoalInvestment.MORE_RETURN, rate=Decimal("0.081"))
    thin = GoalMoreReturn.model_construct(available=False, needed_return=Decimal("0.081"))
    assert (
        T.followed_of(_answer(more_return=thin), GoalInvestment.MORE_RETURN)
        is GoalTrackingReason.ROUTE_GONE
    )
    assert T.followed_of(_answer(), GoalInvestment.MORE_RETURN) is GoalTrackingReason.ROUTE_GONE


def test_followed_of_balanced():
    b = GoalBalanced.model_construct(
        return_rate=Decimal("0.147"), saving_extra=Decimal("300.00"), spending_changes=[]
    )
    got = T.followed_of(_answer(balanced=b), GoalInvestment.BALANCED)
    assert got == T.Followed(
        route=GoalInvestment.BALANCED, rate=Decimal("0.147"), extra=Decimal("300.00")
    )
    typed = GoalBalanced.model_construct(
        return_rate=None, saving_extra=Decimal("300.00"), spending_changes=[]
    )
    assert (
        T.followed_of(_answer(balanced=typed), GoalInvestment.BALANCED)
        is GoalTrackingReason.ROUTE_GONE
    )
    closed_not = GoalBalanced.model_construct(
        return_rate=Decimal("0.147"), saving_extra=None, spending_changes=[]
    )
    assert (
        T.followed_of(_answer(balanced=closed_not), GoalInvestment.BALANCED)
        is GoalTrackingReason.ROUTE_GONE
    )
    # on track: no balanced route is computed, so none is followed
    assert (
        T.followed_of(_answer(word=GoalWord("on_track")), GoalInvestment.BALANCED)
        is GoalTrackingReason.ROUTE_GONE
    )


def test_followed_of_goal_issue():
    assert (
        T.followed_of(_answer(word=None), GoalInvestment.MORE_RETURN)
        is GoalTrackingReason.GOAL_ISSUE
    )


def test_unknown_stored_route_is_not_followed():
    from hausbuch.schemas.plan import GoalChoice

    choice = GoalChoice(investment="something_else", investment_chosen_on=date(2026, 12, 10))
    assert T.route_of(choice) is None


def test_implied_whole_years_compound():
    # two years and 10 days at 10 %: 1.21 × (1 + 0.1 × 10/365) − 1 on 1,000
    got = T.implied(Decimal("0.10"), [(date(2025, 1, 1), Decimal("1000.00"))], date(2027, 1, 11))
    assert got == Decimal("213.32")
