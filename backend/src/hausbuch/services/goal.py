"""GET /household-goal (docs/phases/phase-10.md §7.9 (d), D289, D298).

The goal is a lever of the default household scenario (schemas.plan.Goal); without a default
scenario or without a goal in it the answer is the word `none` („Ziel festlegen“). Otherwise
phase 9's plan is computed once (services.plan.plan_parts) and rules/goal reads its parts:
„Heute im Monat“ is the mean of the window's `saved` (phase 9's household month, to the cent
half-up), the value today the net worth today (a net-worth goal) or the reserve accounts
today (D237), the monthly need the reserve's (D238), the codes' classes the Budget's (D272,
the household's moves included). A goal set while the plan has to-dos (below three closed
months, no stored month-end, a property without a scenario, a rate not typed) gets no word:
`issue` no_projection with those to-dos.

Saving a goal and „Als Budget übernehmen“ use the household scenarios' own create endpoint
(POST /household-scenarios, superseding the default): the goal is `levers.goal`, and adopting
a scenario writes its `spending_changes` as the levers' spending changes and
`levers.goal_choice` with the day chosen; services.household_scenarios checks the goal.
"""

from datetime import date

from sqlalchemy import Connection

from hausbuch.rules.budget import budget as B
from hausbuch.rules.goal import GoalInputs, current_month, household_goal, none_set
from hausbuch.rules.plan import spending as SP
from hausbuch.rules.plan.surplus import mean_cent
from hausbuch.schemas.goal import GoalIssue, HouseholdGoal
from hausbuch.schemas.plan import GoalKind

from .health import _reserve_balances, _window, key_figures, levers_one_off_codes
from .plan import _net_worth, _scenario, plan_parts
from .plan_inputs import PlanInputs


def goal(conn: Connection, today: date) -> HouseholdGoal:
    inputs = PlanInputs(conn, today)  # the plan's reads, kept (phase 10 §7.10 (b))
    row = _scenario(conn, None, inputs)
    if row is None or row.levers.goal is None:
        return none_set()
    levers = row.levers
    codes = levers_one_off_codes(levers)  # phase 10 D307
    reads = inputs.ended_reads(codes)
    plan, parts = plan_parts(conn, today, row, levers, reads, inputs)
    if parts is None:  # below three closed months: the plan's to-dos say what is missing
        assert plan.projection is not None
        passed = levers.goal.due_month <= current_month(today)
        return HouseholdGoal(
            word=None,
            goal=levers.goal,
            choice=levers.goal_choice,
            issue=GoalIssue.DUE_PASSED if passed else GoalIssue.NO_PROJECTION,
            todos=[] if passed else list(plan.projection.todos),
        )
    now = _net_worth(inputs, today)
    if levers.goal.kind is GoalKind.NET_WORTH:
        value_today = now.total
    else:
        rows = inputs.part(
            ("reserve_balances", ("net_worth", today)), lambda c: _reserve_balances(c, now)
        )
        reserve = key_figures(
            conn,
            reads,
            _window(reads),
            levers,
            balances=now,
            debt_figure=None,
            today=today,
            balance_rows=rows,
        ).reserve
        assert reserve is not None  # three closed months, as parts
        value_today = reserve.amount
    classes = {
        code: cls
        for code in parts.means
        if (cls := B.class_of(code, levers.budget_class_overrides, codes)) is not None
    }
    todos = () if parts.projection is not None or plan.projection is None else plan.projection.todos
    return household_goal(
        GoalInputs(
            today=today,
            goal=levers.goal,
            choice=levers.goal_choice,
            heute=mean_cent([m.saved for m in parts.months]),
            value_today=value_today,
            need=parts.reserve.need,
            code_means=parts.means,
            classes=classes,
            household_changes=tuple(SP.in_play(levers.spending_changes, codes)),
            projection=parts.projection,
            todos=tuple(todos),
        )
    )
