"""GET /household-goal (docs/phases/phase-10.md §7.9 (d), D289, D298), and
POST /household-goal/preview for typed levers (§7.10 (a), „Durchspielen“).

The goal is a lever of the default household scenario (schemas.plan.Goal); without a default
scenario or without a goal in it the answer is the word `none` („Ziel festlegen“). Otherwise
phase 9's plan is computed once (services.plan.plan_parts) and rules/goal reads its parts:
„Heute im Monat“ is the mean of the window's `saved` (phase 9's household month, to the cent
half-up), the value today the net worth today (a net-worth goal) or the reserve accounts
today (D237), the monthly need the reserve's (D238), the codes' classes the Budget's (D272,
the household's moves included). A goal set while the plan has to-dos (below three closed
months, no stored month-end, a property's scenario phase 4 refuses, a rate not typed) gets
no word: `issue` no_projection with those to-dos; a property without a scenario is kept
(D301 (a) 5) and the answer names that assumption. The mode (D251) says why route 3 is not
offered; the window's mean income bounds „Nötig“ (D302) and the wants target limits the
balanced route.

Saving a goal and „Als Budget übernehmen“ use the household scenarios' own create endpoint
(POST /household-scenarios, superseding the default): the goal is `levers.goal`, and adopting
a scenario writes its `spending_changes` as the levers' spending changes and
`levers.goal_choice` with the day chosen; services.household_scenarios checks the goal.

Route 3 (§7.11 (c) 3, D310 amended) reads two more lever-free parts. `invested`: today's net
worth, the depot class less the depots ticked into the reserve (D304, as the projection's
start) for the funds, and each valued line of the crypto class by its coin's symbol, a token
priced as a coin (WETH, WSOL, Portal ETH) under that coin's, euros on an exchange or on their
way to one under „EUR“; a coin not valued today counts nowhere, as in the net worth. `history`:
the coins' committed series and the funds series of the workspace's newest download
(services.reference_returns), none before the first (route 3 then says `series_missing`).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, select
from sqlalchemy.orm import aliased

from hausbuch.db.tables import asset as asset_table
from hausbuch.rules.budget import budget as B
from hausbuch.rules.goal import GoalInputs, current_month, household_goal, none_set
from hausbuch.rules.goal import history as H
from hausbuch.rules.goal import series as reference_series
from hausbuch.rules.plan import spending as SP
from hausbuch.rules.plan.surplus import mean_cent
from hausbuch.schemas.goal import GoalIssue, HouseholdGoal
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.net_worth import NetWorth
from hausbuch.schemas.plan import GoalKind, HouseholdLevers, HouseholdScenario, SurplusKey

from . import reference_returns
from .health import MonthRead, _reserve_balances, _window, key_figures, levers_one_off_codes
from .plan import _net_worth, _reserve_depot, _scenario, advice_mode, plan_parts, previewed
from .plan_inputs import PlanInputs


def goal(conn: Connection, today: date) -> HouseholdGoal:
    inputs = PlanInputs(conn, today)  # the plan's reads, kept (phase 10 §7.10 (b))
    row = _scenario(conn, None, inputs)
    if row is None or row.levers.goal is None:
        return none_set()
    levers = row.levers
    reads = inputs.ended_reads(levers_one_off_codes(levers))  # phase 10 D307
    return _goal(conn, today, row, levers, reads, inputs)


def preview_goal(
    conn: Connection, today: date, levers: HouseholdLevers, scenario_id: UUID | None = None
) -> HouseholdGoal:
    """POST /household-goal/preview (phase 10 §7.10 (a), „Durchspielen“): the goal for typed
    levers, as POST /household-plan/preview gives the plan for them: checked the same way, on
    the same row (`scenario_id`, else the default in force) with its levers replaced. Word
    `none` when the levers hold no goal. Reads only: no row, no event."""
    if levers.goal is None:
        return none_set()
    inputs = PlanInputs(conn, today)
    row, reads = previewed(conn, today, levers, scenario_id, inputs)
    return _goal(conn, today, row, levers, reads, inputs)


def _goal(
    conn: Connection,
    today: date,
    row: HouseholdScenario | None,
    levers: HouseholdLevers,
    reads: dict[str, MonthRead],
    inputs: PlanInputs,
) -> HouseholdGoal:
    assert levers.goal is not None
    codes = levers_one_off_codes(levers)  # phase 10 D307
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
            mean_income=plan.surplus.amount(SurplusKey.MEAN_INCOME),
            wants_max=levers.budget_targets.wants_max,
            mode=inputs.part(("advice_mode",), advice_mode),
            invested=_invested(inputs, now, _reserve_depot(parts.reserve)),
            history=_history(inputs),
        )
    )


# --- Route 3's inputs (§7.11 (c) 3, D310 amended) ----------------------------------------------

_EURO = "EUR"  # an exchange's euros and money on its way to one: the euro's series, 0 %


def _coin_symbols(conn: Connection, ids: tuple[UUID, ...]) -> dict[UUID, str]:
    """{asset id: the symbol route 3 looks its series up by}: a token priced as a coin by that
    coin's, else its own, else its name; a spam token is left out."""
    if not ids:
        return {}
    twin = aliased(asset_table)
    a = asset_table.c
    rows = conn.execute(
        select(a.id, a.symbol, a.name, a.spam, twin.c.symbol.label("twin"))
        .select_from(asset_table.outerjoin(twin, twin.c.id == a.priced_as_asset_id))
        .where(a.id.in_(ids))
    ).all()
    return {r.id: (r.twin or r.symbol or r.name).upper() for r in rows if not r.spam}


def _invested(inputs: PlanInputs, now: NetWorth, reserve_depot: Decimal) -> H.Invested:
    """Today's invested values from today's net worth (`now`)."""
    funds = now.by_class.get(NetWorthClass.DEPOT, H.ZERO) - reserve_depot
    lines = [
        line
        for line in now.lines
        if line.net_worth_class is NetWorthClass.CRYPTO and line.value_eur is not None
    ]
    ids = tuple(sorted({line.asset_id for line in lines if line.asset_id is not None}, key=str))
    symbols = inputs.part(("coin_symbols", ids), lambda c: _coin_symbols(c, ids))
    coins: dict[str, Decimal] = {}
    for line in lines:
        symbol = _EURO if line.asset_id is None else symbols.get(line.asset_id)
        if symbol is not None:
            coins[symbol] = coins.get(symbol, H.ZERO) + line.value_eur
    return H.Invested(funds=funds, coins=coins)


def _history(inputs: PlanInputs) -> H.History:
    """The committed coin series and the newest download's funds series, if any."""
    newest = inputs.part(("reference_returns",), reference_returns.newest)
    if newest is None:
        return reference_series.history(None)
    return reference_series.history(newest.rows, newest.read_on)
