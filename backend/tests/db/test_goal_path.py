"""Phase 10 §7.11 (i), D311, on the database: the goal's path on GET /household-goal (its
„Heute angelegt“, the stored path and whether it moved), POST /household-goal/path/adopt's
command (adopt, adopt again, refuse, the append-only supersede and its event), the to-do
„Plan prüfen“, a save that cannot write a path, and the tracking rows of an adopted path.
The arithmetic is tests/fixtures/phase10/goal_path.yaml (tests/rules/test_phase10_goal_path.py).
The world is test_goal_tracking's (test_plan_inputs._projected): rows INVENTED, round values."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_goal_tracking import _follow, _followed_world, _series
from db.test_household_goal import _goal
from db.test_plan_inputs import _projected
from db.test_services_health import _book
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import func, select, update

from hausbuch.api.kept_answers import read_data_version
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_PATH_INVALID
from hausbuch.rules.goal.goal import wants_net
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.budget import BudgetLineKey
from hausbuch.schemas.goal import (
    GoalInvestment,
    GoalPathActionKind,
    GoalSpending,
    GoalTrackingWord,
    GoalWord,
)
from hausbuch.schemas.health import HealthTodoCode
from hausbuch.schemas.plan import (
    GoalPathBudgetChange,
    GoalPathMonth,
    GoalPathState,
    HouseholdScenarioCreate,
    InvestmentSplit,
    SpendingChange,
    ValueChange,
)
from hausbuch.services import budget, goal_tracking, household_scenarios, net_worth, plan_page
from hausbuch.services import goal as goal_service
from hausbuch.services import plan as plan_service
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.health import levers_one_off_codes
from hausbuch.services.plan_inputs import PlanInputs

pytestmark = pytest.mark.db

# 320,000 € by December 2028: the typed 3 % misses it at today's 459 € a month, 72 € more
# (within the room of 135 €) reaches it, so step 1 is the path („Machbar“). 330,000 € is
# beyond step 1 and the funds' history (6 % a year) reaches it in no window: „Kaum
# erreichbar“, nothing to adopt.
DOABLE = "320000.00"
HARDLY = "330000.00"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _goal_world(world: World, target: str = DOABLE) -> None:
    _followed_world(world)
    _follow(world, None, goal=_goal(target))


def _answer(world: World):
    return world.query(goal_service.goal, TODAY)


def _adopt(world: World, **kw):
    return world.cmd(goal_service.adopt_path, **kw)


def _created_events(world: World, scenario_id) -> int:
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(func.count())
            .select_from(event_table)
            .where(
                event_table.c.type == "household_scenario.created",
                event_table.c.entity_id == scenario_id,
            )
        ).scalar_one()


def _save(world: World, **update):
    current = world.query(household_scenarios.default_scenario)
    return world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            supersedes_id=current.id,
            levers=current.levers.model_copy(update=update),
        ),
    )


def test_the_path_and_heute_angelegt_on_get(world):
    """The window's purchases (the depot's 50, 60 and 50 € and the broker's 25 € a month) a
    month, the mean saved and what is saved but not invested; both savings plans; no path
    adopted, so nothing moved and no „Plan prüfen“."""
    _goal_world(world)
    got = _answer(world)
    assert got.word is GoalWord.FEASIBLE
    path = got.goal_path
    assert path is not None and path.state is GoalPathState.DOABLE_RATES and path.adoptable
    assert (path.anlegbar, path.room, path.extra, path.monthly) == (
        Decimal("459.00"),
        Decimal("135.00"),
        Decimal("72.00"),
        Decimal("531.00"),
    )
    today = path.today
    assert today is not None
    assert (today.invested, today.saved, today.saved_not_invested) == (
        Decimal("78.33"),
        Decimal("510.00"),
        Decimal("431.67"),
    )
    assert sorted(p.monthly for p in today.plans) == [Decimal("25.00"), Decimal("50.00")]
    assert path.adopted is None
    assert HealthTodoCode.PLAN_CHECK not in {t.code for t in got.todos}


def test_jetzt_anlegen_is_the_plans_above_target_at_the_walks_split(world):
    """„Jetzt anlegen“ on the path's plan card: the plan's own `above_target` (the reserve
    above its target today), and its part in funds and in crypto at the split the walk invests
    in. Without a saved split the walk takes the holdings' mix, funds only here, so all of it
    goes to funds; with 60/40 saved and a rate typed for crypto too (a kind without a rate
    leaves the effective split, rules/investment_split.py), 12,365 × 60 % = 7,419 to funds
    and the rest, 4,946, to crypto."""
    _goal_world(world)
    above = world.query(plan_service.household_plan, TODAY).above_target
    assert above == Decimal("12365.00")
    path = _answer(world).goal_path
    assert path is not None
    assert (path.above_target, path.above_target_depot, path.above_target_crypto) == (
        above,
        above,
        Decimal("0.00"),
    )
    _save(
        world,
        investment_split=InvestmentSplit(depot=Decimal("60"), crypto=Decimal("40")),
        value_change=ValueChange(depot=Decimal("0.030"), crypto=Decimal("0.050")),
    )
    path = _answer(world).goal_path
    assert path is not None
    assert (path.above_target, path.above_target_depot, path.above_target_crypto) == (
        above,
        Decimal("7419.00"),
        Decimal("4946.00"),
    )
    # „Was ihr tun müsst“ carries the same figures: invest the 12,365 now at 7,419 / 4,946
    # and keep the reserve at the walk's target
    kinds = {a.kind: a for a in path.actions}
    now = kinds[GoalPathActionKind.INVEST_NOW]
    assert (now.amount, now.amount_depot, now.amount_crypto) == (
        above,
        Decimal("7419.00"),
        Decimal("4946.00"),
    )
    assert GoalPathActionKind.KEEP_RESERVE in kinds
    monthly = kinds[GoalPathActionKind.SAVE_MONTHLY]
    assert monthly.amount == path.monthly
    assert monthly.amount_depot + monthly.amount_crypto == monthly.amount
    assert path.plan_reached is not None


def test_adopt_supersedes_the_default_once_with_its_event(world):
    _goal_world(world)
    before = world.query(household_scenarios.default_scenario)
    path = _answer(world).goal_path
    version = read_data_version(world.engine, world.ws)
    row = _adopt(world)
    assert read_data_version(world.engine, world.ws) != version  # kept answers recomputed
    assert row.supersedes_id == before.id and row.is_default and row.name == before.name
    lever = row.levers.goal_path
    assert lever is not None
    assert (lever.adopted_on, lever.state, lever.monthly, lever.extra, lever.room) == (
        TODAY,
        GoalPathState.DOABLE_RATES,
        Decimal("531.00"),
        Decimal("72.00"),
        Decimal("135.00"),
    )
    assert (lever.goal, lever.value_change) == (before.levers.goal, before.levers.value_change)
    # its walk, from the month after the newest closed one to the due month
    assert lever.months[0].month == "2027-04" and lever.months[-1].month == "2028-12"
    assert lever.months[-1].net_worth >= Decimal(DOABLE)
    choice = row.levers.goal_choice
    assert (choice.investment, choice.investment_chosen_on) == (GoalInvestment.PATH.value, TODAY)
    assert (choice.spending, choice.spending_chosen_on) == (GoalSpending.OWN, TODAY)
    assert [(s.code, s.monthly) for s in row.levers.spending_changes] == [
        (s.code, s.monthly) for s in path.spending_changes
    ]
    assert _created_events(world, row.id) == 1
    history = world.query(household_scenarios.list_scenarios, include_history=True)
    assert before.id in {r.id for r in history} and before.id != row.id  # never changed
    # the answer now names the stored path, unmoved
    got = _answer(world)
    assert got.goal_path.adopted is not None and not got.goal_path.adopted.moved
    assert got.goal_path.adopted.monthly == Decimal("531.00")
    assert HealthTodoCode.PLAN_CHECK not in {t.code for t in got.todos}


def test_adopting_again_with_nothing_moved_writes_nothing(world):
    """Once adopted, the goal read under the adopted wants budget is on track (the budget's
    cut is in the scenario); the path stays as adopted and a second adopt writes nothing."""
    _goal_world(world)
    first = _adopt(world)
    assert _answer(world).goal_path.state is GoalPathState.ON_TRACK
    again = _adopt(world)
    assert again.id == first.id and again.levers.goal_path == first.levers.goal_path
    assert world.query(household_scenarios.default_scenario).id == first.id


def test_adopting_again_after_a_move_computes_without_the_paths_own_budget(world):
    """A typed rate changed after adopting, then adopted again: the new path is computed
    from the measured household, not under the first path's wants budget (under which the goal
    reads on track at „Anlegbar“ alone), so it stores the extra its walk needs."""
    _goal_world(world)
    first = _adopt(world)
    _save(world, value_change=ValueChange(depot=Decimal("0.040")))
    second = _adopt(world)
    assert second.id != first.id
    lever = second.levers.goal_path
    assert lever.adopted_on == TODAY and lever.value_change.depot == Decimal("0.040")
    assert lever.state is not GoalPathState.ON_TRACK
    assert lever.extra > 0 and lever.monthly == lever.anlegbar + lever.extra
    assert second.levers.spending_changes  # its own cut, not the first path's carried
    assert _created_events(world, second.id) == 1


def test_the_lever_keeps_the_budget_it_wrote_and_the_split_of_that_day(world):
    _goal_world(world)
    split = InvestmentSplit(depot=Decimal("80"), crypto=Decimal("20"))
    _save(world, investment_split=split)
    row = _adopt(world)
    lever = row.levers.goal_path
    assert lever.investment_split == split
    assert lever.budget == [
        GoalPathBudgetChange(code=c.code, monthly=c.monthly) for c in row.levers.spending_changes
    ]
    assert lever.budget  # step 1 cuts the wants


def test_the_paths_budget_moved_to_another_first_month_is_still_the_paths(world):
    """„Durchspielen“ moves a change's first month to today's and a save rewrites its mean:
    the same codes at the same planned amounts are still the path's budget, stripped."""
    _goal_world(world)
    first = _adopt(world)
    rebased = [
        c.model_copy(update={"from_month": "2027-05"}) for c in first.levers.spending_changes
    ]
    row = _save(world, spending_changes=rebased)
    assert goal_service._without_path_budget(row.levers).spending_changes == []


def test_a_household_edit_of_the_paths_budget_survives_adopting_again(world):
    """After adopting, the household edits one change of the path's budget in „Durchspielen“
    (the screen carries goal_choice, already `own` of the adopt day). The goal is then lowered
    so it is on track: adopting again keeps the household's spending changes in force."""
    _goal_world(world)
    first = _adopt(world)
    edited = [
        first.levers.spending_changes[0].model_copy(
            update={
                "monthly": first.levers.spending_changes[0].monthly + Decimal("1.00"),
                "from_month": "2027-05",
            }
        ),
        *first.levers.spending_changes[1:],
    ]
    row = _save(world, spending_changes=edited)
    assert row.levers.goal_choice.spending_chosen_on == TODAY  # carried as the screen does
    assert goal_service._without_path_budget(row.levers) == row.levers
    _save(world, goal=_goal("300000.00"))
    second = _adopt(world)
    assert second.levers.goal_path.state is GoalPathState.ON_TRACK
    assert second.levers.goal_path.budget == []
    assert [(c.code, c.monthly) for c in second.levers.spending_changes] == [
        (c.code, c.monthly) for c in edited
    ]


def test_a_household_budget_saved_on_the_adopt_day_survives_adopting_again(world):
    """„Als Budget übernehmen“ or „Eigene Aufteilung“ on the day the path was adopted writes
    `own` and that same day: compared by amount, it is the household's and stays."""
    _goal_world(world)
    first = _adopt(world)
    code = first.levers.spending_changes[0].code
    own = [SpendingChange(code=code, monthly=Decimal("2.00"), from_month="2027-05")]
    choice = first.levers.goal_choice.model_copy(
        update={"spending": GoalSpending.OWN, "spending_chosen_on": TODAY}
    )
    _save(world, spending_changes=own, goal_choice=choice)
    _save(world, goal=_goal("300000.00"))
    second = _adopt(world)
    assert second.levers.goal_path.state is GoalPathState.ON_TRACK
    assert [(c.code, c.monthly) for c in second.levers.spending_changes] == [
        (code, Decimal("2.00"))
    ]


def test_a_path_that_cannot_be_adopted_is_refused(world):
    _followed_world(world)
    _save(world, goal=None)
    with pytest.raises(RuleViolationError) as refused:
        _adopt(world)
    assert refused.value.codes == (HOUSEHOLD_SCENARIO_PATH_INVALID,)
    _follow(world, None, goal=_goal(HARDLY))
    assert _answer(world).goal_path.state is GoalPathState.HARDLY
    with pytest.raises(RuleViolationError) as refused:
        _adopt(world)
    assert refused.value.codes == (HOUSEHOLD_SCENARIO_PATH_INVALID,)


def test_an_ordinary_save_cannot_write_a_path_but_carries_one(world):
    _goal_world(world)
    with pytest.raises(RuleViolationError) as refused:
        _follow(world, "path", chosen=TODAY)
    assert refused.value.codes == (HOUSEHOLD_SCENARIO_PATH_INVALID,)
    row = _adopt(world)
    lever = row.levers.goal_path
    doctored = lever.model_copy(update={"monthly": lever.monthly + 1, "extra": lever.extra + 1})
    with pytest.raises(RuleViolationError) as refused:
        _save(world, goal_path=doctored)
    assert refused.value.codes == (HOUSEHOLD_SCENARIO_PATH_INVALID,)
    carried = _save(world, net_worth_years=4)  # another lever saved, the path carried
    assert carried.levers.goal_path == lever
    assert carried.levers.goal_choice.investment == GoalInvestment.PATH.value
    dropped = _save(world, goal_path=None)  # dropping it is allowed
    assert dropped.levers.goal_path is None


def test_moved_inputs_raise_plan_pruefen_and_keep_the_path(world):
    """A typed rate changed after adopting: the path stays as adopted, the answer says it
    moved and „Plan prüfen“ opens „Euer Plan“ (its English: tests/test_i18n_todos.py)."""
    _goal_world(world)
    lever = _adopt(world).levers.goal_path
    row = _save(world, value_change=ValueChange(depot=Decimal("0.040")))
    assert row.levers.goal_path == lever
    got = _answer(world)
    assert got.goal_path.adopted is not None and got.goal_path.adopted.moved
    todo = next(t for t in got.todos if t.code is HealthTodoCode.PLAN_CHECK)
    assert (todo.action, todo.href) == ("Plan prüfen", "/household/plan")
    page = world.query(plan_page.plan_page, TODAY)
    assert HealthTodoCode.PLAN_CHECK in {t.code for t in page.todos}


def test_no_plan_pruefen_once_another_route_is_followed(world):
    """The path adopted, then route 3 followed instead: the stored path stays and reads moved
    after a typed rate changes, but „Plan prüfen“ is not raised for a plan no longer followed."""
    _goal_world(world)
    lever = _adopt(world).levers.goal_path
    _follow(world, "more_return", chosen=TODAY)
    _save(world, value_change=ValueChange(depot=Decimal("0.040")))
    got = _answer(world)
    assert world.query(household_scenarios.default_scenario).levers.goal_path == lever
    assert got.goal_path.adopted is not None and got.goal_path.adopted.moved
    assert HealthTodoCode.PLAN_CHECK not in {t.code for t in got.todos}


def test_generic_mode_lists_no_savings_plan(world):
    _goal_world(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(workspace_table).values(advice_mode="generic"))
    path = _answer(world).goal_path
    assert path is not None and path.state is GoalPathState.DOABLE_RATES
    assert path.return_needed is None and path.today.plans == []


def test_tracking_rows_for_each_stored_month_end_since_the_day_adopted(world):
    """A path adopted on 2 January 2027 (written through the adopt flag with that day, as the
    command would have on that day): one row and one point per closed month-end with a stored
    run, January to March; each month's purchases (the depot's and the broker's), its wants
    (the 300 € at the restaurant), the stored net worth against the path's walk (given here a
    January 100 € above the stored total and a February 100 € below it; March has none)."""
    _goal_world(world)
    for end in (date(2027, 1, 31), date(2027, 2, 28)):
        world.cmd(net_worth.capture_month_end, end)
    lever = _adopt(world).levers.goal_path
    current = world.query(household_scenarios.default_scenario)
    stored = {t.month_end: t.total for t in world.query(goal_tracking.run_totals)}
    jan, feb = date(2027, 1, 31), date(2027, 2, 28)
    walk = [
        GoalPathMonth(month="2027-01", net_worth=stored[jan] + 100),
        GoalPathMonth(month="2027-02", net_worth=stored[feb] - 100),
        *lever.months,
    ]
    early = lever.model_copy(update={"adopted_on": date(2027, 1, 2), "months": walk})
    choice = current.levers.goal_choice.model_copy(
        update={"investment_chosen_on": date(2027, 1, 2)}
    )
    world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            supersedes_id=current.id,
            levers=current.levers.model_copy(update={"goal_path": early, "goal_choice": choice}),
        ),
        adopt=True,
    )
    got = world.query(goal_tracking.goal_tracking, TODAY)
    assert got.shown and got.route is GoalInvestment.PATH
    assert got.rate == lever.rate
    assert [r.month_end for r in got.rows] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
    ]
    assert [p.month_end for p in got.points] == [r.month_end for r in got.rows]
    assert [r.invested for r in got.rows] == [
        Decimal("75.00"),
        Decimal("85.00"),
        Decimal("75.00"),
    ]
    assert all(r.planned == Decimal("531.00") for r in got.rows)
    assert all(r.invested_gap == Decimal("531.00") - r.invested for r in got.rows)
    assert all(r.waiting is None for r in got.rows)  # no split saved
    assert all(r.wants == Decimal("300.00") for r in got.rows)
    assert [r.path_net_worth for r in got.rows] == [stored[jan] + 100, stored[feb] - 100, None]
    assert [r.net_worth_gap for r in got.rows] == [Decimal("100.00"), None, None]
    assert all(r.word is GoalTrackingWord.BEHIND for r in got.rows)
    assert [r.net_worth for r in got.rows] == [stored[r.month_end] for r in got.rows]


def _adopted_in_january(world: World):
    """The path adopted, then moved to 2 January 2027 (as the command would have written it
    that day), so the tracking has a row for each closed month-end January to March."""
    for end in (date(2027, 1, 31), date(2027, 2, 28)):
        world.cmd(net_worth.capture_month_end, end)
    lever = _adopt(world).levers.goal_path
    current = world.query(household_scenarios.default_scenario)
    choice = current.levers.goal_choice.model_copy(
        update={"investment_chosen_on": date(2027, 1, 2)}
    )
    early = lever.model_copy(update={"adopted_on": date(2027, 1, 2)})
    world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            supersedes_id=current.id,
            levers=current.levers.model_copy(update={"goal_path": early, "goal_choice": choice}),
        ),
        adopt=True,
    )
    return early


def test_tracking_counts_the_money_waiting_for_crypto_under_a_saved_split(world):
    """A split saved (§7.11 (i) item 12): the crypto share of the path's 531 € a month not yet
    bought counts as waiting, up to what was saved that month less the loan's repayment, the
    payments into the property and what was invested (Q-O194; this world has neither of the
    first two). No coin is bought in this world, so at 100 % crypto the share (531 €) is above
    the cap (510 € saved less 75, 85 and 75 € invested): the cap binds and the waiting is
    435, 425 and 435 €. At 50 % the share 265.50 € is below it. At 0 % nothing waits: 0.00,
    not None."""
    _goal_world(world)
    _adopted_in_january(world)
    _save(world, investment_split=InvestmentSplit(depot=Decimal("0"), crypto=Decimal("100")))
    rows = world.query(goal_tracking.goal_tracking, TODAY).rows
    assert [r.invested for r in rows] == [Decimal("75.00"), Decimal("85.00"), Decimal("75.00")]
    assert [r.waiting for r in rows] == [Decimal("435.00"), Decimal("425.00"), Decimal("435.00")]
    assert all(r.invested_gap == Decimal("531.00") - r.invested - r.waiting for r in rows)
    _save(world, investment_split=InvestmentSplit(depot=Decimal("50"), crypto=Decimal("50")))
    rows = world.query(goal_tracking.goal_tracking, TODAY).rows
    assert [r.waiting for r in rows] == [Decimal("265.50")] * 3
    _save(world, investment_split=InvestmentSplit(depot=Decimal("100"), crypto=Decimal("0")))
    rows = world.query(goal_tracking.goal_tracking, TODAY).rows
    assert [r.waiting for r in rows] == [Decimal("0.00")] * 3
    assert all(r.invested_gap == Decimal("531.00") - r.invested for r in rows)


# --- Round 2: Luis's answers of 27 Nov 2026 (Q-O192–Q-O195) and the wants net ------------------


def test_the_wants_net_equals_the_budgets_wants_line(world):
    """The ceiling's wants net (rules/goal/goal.py `wants_net`, every want code's mean with a
    refund netted) is the Budget's Wünsche line (services/budget, D273) for the same window:
    a refund of 40 € in February lowers both by the same 13.33 € a month."""
    _, private = _projected(world)
    line = private.line(date(2027, 2, 20), "40.00", name="Trattoria Muster")
    _book(world, line, "40.00", C.HH_REFUND)
    _series(world)
    _follow(world, None, goal=_goal(DOABLE))
    with workspace_transaction(world.engine, world.ws) as conn:
        row = household_scenarios.default_scenario(conn)
        inputs = PlanInputs(conn, TODAY)
        reads = inputs.ended_reads(levers_one_off_codes(row.levers))
        built = goal_service.goal_inputs(conn, TODAY, row, row.levers, reads, inputs)
        view = budget.household_budget(conn, TODAY)
    net = wants_net(built.goal)
    wants = next(li for li in view.lines if li.key is BudgetLineKey.WANT)
    assert built.goal.code_means[C.HH_REFUND] == Decimal("-13.33")
    # equal here; in general within a cent, since each side apportions its means to the cent
    # in its own way (the goal within the regular spending, the Budget within E's mean)
    assert net == wants.monthly, (net, wants.monthly)
    budget_means = {c.category: c.monthly for c in wants.codes}
    assert budget_means[C.HH_REFUND] == Decimal("-13.33")  # the Budget nets it in Wünsche too
    assert net == Decimal("286.67")
    assert _answer(world).goal_path.ceiling.wants_kept <= net


def _no_room(world: World) -> None:
    """A wants target of 100 %: every want is kept, so nothing can be cut (room 0) and the
    path is step 2's, adoptable, with an empty wants budget."""
    current = world.query(household_scenarios.default_scenario)
    targets = current.levers.budget_targets.model_copy(update={"wants_max": Decimal("100.0")})
    _save(world, budget_targets=targets)


def test_an_empty_path_budget_keeps_the_households_spending_plan_on_adopt(world):
    """Q-O193: a path whose budget cuts nothing leaves the household's spending changes (a plan
    saved in „Durchspielen“) and its spending choice exactly as they are."""
    _goal_world(world)
    plan_saved = [
        SpendingChange(code=C.HH_RESTAURANT, monthly=Decimal("280.00"), from_month="2027-05")
    ]
    _save(world, spending_changes=plan_saved)
    _no_room(world)
    before = world.query(household_scenarios.default_scenario)
    path = _answer(world).goal_path
    assert path.room == Decimal("0.00") and path.spending_changes == []
    assert path.state is not GoalPathState.ON_TRACK and path.adoptable, path.state
    row = _adopt(world)
    assert row.levers.goal_path.budget == []
    assert [(c.code, c.monthly, c.from_month) for c in row.levers.spending_changes] == [
        (C.HH_RESTAURANT, Decimal("280.00"), "2027-05")
    ]
    old, new = before.levers.goal_choice, row.levers.goal_choice
    assert (new.spending, new.spending_chosen_on) == (old.spending, old.spending_chosen_on)
    assert new.investment == GoalInvestment.PATH.value


def test_a_changed_split_raises_plan_pruefen_and_the_same_split_does_not(world):
    """Q-O195: the split lever against the path's stored split, by value."""
    _goal_world(world)
    _save(world, investment_split=InvestmentSplit(depot=Decimal("80"), crypto=Decimal("20")))
    _adopt(world)
    same = InvestmentSplit.model_validate({"depot": "80.0", "crypto": "20.0"})
    _save(world, investment_split=same)
    got = _answer(world)
    assert not got.goal_path.adopted.moved
    assert HealthTodoCode.PLAN_CHECK not in {t.code for t in got.todos}
    _save(world, investment_split=InvestmentSplit(depot=Decimal("70"), crypto=Decimal("30")))
    got = _answer(world)
    assert got.goal_path.adopted.moved
    assert HealthTodoCode.PLAN_CHECK in {t.code for t in got.todos}


def test_nothing_waits_when_the_repayment_took_the_crypto_share(world):
    """Q-O194: the cap is saved − the loan's repayment − the payments into the property −
    invested. Each month repays 450 € on the loan from the rental account (saved, so S stays
    510 €): 510 − 450 − 75 (or 85) leaves nothing, so nothing waits at 100 % crypto."""
    _goal_world(world)
    _adopted_in_january(world)
    loan = world.loan()
    for month in (1, 2, 3):
        tx = world.line(date(2027, month, 28), "-450.00")
        _book(world, tx, "-450.00", C.LOAN_PRINCIPAL, loan_id=loan, property_id=world.property_id)
    _save(world, investment_split=InvestmentSplit(depot=Decimal("0"), crypto=Decimal("100")))
    rows = world.query(goal_tracking.goal_tracking, TODAY).rows
    assert [r.invested for r in rows] == [Decimal("75.00"), Decimal("85.00"), Decimal("75.00")]
    assert [r.waiting for r in rows] == [Decimal("0.00")] * 3
    assert all(r.invested_gap == Decimal("531.00") - r.invested for r in rows)
