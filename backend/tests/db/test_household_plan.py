"""Phase 9 step 2, the service side (docs/phases/phase-9.md §6, §7, §13; D244–D248):
GET /household-plan assembled on read, over invented data (owners A and B, a flat with its
rental account, a private current account and a depot). The arithmetic is held by the rule
fixtures (tests/rules/test_phase9_plan.py); these tests hold the reads, the to-dos and the
two done checks of the build order (§16, step 2): the projection's bank row equals the as_is
path, and a projected year's flat cash equals phase 4's for the same scenario."""

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

import pytest
from db.matching_support import World
from db.test_services_health import _book
from db.test_services_health import _scenario as _flat_scenario
from db.test_services_health_snapshots import NOW, TODAY, _closed_history, _coin_held_since_july
from db.test_services_net_worth import _price
from db.test_services_private_matching import Private
from db.test_services_transfers import SAVINGS_IBAN, _savings
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.health import BandLimits, HealthTodoCode, MarkedFigure
from hausbuch.schemas.household import TransferLinkCreate, TransferLinkKind
from hausbuch.schemas.performance import YearKind
from hausbuch.schemas.plan import (
    HouseholdLevers,
    HouseholdScenarioCreate,
    IncomeChange,
    OneOff,
    PlanClass,
    PlanOverride,
    ReserveAccounts,
    SurplusKey,
)
from hausbuch.services import household_scenarios, net_worth, plan, transfers
from hausbuch.services.errors import NotFoundError
from hausbuch.services.performance import performance_projection

C = AllocationCategory
CENT = Decimal("0.01")
BROKER = "Beispiel ETF Broker"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _bought_in_2026(world: World) -> None:
    """World's flat was bought in 2024, before the first rule set, so phase 4 refuses to
    project it (RuleError projection.no_rule_set); moved to 1 Jan 2026 here."""
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(property_table)
            .where(property_table.c.id == world.property_id)
            .values(notarized_on=date(2025, 11, 20), purchase_date=date(2026, 1, 1))
        )


def _household(world: World, **levers):
    return world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(name="Basis", is_default=True, levers=HouseholdLevers(**levers)),
    )


def _plan(world: World, scenario=None):
    return world.query(plan.household_plan, TODAY, scenario)


def _covered(world: World, import_id) -> None:
    """An import that covers 5 Jan 2026 to 31 Mar 2027, as the rental account's does."""
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == import_id)
            .values(first_booking_on=date(2026, 1, 5), last_booking_on=date(2027, 3, 31))
        )


def _depot(world: World) -> Private:
    """A depot of the household with an IBAN (phase 6 D156 amended) and its own import."""
    depot = Private.__new__(Private)
    depot.world = world
    with workspace_transaction(world.engine, world.ws) as conn:
        depot.account_id = world._account(conn, SAVINGS_IBAN, owner=True)
        conn.execute(
            update(account_table)
            .where(account_table.c.id == depot.account_id)
            .values(kind="depot", label="Depot")
        )
    depot.import_id = depot.add_import(world.now)
    _covered(world, depot.import_id)
    return depot


def _private_and_depot(world: World) -> tuple[Private, Private]:
    """Twelve closed months (the rental account's), a private current account holding
    12,000 € and a depot, both covering the months."""
    _closed_history(world)
    private = Private(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(  # a reserve to draw on: 12,000 € on the current account
            update(account_table)
            .where(account_table.c.id == private.account_id)
            .values(opening_balance=Decimal("12000.00"))
        )
    _covered(world, private.import_id)
    return private, _depot(world)


def _savings_history(world: World) -> tuple[Private, Private]:
    """Twelve closed months (the rental account's) plus, in January to March 2027, a monthly
    transfer from the private account into the depot (50, 60, 50 €; linked) and a monthly
    purchase at a broker booked hh_investment_securities (25 € each)."""
    private, depot = _private_and_depot(world)
    for day, amount in ((date(2027, 1, 15), "50.00"), (date(2027, 2, 15), "60.00")):
        _transfer(world, private, depot, day, amount)
    _transfer(world, private, depot, date(2027, 3, 15), "50.00")
    for month in (1, 2, 3):
        line = private.line(date(2027, month, 20), "-25.00", name=BROKER)
        _book(world, line, "-25.00", C.HH_INVESTMENT_SECURITIES)
    return private, depot


def _transfer(world: World, private: Private, depot: Private, day: date, amount: str) -> None:
    out = private.line(day, f"-{amount}", iban=SAVINGS_IBAN, purpose="Sparplan")
    into = depot.line(day, amount, purpose="Einzahlung")
    _book(world, out, f"-{amount}", C.INTERNAL_TRANSFER_OUT)
    _book(world, into, amount, C.INTERNAL_TRANSFER_IN)
    body = TransferLinkCreate(
        kind=TransferLinkKind.DEPOT_CASH, out_transaction_id=out, in_transaction_id=into
    )
    world.cmd(transfers.link_by_hand, body)


def test_too_few_closed_months_give_the_to_do_and_no_figure(world):
    """D231: without three closed months the plan holds its levers and the to-do, and the
    projection's card the same to-do; no mean, no plan, no path."""
    got = _plan(world)
    assert got.as_of == TODAY and got.scenario_id is None
    assert got.levers == HouseholdLevers()
    assert got.surplus is None and got.reserve is None
    assert (got.plans, got.paths, got.months) == ([], [], [])
    assert [t.code for t in got.todos] == [HealthTodoCode.TOO_FEW_MONTHS]
    assert got.projection is not None and got.projection.points == []
    assert [t.code for t in got.projection.todos] == [HealthTodoCode.TOO_FEW_MONTHS]


def test_the_plans_are_read_from_the_private_transfers_and_the_payees(world):
    """D245: the depot fed in each of the last three closed months by a linked transfer, and
    the broker paid in each of them, are plans with the median of their three sums; the
    scenario's override sets the broker's amount, and an override of a plan Hausbuch no longer
    finds is „Szenario prüfen“."""
    _, depot = _savings_history(world)
    broker = "beispiel-etf-broker"
    _household(
        world,
        plan_overrides=[
            PlanOverride(plan_key=broker, monthly=Decimal("30.00")),
            PlanOverride(plan_key="gibt-es-nicht", dropped=True),
        ],
    )
    got = _plan(world)
    assert got.newest_closed == "2027-03"
    assert [(p.key, p.asset_class, p.monthly, p.months, p.overridden) for p in got.plans] == [
        (
            str(depot.account_id),
            PlanClass.DEPOT,
            Decimal("50.00"),
            [Decimal("50.00"), Decimal("60.00"), Decimal("50.00")],
            False,
        ),
        (broker, PlanClass.DEPOT, Decimal("30.00"), [Decimal("25.00")] * 3, True),
    ]
    assert got.plans[1].destination_payee == BROKER
    assert got.already_invested == Decimal("80.00")
    investable = got.surplus.amount(SurplusKey.INVESTABLE)
    assert got.investable_left == investable - Decimal("80.00")
    assert [t.code for t in got.todos] == [
        HealthTodoCode.SCENARIO_CHECK,
        HealthTodoCode.NO_DEFAULT_SCENARIO,
    ]
    # The paths: every plan paused, each paused alone, all continued (D246).
    assert {p.key for p in got.paths} == {
        "reserve_first",
        f"pause:{depot.account_id}",
        f"pause:{broker}",
        "as_is",
    }


def test_a_projected_years_flat_cash_is_phase_4s(world):
    """Done check of step 2: a month of a year phase 4 projects carries the default
    scenario's (net_cash + tax_effect.effect) ÷ 12 of that year, read from
    services.performance as it is; the newest closed month's own year keeps the measured
    mean."""
    _closed_history(world)
    _bought_in_2026(world)
    flat = _flat_scenario(world, None, NOW)
    _household(world, horizon_months=24)
    got = _plan(world)
    phase4 = world.query(
        performance_projection, world.property_id, today=TODAY, scenario_id=flat.id
    )
    projected = {y.year: y for y in phase4.years if y.kind is YearKind.PROJECTED}
    assert 2028 in projected and 2027 not in projected
    year = projected[2028]
    effect = Decimal("0") if year.tax_effect is None else year.tax_effect.effect
    expected = ((year.net_cash + effect) / 12).quantize(CENT, rounding=ROUND_HALF_UP)
    mean = got.surplus.amount(SurplusKey.MEAN_RENTAL_CASH)
    assert mean == Decimal("310.00")
    by_month = {m.month: m.flat for m in got.months}
    assert by_month["2027-04"] == by_month["2027-12"] == mean  # today's year: phase 4's actuals
    assert {by_month[f"2028-{n:02d}"] for n in range(1, 13)} == {expected}
    assert expected != mean  # the check holds something
    assert [t.code for t in got.todos] == []


def test_the_projections_bank_row_is_the_as_is_path(world):
    """Done check of step 2 (§7.5 (4)): from the stored run at the newest closed month-end,
    bank(k) − bank(0) equals the as_is path's reserve(k) − reserve(0); with a horizon ending on
    December 2028 the first point's bank row and the path's reserve at the horizon agree."""
    _savings_history(world)
    _bought_in_2026(world)
    _flat_scenario(world, None, NOW)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    _household(
        world,
        horizon_months=21,
        net_worth_years=1,
        value_change={"depot": Decimal("0.030")},
    )
    got = _plan(world)
    assert got.months[-1].month == "2028-12"
    projection = got.projection
    assert projection is not None and projection.todos == [], projection.todos
    assert projection.start.on == date(2027, 3, 31)
    assert projection.points
    [point] = projection.points
    assert point.on == date(2028, 12, 31)
    as_is = next(p for p in got.paths if p.key == "as_is")
    assert point.bank - projection.start.bank == as_is.reserve_at_horizon - got.reserve.amount
    assert point.bank != projection.start.bank
    kinds = [(a.kind.value, a.value) for a in projection.assumptions]
    assert kinds == [("depot_rate", Decimal("0.030")), ("flat_growth", Decimal("0.02"))]


def test_without_a_run_or_a_scenario_the_projection_is_a_to_do(world):
    """§7.5 (1), D248: no saved household scenario shows no projection; with one, a missing
    run at the newest closed month-end is „Monatsabschluss festhalten“, a property without a
    default scenario „Szenario für die Wohnung anlegen“; the paths are still computed, from
    today's balances when no run holds the month-end."""
    _closed_history(world)
    got = _plan(world)
    assert [t.code for t in got.projection.todos] == [HealthTodoCode.RATE_MISSING]
    assert got.paths and got.reserve is not None
    # D247: a property without a default scenario is a to-do of the plan too, not only of the
    # projection card.
    assert HealthTodoCode.NO_DEFAULT_SCENARIO in [t.code for t in got.todos]
    _household(world)
    got = _plan(world)
    assert [t.code for t in got.projection.todos] == [
        HealthTodoCode.MONTH_END_MISSING,
        HealthTodoCode.NO_DEFAULT_SCENARIO,
    ]
    assert [t.code for t in got.todos].count(HealthTodoCode.NO_DEFAULT_SCENARIO) == 1
    # Without a default scenario the flat keeps its measured mean in every month.
    assert {m.flat for m in got.months} == {Decimal("310.00")}
    # A default scenario phase 4 refuses to project (World's flat was bought in 2024, before
    # the first rule set): „Szenario prüfen“ on the plan and in the card's place, the mean kept.
    _flat_scenario(world, None, NOW)
    got = _plan(world)
    assert [t.code for t in got.todos] == [HealthTodoCode.SCENARIO_CHECK]
    assert [t.code for t in got.projection.todos] == [HealthTodoCode.SCENARIO_CHECK]
    assert {m.flat for m in got.months} == {Decimal("310.00")}


def test_the_generic_mode_keeps_the_first_and_last_path(world):
    """D251: under advice_mode generic only „Reserve zuerst“ and „Alles weiter wie bisher“."""
    _savings_history(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(workspace_table).values(advice_mode="generic"))
    got = _plan(world)
    assert len(got.plans) == 2
    assert [p.key for p in got.paths if p.key.startswith("pause:")] == []
    assert {p.key for p in got.paths} == {"reserve_first", "as_is"}


def test_a_named_scenario_is_used_and_an_unknown_one_refused(world):
    """?scenario= reads that row, a withdrawn one too; an unknown id is a 404."""
    _closed_history(world)
    row = _household(world, buffer_percent=Decimal("20"))
    gone = world.cmd(household_scenarios.withdraw_scenario, row.id)
    got = _plan(world, row.id)
    assert got.scenario_id == row.id and got.levers.buffer_percent == Decimal("20")
    assert _plan(world).scenario_id is None  # no default in force after the withdrawal
    assert _plan(world, gone.id).scenario_id == gone.id
    with pytest.raises(NotFoundError) as unknown:
        _plan(world, world.property_id)
    assert unknown.value.entity_type is EntityType.HOUSEHOLD_SCENARIO


def test_a_reserve_account_that_no_longer_fits_and_an_income_change_are_named(world):
    """§7.3: a scenario counting an account to the reserve that is no reserve account any
    more (an exchange; a depot may be added since phase 10 D304) is „Szenario prüfen“; an
    income change typed in the scenario points to the owners' expected pay (D248) and
    changes the free cash from its month on."""
    _closed_history(world)
    savings = _savings(world)
    _covered(world, savings.import_id)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(  # a positive reserve, so the paths start above 0
            update(account_table)
            .where(account_table.c.id == savings.account_id)
            .values(opening_balance=Decimal("5000.00"))
        )
    for month in (1, 2, 3):  # a fee each month, so the account covers them
        fee = savings.line(date(2027, month, 28), "-1.00", purpose="Gebühr")
        _book(world, fee, "-1.00", C.HH_INTEREST_FEES)
    _household(
        world,
        reserve_accounts=ReserveAccounts(added=[savings.account_id]),
        income_changes=[IncomeChange(monthly_net=Decimal("-600.00"), from_month="2027-06")],
    )
    got = _plan(world)
    assert [t.code for t in got.todos] == [
        HealthTodoCode.INCOME_CHANGE_LINK,
        HealthTodoCode.NO_DEFAULT_SCENARIO,
    ]
    by_month = {m.month: m for m in got.months}
    assert by_month["2027-06"].free == by_month["2027-05"].free - Decimal("600.00")
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(account_table)
            .where(account_table.c.id == savings.account_id)
            .values(kind="exchange")
        )
    got = _plan(world)
    assert [t.code for t in got.todos] == [
        HealthTodoCode.SCENARIO_CHECK,
        HealthTodoCode.INCOME_CHANGE_LINK,
        HealthTodoCode.NO_DEFAULT_SCENARIO,
    ]


def test_the_rental_line_is_said_only_while_the_rental_account_is_no_reserve(world):
    """„davon … auf dem Mietkonto, das nicht zur Reserve zählt“: the flat's positive mean, but
    not once the lever counts the rental account to the reserve (PR #434 review E)."""
    _closed_history(world)
    row = _household(world)
    got = _plan(world)
    assert got.paths_on_rental_account == Decimal("310.00")
    counted = HouseholdLevers(reserve_accounts=ReserveAccounts(added=[world.account_id]))
    world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis", is_default=True, levers=counted, supersedes_id=row.id
        ),
    )
    got = _plan(world)
    assert any(a.account_id == world.account_id and a.counted for a in got.reserve.accounts)
    assert got.paths_on_rental_account is None


def test_a_payment_linked_by_hand_and_booked_to_a_payee_is_one_plan(world):
    """D245: a private payment to a broker booked hh_investment_securities and linked by hand
    to the depot's credit (a hand link stays current whatever its legs are booked as) is the
    out-leg of that link; it is not read again as a payment to the broker, so one plan."""
    private, depot = _private_and_depot(world)
    for month in (1, 2, 3):
        day = date(2027, month, 15)
        out = private.line(day, "-50.00", name=BROKER, purpose="Sparplan")
        into = depot.line(day, "50.00", purpose="Einzahlung")
        _book(world, out, "-50.00", C.HH_INVESTMENT_SECURITIES)
        _book(world, into, "50.00", C.INTERNAL_TRANSFER_IN)
        body = TransferLinkCreate(
            kind=TransferLinkKind.DEPOT_CASH, out_transaction_id=out, in_transaction_id=into
        )
        world.cmd(transfers.link_by_hand, body)
    got = _plan(world)
    assert [(p.key, p.monthly) for p in got.plans] == [(str(depot.account_id), Decimal("50.00"))]
    assert got.already_invested == Decimal("50.00")


def test_a_past_one_off_is_ignored_and_the_row_can_still_be_superseded(world):
    """§7.3 amended (PR #434 review D): a one-off saved on 10 Feb 2027 for February is in a
    closed month when read on 10 Apr 2027 (newest closed month March). The plan ignores it
    (its month is measured), raises no to-do, and the row is superseded both by a plain save
    and by „Übernehmen“ of the marks' limits, which saves the same levers with `bands`
    (until then every later save was refused with household_scenario.one_off_outside)."""
    _closed_history(world)
    past = OneOff(month="2027-02", amount=Decimal("850.00"), source="return_estimate:2026")
    row = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis", is_default=True, levers=HouseholdLevers(one_offs=[past])
        ),
        now=datetime(2027, 2, 10, 9, 0, tzinfo=UTC),
    )
    got = _plan(world)
    assert [t for t in got.todos if t.code is HealthTodoCode.SCENARIO_CHECK] == []
    assert {m.free for m in got.months} == {Decimal("310.00")}  # the amount is left out
    plain = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(one_offs=[past], buffer_percent=Decimal("20")),
            supersedes_id=row.id,
        ),
    )
    bands = [BandLimits(figure=MarkedFigure.RESERVE, red=Decimal("0.5"))]
    taken = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=plain.levers.model_copy(update={"bands": bands}),
            supersedes_id=plain.id,
        ),
    )
    assert taken.levers.one_offs == [past]  # kept, so its tax year is not proposed again


def test_an_incomplete_stored_month_end_is_no_start(world):
    """§7.5 (1), (2): phase 6 stores a month-end run that leaves a coin without a price on
    the day out; the projection does not start from it (the coin would be 0 €) but shows the
    gap's to-do, the guard step 1's daily step uses. Once the coin has a price on the day and
    the month-end is captured again, the projection starts with its value."""
    _closed_history(world)
    _bought_in_2026(world)
    _flat_scenario(world, None, NOW)
    _, coin = _coin_held_since_july(world, date(2027, 4, 5))
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    _household(world, value_change={"crypto": Decimal("0.000")})
    got = _plan(world)
    [todo] = got.projection.todos
    assert got.projection.points == []
    assert todo.code is HealthTodoCode.MONTH_END_MISSING
    assert "31.03.2027 ist noch unvollständig" in todo.text and "Bitcoin" in todo.text
    assert (todo.action, todo.href) == ("Kursquelle wählen", "/assets")

    world.cmd(lambda ctx: _price(ctx, coin, date(2027, 3, 31), "40000.00", "user"))
    later = datetime(2027, 4, 10, 9, 0, tzinfo=UTC)  # a newer run than the first capture's
    captured = world.cmd(net_worth.capture_month_end, date(2027, 3, 31), now=later)
    assert captured.unvalued_positions == 0
    got = _plan(world)
    assert got.projection.todos == [] and got.projection.points
    assert got.projection.start.crypto == Decimal("80000.00")
