"""Phase 10 §7.11 (a) 2, (a) 6, (f) 1 on the database: GET /household-plan's effective split,
the form's prefill from the window's purchases and „noch nicht angelegt“, read through
services.plan. The arithmetic is tests/fixtures/phase10/investment_split.yaml
(tests/rules/test_phase10_investment_split.py)."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_household_plan import BROKER, _savings_history
from db.test_services_health import _book
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.plan.savings_plans import payee_key
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.health import HealthTodoCode
from hausbuch.schemas.plan import (
    HouseholdLevers,
    InvestmentSplit,
    PlanClass,
    PlanOverride,
    ReserveAccounts,
    SplitSource,
    ValueChange,
)
from hausbuch.services import plan, plan_inputs

pytestmark = pytest.mark.db

D = Decimal
RATES = ValueChange(depot=D("0.050"), crypto=D("0.080"))


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    plan_inputs.clear()
    yield World(app_engine, fresh_workspaces[0], now=NOW)
    plan_inputs.clear()


def _parts(world: World, levers: HouseholdLevers):
    """plan_parts with levers built here, past the save's checks (a depot in the reserve's
    `added` is refused on save until D304's change lands)."""
    return world.query(lambda conn, today: plan.plan_parts(conn, today, None, levers)[0], TODAY)


def test_the_plans_give_the_mix_and_the_purchases_the_prefill(world):
    """Closed months January to March 2027; the depot plan (50 €) and the broker (25 €) are both
    funds, so the mix is all funds. Without a typed rate nothing moves and the to-do names
    the funds; with one the mix is 100/0 from the plans. The prefill is every purchase of the
    plan's own closed window: 50 + 60 + 50 into the depot and 3 × 25 to the broker, 235 €,
    all funds."""
    _savings_history(world)
    got = _parts(world, HouseholdLevers())
    assert got.newest_closed == "2027-03"
    split = got.investment_split
    assert split is not None
    assert (split.source, split.shares, split.left_out) == (
        SplitSource.SAVINGS_PLANS,
        None,
        [PlanClass.DEPOT],
    )
    (todo,) = split.todos
    assert (todo.code, todo.action) == (HealthTodoCode.RATE_MISSING, "Rendite eintragen")
    assert todo not in got.todos  # the projection's own to-do names the same gap

    typed = _parts(world, HouseholdLevers(value_change=RATES)).investment_split
    assert typed.source is SplitSource.SAVINGS_PLANS
    assert typed.shares == InvestmentSplit(depot=D("100.0"), crypto=D("0.0"))
    assert typed.todos == []

    prefill = got.investment_split_prefill
    assert prefill is not None
    # the private account's first line is in January 2027, so its window starts there
    assert (prefill.from_month, prefill.to_month) == ("2027-01", "2027-03")
    assert prefill.bought == {PlanClass.DEPOT: D("235.00"), PlanClass.CRYPTO: D("0.00")}
    assert prefill.split == InvestmentSplit(depot=D("100.0"), crypto=D("0.0"))
    assert got.not_invested is None  # no split saved


def test_a_saved_split_wins_and_shows_what_is_not_yet_invested(world):
    """Saved 60/40: crypto's 40 % of the 235 € invested is 94.00, nothing bought in it, so
    94.00 „noch nicht angelegt“ at an actual share of 0.0; the funds are above theirs. In
    generic mode the amount only, without a kind (D251)."""
    _savings_history(world)
    saved = InvestmentSplit(depot=D("60.0"), crypto=D("40.0"))
    levers = HouseholdLevers(value_change=RATES, investment_split=saved)
    got = _parts(world, levers)
    assert got.investment_split.source is SplitSource.SAVED
    assert got.investment_split.shares == saved
    gap = got.not_invested
    assert gap is not None
    assert (gap.invested, gap.amount) == (D("235.00"), D("94.00"))
    assert [(x.asset_class, x.target_percent, x.actual_percent, x.amount) for x in gap.lines] == [
        (PlanClass.CRYPTO, D("40.0"), D("0.0"), D("94.00"))
    ]
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(workspace_table).values(advice_mode="generic"))
    plan_inputs.clear()
    generic = _parts(world, levers).not_invested
    assert generic is not None
    assert (generic.amount, generic.lines) == (D("94.00"), [])


def test_a_depot_counted_in_the_reserve_is_left_out(world):
    """D304: a depot in the reserve's set is no purchase and no plan of the mix; the broker's
    3 × 25 € remain."""
    _, depot = _savings_history(world)
    levers = HouseholdLevers(
        value_change=RATES, reserve_accounts=ReserveAccounts(added=[depot.account_id])
    )
    got = _parts(world, levers)
    assert got.investment_split_prefill.bought == {
        PlanClass.DEPOT: D("75.00"),
        PlanClass.CRYPTO: D("0.00"),
    }
    assert got.investment_split.source is SplitSource.SAVINGS_PLANS  # the broker's plan


def test_without_plans_the_holdings_decide(world):
    """Both plans dropped: the depot's cash (160 € transferred in) is today's only holding, so
    the mix comes from the holdings, all funds."""
    _, depot = _savings_history(world)
    dropped = [
        PlanOverride(plan_key=str(depot.account_id), dropped=True),
        PlanOverride(plan_key=payee_key(BROKER), dropped=True),
    ]
    got = _parts(world, HouseholdLevers(value_change=RATES, plan_overrides=dropped))
    assert got.plans == []
    assert got.investment_split.source is SplitSource.HOLDINGS
    assert got.investment_split.shares == InvestmentSplit(depot=D("100.0"), crypto=D("0.0"))


def test_the_window_reaches_back_twelve_closed_months_not_three(world):
    """The prefill and „noch nicht angelegt“ read the whole window (the closed months, at
    most twelve), not only the last three the savings plans are detected from. With the
    private account's first line in February 2026, February 2026 to March 2027 are closed and
    the window is April 2026 to March 2027: a crypto purchase in October 2026 (100 €) and a
    broker purchase in November 2026 (40 €) count, the broker's February 2026 purchase (999 €,
    before the window) does not. Funds 235 + 40 = 275, crypto 100, 375 invested; saved 50/50:
    crypto's 187.50 less its 100 leaves 87.50."""
    private, depot = _savings_history(world)
    for day, amount, name, code in (
        (date(2026, 2, 20), "-999.00", BROKER, C.HH_INVESTMENT_SECURITIES),
        (date(2026, 10, 20), "-100.00", "Beispiel Krypto Börse", C.HH_INVESTMENT_CRYPTO),
        (date(2026, 11, 20), "-40.00", BROKER, C.HH_INVESTMENT_SECURITIES),
    ):
        _book(world, private.line(day, amount, name=name), amount, code)
    saved = InvestmentSplit(depot=D("50.0"), crypto=D("50.0"))
    got = _parts(world, HouseholdLevers(value_change=RATES, investment_split=saved))
    prefill = got.investment_split_prefill
    assert (prefill.from_month, prefill.to_month) == ("2026-04", "2027-03")
    assert prefill.bought == {PlanClass.DEPOT: D("275.00"), PlanClass.CRYPTO: D("100.00")}
    assert prefill.split == InvestmentSplit(depot=D("73.3"), crypto=D("26.7"))
    gap = got.not_invested
    assert (gap.from_month, gap.invested, gap.amount) == ("2026-04", D("375.00"), D("87.50"))
    assert [(x.asset_class, x.actual_percent, x.amount) for x in gap.lines] == [
        (PlanClass.CRYPTO, D("26.7"), D("87.50"))
    ]
    # the savings plans still come from the last three closed months only
    assert {p.key for p in got.plans} == {str(depot.account_id), payee_key(BROKER)}
