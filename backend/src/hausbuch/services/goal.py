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
worth, the depot class less today's lines of the depots ticked into the reserve (D304; the
reserve's own balances are the stored month-end's, so they are not subtracted from today's
figure) for the funds, and each valued line of the crypto class by its coin's symbol, a token
priced as a coin (WETH, WSOL, Portal ETH) under that coin's, euros on an exchange or on their
way to one under „EUR“; a coin not valued today, a spam token and a token without a symbol
count nowhere in the mix's coins (a symbol-less token has no series to look up). `history`:
the coins' committed series and the funds series of the workspace's newest download
(services.reference_returns), none before the first (route 3 then says `series_missing`).
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import Connection, select
from sqlalchemy.orm import aliased

from hausbuch.db.tables import asset as asset_table
from hausbuch.rules.budget import budget as B
from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_PATH_INVALID
from hausbuch.rules.goal import GoalInputs, current_month, household_goal, none_set
from hausbuch.rules.goal import history as H
from hausbuch.rules.goal import path as PA
from hausbuch.rules.goal import series as reference_series
from hausbuch.rules.goal.goal import path_months
from hausbuch.rules.plan import spending as SP
from hausbuch.rules.plan.surplus import mean_cent
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.goal import (
    GoalInvestment,
    GoalIssue,
    GoalPathAdopted,
    GoalPathToday,
    HouseholdGoal,
)
from hausbuch.schemas.health import HealthTodo, HealthTodoCode, Reserve
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.net_worth import NetWorth
from hausbuch.schemas.plan import (
    GoalChoice,
    GoalKind,
    GoalPathBudgetChange,
    GoalPathLever,
    GoalPathMonth,
    GoalPathState,
    GoalSpending,
    HouseholdLevers,
    HouseholdPlan,
    HouseholdScenario,
    HouseholdScenarioCreate,
    SpendingChange,
    SurplusKey,
)

from . import household_scenarios as scenarios
from . import reference_returns
from .context import CommandContext
from .documents import LOCAL_TIME_ZONE
from .errors import RuleViolationError
from .health import MonthRead, _reserve_balances, _window, key_figures, levers_one_off_codes
from .plan import PlanParts, _net_worth, _scenario, advice_mode, plan_parts, previewed
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
    built = goal_inputs(conn, today, row, levers, reads, inputs)
    if isinstance(built, HouseholdGoal):
        return built
    return with_path(household_goal(built.goal), built, levers)


# --- The path's reads (§7.11 (i) items 5, 7) ---------------------------------------------------

PLAN_PAGE_HREF = "/household/plan"
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def plan_check_todo() -> HealthTodo:
    """„Plan prüfen“ (§7.11 (i) 5): the adopted path was computed from figures that moved; the
    path stays as adopted until the household adopts it again."""
    return HealthTodo(
        code=HealthTodoCode.PLAN_CHECK,
        text=(
            "Seit ihr den Plan übernommen habt, hat sich geändert, womit er gerechnet wurde "
            "(das Ziel, die Renditen, das Budget für Wünsche oder eure Monate). Prüft den Plan "
            "und übernehmt ihn neu, wenn er passt."
        ),
        action="Plan prüfen",
        href=PLAN_PAGE_HREF,
    )


def with_path(answer: HouseholdGoal, built: "GoalReads", levers: HouseholdLevers) -> HouseholdGoal:
    """The path's „Heute angelegt“ (item 7: the window's purchases a month, the detected savings
    plans, the saved but not invested) and, with a path adopted, the stored path with whether
    its inputs moved (item 5), the to-do „Plan prüfen“ then added while the path is the return
    followed. Without a path unchanged."""
    path = answer.goal_path
    if path is None:
        return answer
    months = len(built.parts.months)
    prefill = built.plan.investment_split_prefill
    bought = ZERO if prefill is None else sum(prefill.bought.values(), ZERO)
    invested = (bought / months).quantize(CENT, ROUND_HALF_UP) if months else ZERO
    saved = built.goal.heute
    generic = built.goal.mode is AdviceMode.GENERIC
    update: dict = {
        "today": GoalPathToday(
            invested=invested,
            saved=saved,
            saved_not_invested=max(ZERO, saved - invested),
            plans=[] if generic else list(built.plan.plans),
        )
    }
    todos = list(answer.todos)
    lever = levers.goal_path
    if lever is not None:
        moved = PA.moved(
            lever,
            levers.goal,
            levers.value_change,
            levers.budget_targets.wants_max,
            path.anlegbar,
            levers.investment_split,
        )
        update["adopted"] = GoalPathAdopted(
            adopted_on=lever.adopted_on,
            state=lever.state,
            monthly=lever.monthly,
            extra=lever.extra,
            rate=lever.rate,
            return_needed=lever.return_needed,
            windows=lever.windows,
            reached_windows=lever.reached_windows,
            wants_budget_total=lever.wants_budget_total,
            moved=moved,
        )
        choice = levers.goal_choice
        if moved and choice is not None and choice.investment == GoalInvestment.PATH.value:
            # „Plan prüfen“ only while the path is the route followed: after another route was
            # chosen the stored path is history, shown but no longer a plan to check
            todos.append(plan_check_todo())
    return answer.model_copy(update={"goal_path": path.model_copy(update=update), "todos": todos})


def adopt_path(ctx: CommandContext) -> HouseholdScenario:
    """POST /household-goal/path/adopt (§7.11 (i) 5, D311): „Diesen Plan übernehmen“. The goal
    of the default scenario in force is computed again here, on today's data, and its path of
    step 1 or step 2 is written into one new row superseding the default (D248, append-only,
    with its `household_scenario.created` event): the lever `goal_path` (what the path counts
    on, what it was computed from and its walk to the due month), the wants budget as the
    spending changes (as „Als Budget übernehmen“ stores them; on track the household's own
    budget is kept), and `goal_choice` with the return followed `path` and today's day. The
    browser sends nothing. RuleViolationError household_scenario.path_invalid without a goal
    or a path that can be adopted, or when the path would invest less than nothing a month.
    A path adopted and followed whose inputs have not moved (rules/goal/path.py `moved`) is
    kept: adopting again writes nothing and returns the row in force. Adopted again after its
    inputs moved, the path is computed without the wants budget an earlier path wrote
    (`_without_path_budget`): under that budget the goal reads on track, and a path computed
    there would store only „Anlegbar“ as its monthly amount while the walk counts the cut."""
    conn = ctx.conn
    scenarios._lock(ctx)
    today = ctx.now.astimezone(LOCAL_TIME_ZONE).date()
    inputs = PlanInputs(conn, today)
    row = scenarios.default_scenario(conn)
    if row is None or row.levers.goal is None:
        raise _refused("there is no goal in the default scenario")
    levers = row.levers
    basis = _without_path_budget(levers)
    reads = inputs.ended_reads(levers_one_off_codes(basis))
    built = goal_inputs(conn, today, row, basis, reads, inputs)
    if isinstance(built, HouseholdGoal):
        raise _refused("the goal has no answer yet")
    answer = with_path(household_goal(built.goal), built, levers)
    path = answer.goal_path
    if path is None:
        raise _refused("the goal has no path that can be adopted")
    stored = levers.goal_path
    choice = levers.goal_choice or GoalChoice()
    if (
        stored is not None
        and choice.investment == GoalInvestment.PATH.value
        and not PA.moved(
            stored,
            levers.goal,
            levers.value_change,
            levers.budget_targets.wants_max,
            path.anlegbar,
            levers.investment_split,
        )
    ):
        # Adopted and followed, its inputs unmoved: the path stays as adopted. The answer read
        # under the adopted wants budget may differ (it reads „Auf Kurs“ once the budget's cut
        # is in the scenario), which is no reason to write a second path.
        return row
    if not path.adoptable:
        raise _refused("the goal has no path that can be adopted")
    assert path.monthly is not None and path.extra is not None
    assert path.wants_budget_total is not None
    if path.monthly < 0:
        raise _refused("the path would invest less than nothing a month")
    choice = basis.goal_choice or GoalChoice()
    changes = basis.spending_changes
    budget: list[GoalPathBudgetChange] = []
    # A path whose wants budget cuts nothing (on track, or step 2 with no room) leaves the
    # household's spending changes and its spending choice exactly as they are (Q-O193).
    writes_budget = path.state is not GoalPathState.ON_TRACK and bool(path.spending_changes)
    if writes_budget:
        changes = list(path.spending_changes)
        budget = [_budget_change(c) for c in changes]
    lever = GoalPathLever(
        adopted_on=today,
        state=path.state,
        monthly=path.monthly,
        extra=path.extra,
        rate=path.rate,
        return_needed=path.return_needed,
        windows=path.windows,
        reached_windows=path.reached_windows,
        wants_budget_total=path.wants_budget_total,
        goal=levers.goal,
        value_change=levers.value_change,
        wants_max=levers.budget_targets.wants_max,
        anlegbar=path.anlegbar,
        room=path.room,
        investment_split=levers.investment_split,
        budget=budget,
        months=[GoalPathMonth(month=m, net_worth=v) for m, v in path_months(built.goal, path)],
    )
    update: dict = {
        "investment": GoalInvestment.PATH.value,
        "investment_chosen_on": today,
    }
    if writes_budget:
        update |= {"spending": GoalSpending.OWN, "spending_chosen_on": today}
    new = levers.model_copy(
        update={
            "goal_path": lever,
            "spending_changes": changes,
            "goal_choice": choice.model_copy(update=update),
        }
    )
    data = HouseholdScenarioCreate(
        name=row.name,
        is_default=True,
        levers=HouseholdLevers.model_validate(new.model_dump(mode="json")),
        supersedes_id=row.id,
    )
    return scenarios.create_scenario(ctx, data, adopt=True)


def _without_path_budget(levers: HouseholdLevers) -> HouseholdLevers:
    """The levers without the wants budget an adopted path wrote: a path of step 1 or 2 stores
    its cut as the spending changes with the spending choice `own` (`adopt_path`) and keeps
    that budget on its lever. The spending changes are stripped only while they still are that
    budget, code by code at the same planned amount (their months and `mean_at_save` are not
    compared: „Durchspielen“ moves a first month to today's and a save rewrites the mean). A
    budget the household changed or saved since, on any day, is the household's and stays."""
    stored, choice = levers.goal_path, levers.goal_choice
    if stored is None or choice is None or stored.state is GoalPathState.ON_TRACK:
        return levers
    if choice.spending != GoalSpending.OWN:
        return levers
    current = [c for c in levers.spending_changes if c.monthly is not None]
    if len(current) != len(levers.spending_changes):
        return levers  # a percent change is the household's: the path writes amounts only
    if sorted((c.code.value, c.monthly) for c in current) != sorted(
        (b.code.value, b.monthly) for b in stored.budget
    ):
        return levers
    return levers.model_copy(
        update={
            "spending_changes": [],
            "goal_choice": choice.model_copy(update={"spending": None, "spending_chosen_on": None}),
        }
    )


def _budget_change(change: SpendingChange) -> GoalPathBudgetChange:
    assert change.monthly is not None  # the path writes planned amounts (rules/goal `_changes`)
    return GoalPathBudgetChange(code=change.code, monthly=change.monthly)


def _refused(message: str) -> RuleViolationError:
    return RuleViolationError([HOUSEHOLD_SCENARIO_PATH_INVALID], message)


@dataclass(frozen=True)
class GoalReads:
    """What the goal read beside its inputs, for „Euer Plan“ (services.plan_page): the plan,
    its parts and today's net worth."""

    goal: GoalInputs
    plan: HouseholdPlan
    parts: PlanParts
    now: NetWorth


def reserve_today(
    conn: Connection,
    today: date,
    levers: HouseholdLevers,
    reads: dict[str, MonthRead],
    inputs: PlanInputs,
    now: NetWorth,
) -> Reserve:
    """The reserve on today's balances (D237): a reserve goal's value today."""
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
    return reserve


def goal_inputs(
    conn: Connection,
    today: date,
    row: HouseholdScenario | None,
    levers: HouseholdLevers,
    reads: dict[str, MonthRead],
    inputs: PlanInputs,
) -> HouseholdGoal | GoalReads:
    """The goal's inputs and what they were read from; the answer itself while the plan has
    fewer than three closed months."""
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
        value_today = reserve_today(conn, today, levers, reads, inputs, now).amount
    classes = {
        code: cls
        for code in parts.means
        if (cls := B.class_of(code, levers.budget_class_overrides, codes)) is not None
    }
    todos = () if parts.projection is not None or plan.projection is None else plan.projection.todos
    gi = GoalInputs(
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
        invested=_invested(inputs, now, _reserve_depots(parts.reserve)),
        history=_history(inputs),
    )
    return GoalReads(goal=gi, plan=plan, parts=parts, now=now)


# --- Route 3's inputs (§7.11 (c) 3, D310 amended) ----------------------------------------------

_EURO = "EUR"  # an exchange's euros and money on its way to one: the euro's series, 0 %


def _reserve_depots(reserve: Reserve) -> frozenset[UUID]:
    """D304: the depots ticked „Zählt zur Reserve“."""
    return frozenset(
        a.account_id for a in reserve.accounts if a.counted and a.kind is AccountKind.DEPOT
    )


def _coin_symbols(conn: Connection, ids: tuple[UUID, ...]) -> dict[UUID, str]:
    """{asset id: the symbol route 3 looks its series up by}: a token priced as a coin by that
    coin's, else its own; a spam token and a token without a symbol are left out (a name is
    never a symbol: it could equal a ticker and borrow that coin's history)."""
    if not ids:
        return {}
    twin = aliased(asset_table)
    a = asset_table.c
    rows = conn.execute(
        select(a.id, a.symbol, a.spam, twin.c.symbol.label("twin"))
        .select_from(asset_table.outerjoin(twin, twin.c.id == a.priced_as_asset_id))
        .where(a.id.in_(ids))
    ).all()
    return {r.id: (r.twin or r.symbol).upper() for r in rows if not r.spam and (r.twin or r.symbol)}


def _invested(inputs: PlanInputs, now: NetWorth, reserve_depots: frozenset[UUID]) -> H.Invested:
    """Today's invested values from today's net worth (`now`), without the depots in the
    reserve (`reserve_depots`), whose lines are today's too."""
    in_reserve = sum(
        (
            line.value_eur
            for line in now.lines
            if line.net_worth_class is NetWorthClass.DEPOT
            and line.account_id in reserve_depots
            and line.value_eur is not None
        ),
        H.ZERO,
    )
    funds = now.by_class.get(NetWorthClass.DEPOT, H.ZERO) - in_reserve
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
