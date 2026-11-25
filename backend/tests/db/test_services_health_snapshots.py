"""Phase 9 step 1, the service side (docs/phases/phase-9.md §5.5, §13, D242, D259 amended):
GET /health, the months on read, the stored snapshot of a closed month, the daily task's step
and `hausbuch health-check`, over invented data (owners A and B, a flat and its rental
account). The arithmetic is held by the rule fixtures; these tests hold the reads and writes."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_health import _account, _book, _statement
from db.test_services_net_worth import _asset, _held, _price
from sqlalchemy import func, insert, select, update

from hausbuch import cli
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import health_snapshot as health_snapshot_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.health_daily import run_health_daily
from hausbuch.rules.errors import (
    HEALTH_MONTH_END_INCOMPLETE,
    HEALTH_MONTH_END_MISSING,
    HEALTH_MONTH_NOT_CLOSED,
    HEALTH_RANGE_INVALID,
)
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import (
    ClosedReasonCode,
    HealthSnapshotOutcome,
    HealthTodoCode,
    ReserveNeedBasis,
)
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import HouseholdLevers, HouseholdScenarioCreate
from hausbuch.services import allocations, health, household_scenarios, net_worth
from hausbuch.services.errors import RuleViolationError

C = AllocationCategory
NOW = datetime(2027, 4, 10, 8, 0, tzinfo=UTC)
TODAY = date(2027, 4, 10)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _booked(world: World, booked: date, amount: str, category: C, *, confirm: bool = True):
    tx = world.line(booked, amount)
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[
                AllocationDraft(
                    amount=Decimal(amount), category=category, property_id=world.property_id
                )
            ],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=confirm,
        auto=False,
    )
    return tx


def _closed_history(world: World) -> None:
    """The rental account covered from 5 Jan 2026 to 31 Mar 2027 (its one import, made on 10
    Apr 2027), the rent of each month booked and confirmed, no line waiting: every month from
    January 2026 to March 2027 is closed."""
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == world.import_id)
            .values(first_booking_on=date(2026, 1, 5), last_booking_on=date(2027, 3, 31))
        )
    for year, month in [(2026, m) for m in range(1, 13)] + [(2027, m) for m in range(1, 4)]:
        _booked(world, date(year, month, 5), "950.00", C.RENT_COLD)
        _booked(world, date(year, month, 20), "-640.00", C.LOAN_INTEREST)


def _snapshots(world: World) -> int:
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(select(func.count()).select_from(health_snapshot_table)).scalar_one()


def _events(world: World) -> int:
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(func.count())
            .select_from(event_table)
            .where(event_table.c.type == "health.snapshot_stored")
        ).scalar_one()


def test_health_reads_twelve_closed_months_and_marks_nothing_unconfirmed(world):
    """GET /health: the window is the newest twelve closed months; the flow figures are
    computed over it; no limit is confirmed, so no colour, the proposals and the to-do."""
    _closed_history(world)
    got = world.query(health.health, date(2027, 4, 10))
    assert got.window.months[0] == "2026-04" and got.window.months[-1] == "2027-03"
    assert got.newest.month == "2027-03"
    assert got.savings_rate.income == Decimal("950.00") * 12 - Decimal("640.00") * 12
    assert got.debt is not None and got.reserve is not None
    assert got.free_cash == Decimal("310.00")
    assert all(light.colour is None for light in got.marks.values())
    assert got.proposals
    assert [t.code for t in got.todos] == [HealthTodoCode.BANDS_UNCONFIRMED]


def test_too_few_closed_months_show_no_flow_figure(world):
    """D231: a waiting line in January 2027 leaves February and March as the window: no
    savings rate, reserve, debt or free cash, and the to-do for too few months (no month
    after the window keeps it open: the window reaches March)."""
    _closed_history(world)
    world.line(date(2027, 1, 12), "-30.00")  # waiting in Zu prüfen
    got = world.query(health.health, date(2027, 4, 10))
    assert got.window.months == ["2027-02", "2027-03"]
    assert (got.savings_rate, got.reserve, got.debt, got.free_cash) == (None, None, None, None)
    assert HealthTodoCode.TOO_FEW_MONTHS in [t.code for t in got.todos]


def test_a_closed_month_is_stored_once_beside_its_run(world):
    """D242: „Kennzahlen festhalten“ stores the month as of that month, beside the newest run
    of its month-end, with its event; pressing it again stores nothing new."""
    _closed_history(world)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    first = world.cmd(health.store_snapshot, "2027-03")
    assert first.outcome is HealthSnapshotOutcome.STORED
    figures = first.snapshot.figures
    assert figures.month.month == "2027-03" and figures.month.sources == {}
    assert figures.window.months[-1] == "2027-03" and len(figures.window.months) == 12
    assert first.snapshot.rule_version == health.rule_version()
    assert len(first.snapshot.rule_version) == 64
    again = world.cmd(health.store_snapshot, "2027-03")
    assert again.outcome is HealthSnapshotOutcome.UNCHANGED
    assert again.snapshot.id == first.snapshot.id
    assert (_snapshots(world), _events(world)) == (1, 1)


def test_an_unclosed_month_is_refused_and_a_month_without_a_run_captures_it(world):
    """POST /health/snapshots: a month still running, one before January 2026 and one with a
    waiting line are refused, nothing written. A closed month whose month-end has no run gets
    its run captured first and the snapshot beside it (review of PR #426: the button no
    longer asks for „Monatsabschluss festhalten“ first); it does not wait for the month
    before it, which the daily task does."""
    _closed_history(world)
    world.line(date(2027, 2, 12), "-30.00")
    world.cmd(net_worth.capture_month_end, date(2027, 2, 28))
    for month in ("2027-04", "2025-12", "2027-02"):
        with pytest.raises(RuleViolationError) as refused:
            world.cmd(health.store_snapshot, month)
        assert list(refused.value.codes) == [HEALTH_MONTH_NOT_CLOSED]
    assert _snapshots(world) == 0
    assert world.query(net_worth.latest_run, date(2027, 1, 31)) is None
    january = world.cmd(health.store_snapshot, "2027-01")
    run = world.query(net_worth.latest_run, date(2027, 1, 31))
    assert january.outcome is HealthSnapshotOutcome.STORED
    assert run is not None and january.snapshot.net_worth_run_id == run.net_worth_run_id
    march = world.cmd(health.store_snapshot, "2027-03")  # February, before it, is open
    assert march.outcome is HealthSnapshotOutcome.STORED
    assert _snapshots(world) == 2


def test_the_daily_step_stores_each_missing_month_once(world):
    """§11.5 with the review of PR #426: every ended, closed month with a complete month-end
    and no snapshot gets one, oldest first, back to January 2026; a month-end without a run
    is captured first, one whose run equals the day read again keeps that run (no second
    run); a second step stores nothing."""
    _closed_history(world)
    january = world.cmd(net_worth.capture_month_end, date(2027, 1, 31)).run
    stored = world.cmd(health.store_missing_snapshots)
    months = [r.snapshot.figures.month.month for r in stored]
    assert months == health.months_between("2026-01", "2027-03")
    by_month = {r.snapshot.figures.month.month: r.snapshot for r in stored}
    assert by_month["2027-01"].net_worth_run_id == january.net_worth_run_id
    runs = world.query(net_worth.list_runs)
    assert len(runs) == 15 and len({r.as_of for r in runs}) == 15
    for month, snapshot in by_month.items():
        run = world.query(net_worth.latest_run, health.month_bounds(month)[1])
        assert snapshot.net_worth_run_id == run.net_worth_run_id
    assert world.cmd(health.store_missing_snapshots) == []
    assert (_snapshots(world), _events(world)) == (15, 15)


def test_the_daily_task_stores_as_the_system_and_is_idempotent(world):
    """§11.5: health_daily runs the step for each workspace under the system actor; an empty
    workspace stores nothing and fails nothing; the second run stores nothing."""
    _closed_history(world)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    deps = JobDeps(
        engine=world.engine,
        settings=None,
        blob_store=None,
        model_client=None,
        clock=lambda: datetime(2027, 4, 11, 8, 30, tzinfo=UTC),
    )
    first = run_health_daily(deps, workspace_ids=[world.ws])
    assert (first.stored, first.failed_workspaces) == (15, 0)  # every closed month since Jan 2026
    again = run_health_daily(deps, workspace_ids=[world.ws])
    assert (again.stored, again.failed_workspaces) == (0, 0)
    with workspace_transaction(world.engine, world.ws) as conn:
        actors = conn.execute(
            select(event_table.c.actor_type, event_table.c.actor_id).where(
                event_table.c.type == "health.snapshot_stored"
            )
        ).all()
    assert {(a.actor_type, a.actor_id) for a in actors} == {("system", "health_daily")}
    assert len(actors) == 15


def test_a_month_changed_since_its_snapshot_shows_both(world):
    """GET /health/months: the stored month beside the month on read once a line booked
    later changes it; equal months carry no stored copy."""
    _closed_history(world)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    world.cmd(health.store_snapshot, "2027-03")
    views = world.query(health.health_months, "2027-02", "2027-03", date(2027, 4, 10))
    assert [v.month for v in views] == ["2027-02", "2027-03"]
    assert views[1].snapshot_id is not None and views[1].stored is None
    # February is closed but its month-end has no run: the to-do that lets it be stored.
    assert views[0].todo.code is HealthTodoCode.MONTH_END_MISSING
    assert views[0].todo.href == "/assets?month_end=2027-02-28" and views[1].todo is None
    _booked(world, date(2027, 3, 25), "-100.00", C.INSURANCE)
    views = world.query(health.health_months, "2027-03", "2027-03", date(2027, 4, 10))
    assert views[0].stored is not None
    assert views[0].stored.spending != views[0].household.spending or (
        views[0].stored.rental_result != views[0].household.rental_result
    )
    for first, last in [("2027-03", "2027-02"), ("2025-01", "2027-03")]:
        with pytest.raises(RuleViolationError) as refused:
            world.query(health.health_months, first, last, date(2027, 4, 10))
        assert list(refused.value.codes) == [HEALTH_RANGE_INVALID]


def test_a_waiting_depot_line_keeps_its_month_open(world):
    """Review finding 4: a depot's cash lines count in the household month, so a depot line
    waiting in Zu prüfen keeps its month open like a bank line; the depot's statements are
    not a coverage check (coverage stays on bank accounts and cards)."""
    _closed_history(world)
    assert world.query(health.read_months, ["2027-03"], TODAY)["2027-03"].closed.closed
    depot = _account(world, "Depot", AccountKind.DEPOT)
    fee, waiting = _statement(
        world,
        depot,
        date(2027, 3, 15),
        date(2027, 3, 20),
        datetime(2027, 4, 9, tzinfo=UTC),
        lines=[date(2027, 3, 15), date(2027, 3, 20)],
    )
    _book(world, fee, "-10.00", C.DEPOT_FEE)
    assert depot not in {a.account_id for a in world.query(health.coverage_accounts)}
    read = world.query(health.read_months, ["2027-03"], TODAY)["2027-03"]
    assert read.household.spending == Decimal("10.00")  # the depot's fee is in the month
    assert not read.closed.closed
    assert [(r.code, r.count) for r in read.closed.reasons] == [(ClosedReasonCode.LINES_WAITING, 1)]
    with pytest.raises(RuleViolationError) as refused:
        world.cmd(health.store_snapshot, "2027-03")
    assert list(refused.value.codes) == [HEALTH_MONTH_NOT_CLOSED]
    _book(world, waiting, "-10.00", C.DEPOT_FEE)
    assert world.query(health.read_months, ["2027-03"], TODAY)["2027-03"].closed.closed


def test_health_check_recomputes_each_stored_month(world):
    """D259 amended: each stored figure equals its recomputation from the stored rows; a line
    booked later in a stored month makes the check name the figures that moved."""
    _closed_history(world)
    assert world.query(health.health, date(2027, 4, 10)).one_offs_left_out_since is None
    for month in ("2027-01", "2027-02", "2027-03"):
        world.cmd(health.store_snapshot, month)
    # phase 10 D308: „Seit Januar 2027: Einmaliges nicht im Schnitt“
    assert world.query(health.health, date(2027, 4, 10)).one_offs_left_out_since == "2027-01"
    result = world.query(health.health_check, "2027-01", "2027-03", date(2027, 4, 10))
    assert result.months == ["2027-01", "2027-02", "2027-03"]
    assert result.match, [r for r in result.rows if not r.equal]
    _booked(world, date(2027, 3, 25), "-100.00", C.INSURANCE)
    result = world.query(health.health_check, "2027-01", "2027-03", date(2027, 4, 10))
    assert not result.match
    # The rent less interest of every month is all of its saving, so the rates stay 1; the
    # free cash, the debt service ratio and the month-end's balance move, in March only.
    moved = {(r.month, r.figure) for r in result.rows if not r.equal}
    assert moved == {
        ("2027-03", "free_cash"),
        ("2027-03", "debt_service_ratio"),
        ("2027-03", "net_worth"),
    }
    assert [i.agrees for i in result.inputs if i.month == "2027-03"] == [False]
    with pytest.raises(RuleViolationError) as refused:
        world.query(health.health_check, "2026-01", "2026-03", date(2027, 4, 10))
    assert list(refused.value.codes) == [HEALTH_MONTH_END_MISSING]


def _cover(world: World, last: date) -> None:
    """The rental account's one import covers 5 Jan 2026 to `last`."""
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(import_table)
            .where(import_table.c.id == world.import_id)
            .values(first_booking_on=date(2026, 1, 5), last_booking_on=last)
        )


def test_a_run_frozen_before_the_months_lines_is_captured_again(world):
    """Review of PR #426, finding 2: phase 6's month-end job freezes 31 Mar on 1 Apr, before
    March's statement is in; once it is and March closes, the daily step captures the day
    again and stores March beside the new run, which equals the day read again, so
    health-check finds nothing to report. The frozen run stays (runs are insert-only)."""
    _cover(world, date(2027, 2, 28))
    for year, month in [(2026, m) for m in range(1, 13)] + [(2027, m) for m in (1, 2)]:
        _booked(world, date(year, month, 5), "950.00", C.RENT_COLD)
        _booked(world, date(year, month, 20), "-640.00", C.LOAN_INTEREST)
    first_of_april = datetime(2027, 4, 1, 8, 0, tzinfo=UTC)
    frozen = world.cmd(net_worth.capture_month_end, date(2027, 3, 31), now=first_of_april).run
    stored = world.cmd(health.store_missing_snapshots, now=first_of_april)
    assert "2027-03" not in [r.snapshot.figures.month.month for r in stored]  # not closed
    _booked(world, date(2027, 3, 5), "950.00", C.RENT_COLD)
    _booked(world, date(2027, 3, 20), "-640.00", C.LOAN_INTEREST)
    _cover(world, date(2027, 3, 31))
    eleventh = datetime(2027, 4, 11, 8, 30, tzinfo=UTC)
    stored = world.cmd(health.store_missing_snapshots, now=eleventh)
    assert [r.snapshot.figures.month.month for r in stored] == ["2027-03"]
    snapshot = stored[0].snapshot
    assert snapshot.net_worth_run_id != frozen.net_worth_run_id
    run = world.query(net_worth.stored_run, snapshot.net_worth_run_id)
    again = world.query(net_worth.net_worth, date(2027, 3, 31))
    assert run.total == again.total
    assert world.query(net_worth.stored_run, frozen.net_worth_run_id).total != again.total
    result = world.query(health.health_check, "2027-03", "2027-03", date(2027, 4, 11))
    assert result.match, [r for r in result.rows if not r.equal]
    assert world.cmd(health.store_missing_snapshots, now=eleventh) == []


def _coin_held_since_july(world: World, price_on: date):
    """An exchange holding 2 coins since 1 Jul 2026 whose only price is of `price_on`."""
    exchange = _account(world, "Börse", AccountKind.EXCHANGE)

    def stored(ctx):
        coin = _asset(ctx, kind="crypto", symbol="BTC", name="Bitcoin")
        _held(ctx, exchange, coin, "2")
        _price(ctx, coin, price_on, "50000.00", "user")
        return coin

    return exchange, world.cmd(stored)


def test_a_month_end_without_a_price_is_not_stored(world):
    """Review of PR #426, finding 1: a month-end whose reading leaves a position without a
    value (no price on or before the day) is never stored: the button is refused with
    health.month_end_incomplete, the daily step skips the month, and the month's to-do names
    the gap with the net worth's own button. A run captured by hand that omits the coin
    gets no snapshot either."""
    _closed_history(world)
    _coin_held_since_july(world, date(2027, 4, 5))
    captured = world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    assert captured.unvalued_positions == 1
    with pytest.raises(RuleViolationError) as refused:
        world.cmd(health.store_snapshot, "2027-03")
    assert list(refused.value.codes) == [HEALTH_MONTH_END_INCOMPLETE]
    stored = world.cmd(health.store_missing_snapshots)
    assert [r.snapshot.figures.month.month for r in stored] == health.months_between(
        "2026-01", "2026-06"
    )
    [march] = world.query(health.health_months, "2027-03", "2027-03", TODAY)
    assert march.closed.closed and march.snapshot_id is None
    assert march.todo.code is HealthTodoCode.MONTH_END_MISSING
    assert "31.03.2027 ist noch unvollständig" in march.todo.text
    assert "Bitcoin" in march.todo.text
    assert (march.todo.action, march.todo.href) == ("Kursquelle wählen", "/assets")


def test_health_check_reports_a_holding_the_stored_month_end_lacks(world):
    """Review of PR #426: a holding booked after its month was stored, with no price on the
    day, leaves the day read again without a value for it: health-check reports it as an
    input that disagrees, not „gleich“."""
    _closed_history(world)
    world.cmd(health.store_snapshot, "2027-03")
    assert world.query(health.health_check, "2027-03", "2027-03", TODAY).match
    exchange, _ = _coin_held_since_july(world, date(2027, 4, 5))
    result = world.query(health.health_check, "2027-03", "2027-03", TODAY)
    assert not result.match
    assert all(r.equal for r in result.rows)  # the figures recompute from the stored run
    [gap] = [i for i in result.inputs if not i.agrees]
    assert gap.account_id == exchange
    assert gap.reason.startswith("Am Monatsende ohne Wert und nicht im gespeicherten Abschluss")


def test_the_daily_step_waits_for_the_month_before(world):
    """Review of PR #426, finding 3: while February has a line waiting, the daily step does
    not store March, though March is closed, and March shows no to-do of its own (February's
    reasons say what to do); once February closes, both are stored, March with its full
    window, and health-check finds nothing to report."""
    _closed_history(world)
    waiting = world.line(date(2027, 2, 17), "-80.00")
    stored = world.cmd(health.store_missing_snapshots)
    assert [r.snapshot.figures.month.month for r in stored] == health.months_between(
        "2026-01", "2027-01"
    )
    february, march = world.query(health.health_months, "2027-02", "2027-03", TODAY)
    assert not february.closed.closed and march.closed.closed
    assert march.snapshot_id is None and march.todo is None
    _book(world, waiting, "-80.00", C.INSURANCE, property_id=world.property_id)
    stored = world.cmd(health.store_missing_snapshots)
    assert [r.snapshot.figures.month.month for r in stored] == ["2027-02", "2027-03"]
    assert len(stored[1].snapshot.figures.window.months) == 12
    result = world.query(health.health_check, "2027-02", "2027-03", TODAY)
    assert result.match, [r for r in result.rows if not r.equal]


def test_the_first_month_of_history_does_not_wait_for_the_month_before(world):
    """Review of PR #426, finding 3: the household's history starts the day after its
    earliest opening balance (31 May 2026 here, so June). May cannot close, since no account
    holds its lines, yet June is stored without waiting for it, and every month after June
    follows. With the opening's own month as the start, June would wait for May for good."""
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(account_table)
            .where(account_table.c.id == world.account_id)
            .values(opening_balance_on=date(2026, 5, 31))
        )
        conn.execute(
            update(import_table)
            .where(import_table.c.id == world.import_id)
            .values(first_booking_on=date(2026, 6, 5), last_booking_on=date(2027, 3, 31))
        )
    for year, month in [(2026, m) for m in range(6, 13)] + [(2027, m) for m in range(1, 4)]:
        _booked(world, date(year, month, 5), "950.00", C.RENT_COLD)
        _booked(world, date(year, month, 20), "-640.00", C.LOAN_INTEREST)
    assert world.query(health._first_history_month) == "2026-06"
    reads = world.query(health.read_months, ["2026-05", "2026-06"], TODAY)
    assert not reads["2026-05"].closed.closed and reads["2026-06"].closed.closed
    stored = world.cmd(health.store_missing_snapshots)
    assert [r.snapshot.figures.month.month for r in stored] == health.months_between(
        "2026-06", "2027-03"
    )


class _April(datetime):
    """The CLI's clock on 10 Apr 2027, the day these tests' months have ended."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        return datetime(2027, 4, 10, 10, 0, tzinfo=UTC).astimezone(tz)


def test_the_cli_prints_gleich_or_the_difference(world, dbname, monkeypatch, capsys):
    """`hausbuch health-check`: exit 0 and „gleich“ per figure while the stored months match,
    exit 1 with the difference once a later booking moved one; a range with no snapshot is
    an error on stderr. Nothing is written."""
    _closed_history(world)
    for month in ("2027-01", "2027-02", "2027-03"):
        world.cmd(health.store_snapshot, month)
    monkeypatch.setattr(cli, "datetime", _April)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["health-check", "--workspace", str(world.ws), "--from", "2027-01"]
    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert "2027-03 free_cash: gleich" in out
    assert out[-1] == "2027-01 bis 2027-03: alles gleich"
    _booked(world, date(2027, 3, 25), "-100.00", C.INSURANCE)
    assert cli.main(argv, environ) == cli.EXIT_ERROR
    out = capsys.readouterr().out.splitlines()
    assert any(line.startswith("2027-03 free_cash: Differenz -") for line in out)
    assert out[-1] == "2027-01 bis 2027-03: abweichend"
    missing = ["health-check", "--workspace", str(world.ws), "--from", "2026-01", "--to", "2026-03"]
    assert cli.main(missing, environ) == cli.EXIT_ERROR
    assert "health.month_end_missing" in capsys.readouterr().err
    assert (_snapshots(world), _events(world)) == (3, 3)


def test_health_carries_the_last_ended_month_as_the_months_read_returns_it(world):
    """Phase 10 §7.3 (2): GET /health's `last_ended` is the last ended month's household
    month exactly as GET /health/months returns it for that month alone, closed or not (a
    line waiting in March keeps it open), so Übersicht's cash flow tile can read its savings
    rate from GET /health; before the first ended month there is none."""
    _closed_history(world)
    got = world.query(health.health, TODAY)
    [march] = world.query(health.health_months, "2027-03", "2027-03", TODAY)
    assert got.last_ended == march.household and got.last_ended.month == "2027-03"
    assert got.last_ended.model_dump_json() == march.household.model_dump_json()

    _booked(world, date(2027, 3, 25), "-45.00", C.REPAIRS, confirm=False)  # waiting
    got = world.query(health.health, TODAY)
    [march] = world.query(health.health_months, "2027-03", "2027-03", TODAY)
    assert not march.closed.closed
    assert got.last_ended.model_dump_json() == march.household.model_dump_json()

    assert world.query(health.health, date(2026, 1, 15)).last_ended is None


def _electronics_in_january(world: World) -> None:
    """_closed_history with a private current account covered from January 2026 to March
    2027 (its history starting with a grocery line of 10 € on 2 Jan 2026) and one purchase of
    10 € on 12 Jan 2027 booked as Elektronik (one-off by its code, phase 10 D307)."""
    _closed_history(world)
    current = _account(world, "Giro", AccountKind.BANK_CURRENT)
    first, line = _statement(
        world,
        current,
        date(2026, 1, 1),
        date(2027, 3, 31),
        datetime(2027, 4, 9, tzinfo=UTC),
        closing="-20.00",
        lines=[date(2026, 1, 2), date(2027, 1, 12)],
    )
    _book(world, first, "-10.00", C.HH_GROCERIES)
    _book(world, line, "-10.00", C.HH_ELECTRONICS)


ENDS = {"2027-01": date(2027, 1, 31), "2027-02": date(2027, 2, 28), "2027-03": date(2027, 3, 31)}


def _rate(world: World, month: str) -> Decimal:
    with workspace_transaction(world.engine, world.ws) as conn:
        figures = conn.execute(
            select(health_snapshot_table.c.figures).where(
                health_snapshot_table.c.month_end == ENDS[month]
            )
        ).scalar_one()
    return Decimal(figures["savings_rate"]["rate"])


def test_health_check_reads_the_switches_in_force_when_each_month_was_stored(world):
    """Phase 10 §7.12 (c): a recomputation of a stored month reads the default scenario's
    one-off switches in force when it was stored. January is stored with Elektronik one-off
    (the taxonomy's default), February after a default scenario switched it to regular,
    March after that scenario was withdrawn; each window holds the January purchase, so
    February's rate is below the others. health-check matches every month, though today's
    switches are the default ones, and with Elektronik regular again today it still does,
    while today's own rate moves."""
    _electronics_in_january(world)
    world.cmd(health.store_snapshot, "2027-01")
    world.now = NOW + timedelta(days=1)
    row = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(one_off_overrides={C.HH_ELECTRONICS: False}),
        ),
    )
    world.cmd(health.store_snapshot, "2027-02")
    world.now = NOW + timedelta(days=2)
    world.cmd(household_scenarios.withdraw_scenario, row.id)
    world.cmd(health.store_snapshot, "2027-03")
    assert _rate(world, "2027-02") < _rate(world, "2027-01") == _rate(world, "2027-03")
    result = world.query(health.health_check, "2027-01", "2027-03", TODAY)
    assert result.match, [r for r in result.rows if not r.equal]
    today = world.query(health.health, TODAY).savings_rate.rate
    world.now = NOW + timedelta(days=3)
    world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(one_off_overrides={C.HH_ELECTRONICS: False}),
        ),
    )
    assert world.query(health.health, TODAY).savings_rate.rate < today
    result = world.query(health.health_check, "2027-01", "2027-03", TODAY)
    assert result.match, [r for r in result.rows if not r.equal]


def _stored_again(world: World, month: str, *, d308: bool, at: datetime) -> None:
    """A newer row of `month` (the newest counts), a copy of its newest one with the savings
    rate as a row stored before D308 holds it (`d308` False: `rate_regular` set, the flag
    off) or as one stored under it; for a row whose window holds no one-off spending."""
    h = health_snapshot_table.c
    with workspace_transaction(world.engine, world.ws) as conn:
        row = conn.execute(
            select(health_snapshot_table)
            .where(h.month_end == ENDS[month])
            .order_by(h.created_at.desc())
            .limit(1)
        ).one()
        figures = dict(row.figures)
        rate = dict(figures["savings_rate"])
        assert Decimal(rate["one_off_spending"]) == 0
        rate["one_offs_left_out"] = d308
        rate["rate_regular"] = None if d308 else rate["rate"]
        figures["savings_rate"] = rate
        conn.execute(
            insert(health_snapshot_table).values(
                {**row._mapping, "id": uuid4(), "figures": figures, "created_at": at}
            )
        )


def test_one_offs_left_out_since_names_the_newest_unbroken_run(world):
    """Phase 10 D308: „Seit <Monat>“ names the first month of the newest unbroken run of
    months whose newest row is stored under D308: a month whose newest row is stored before
    it (the savings rate without the flag) between them starts the run later, a newest month
    stored before D308 names none, and the months stored again under it restore the run."""
    _closed_history(world)
    for month in ("2027-01", "2027-02", "2027-03"):
        world.cmd(health.store_snapshot, month)

    assert world.query(health.health, TODAY).one_offs_left_out_since == "2027-01"
    _stored_again(world, "2027-02", d308=False, at=NOW + timedelta(hours=1))
    assert world.query(health.health, TODAY).one_offs_left_out_since == "2027-03"
    _stored_again(world, "2027-03", d308=False, at=NOW + timedelta(hours=1))
    assert world.query(health.health, TODAY).one_offs_left_out_since is None
    _stored_again(world, "2027-02", d308=True, at=NOW + timedelta(hours=2))
    _stored_again(world, "2027-03", d308=True, at=NOW + timedelta(hours=2))
    assert world.query(health.health, TODAY).one_offs_left_out_since == "2027-01"


def test_health_check_over_months_stored_before_d308_names_the_expected_figures(world):
    """Phase 10 §7.12 (c): a month stored before D308 is recomputed under it and reported
    unequal where the spec expects it, never refused. Simulated: the months stored while a
    default scenario counted Elektronik as regular (as every code but the car purchase was
    before D307) with their savings rate as before D308, stored again after the scenario was
    withdrawn, so their recomputation reads the taxonomy's switches (Elektronik one-off)."""
    _electronics_in_january(world)
    world.now = NOW + timedelta(days=1)
    row = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(one_off_overrides={C.HH_ELECTRONICS: False}),
        ),
    )
    for month in ("2027-01", "2027-02", "2027-03"):
        world.cmd(health.store_snapshot, month)
    world.now = NOW + timedelta(days=2)
    world.cmd(household_scenarios.withdraw_scenario, row.id)
    for month in ("2027-01", "2027-02", "2027-03"):
        _stored_again(world, month, d308=False, at=NOW + timedelta(days=3))
    assert world.query(health.health, TODAY).one_offs_left_out_since is None
    result = world.query(health.health_check, "2027-01", "2027-03", TODAY)
    moved = {(r.month, r.figure) for r in result.rows if not r.equal}
    # The reserve's need reads need-class spending only since phase 10 D305; Elektronik is a
    # want, so the switch no longer moves `reserve_months` (it did while the need was every
    # regular payment).
    assert moved == {
        (month, figure)
        for month in ("2027-01", "2027-02", "2027-03")
        for figure in ("savings_rate_window", "free_cash")
    }
    assert all(i.agrees for i in result.inputs)


def test_a_one_off_switch_alone_does_not_show_a_stored_month_as_changed(world):
    """GET /health/months (review of phase 10 §7.12): „seit dem Abschluss geändert“ compares
    the month's income, spending, saved and own savings rate only, whose meaning no one-off
    switch changes. January is stored while Elektronik counted as regular (the code set before
    D307), read after that scenario is withdrawn with Elektronik one-off: no stored copy. A
    switch saved after storing (Lebensmittel one-off) shows none either; a line booked later
    still does."""
    _electronics_in_january(world)
    world.now = NOW + timedelta(days=1)
    row = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(one_off_overrides={C.HH_ELECTRONICS: False}),
        ),
    )
    world.cmd(health.store_snapshot, "2027-01")
    world.now = NOW + timedelta(days=2)
    world.cmd(household_scenarios.withdraw_scenario, row.id)
    [january] = world.query(health.health_months, "2027-01", "2027-01", TODAY)
    assert january.snapshot_id is not None
    assert january.household.one_off_spending == Decimal("10.00")  # read: Elektronik one-off
    assert january.stored is None
    # stored again under today's switches (Elektronik one-off), then switched to regular
    world.cmd(health.store_snapshot, "2027-01")
    world.now = NOW + timedelta(days=3)
    world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(one_off_overrides={C.HH_ELECTRONICS: False}),
        ),
    )
    [january] = world.query(health.health_months, "2027-01", "2027-01", TODAY)
    assert january.household.one_off_spending == 0
    assert january.stored is None
    # a line booked later still shows the stored month beside the month on read
    _booked(world, date(2027, 1, 20), "-100.00", C.INSURANCE)
    [january] = world.query(health.health_months, "2027-01", "2027-01", TODAY)
    assert january.stored is not None
    assert january.stored.spending != january.household.spending or (
        january.stored.rental_result != january.household.rental_result
    )


def _groceries_in_2027(world: World) -> None:
    """_closed_history with a private current account covered from January 2026 to March
    2027 (its history starting with a line on 2 Jan 2026) and 10 € of groceries on the 5th
    of January, February and March 2027 (a need by the taxonomy, phase 10 D272)."""
    _closed_history(world)
    current = _account(world, "Giro", AccountKind.BANK_CURRENT)
    first, *groceries = _statement(
        world,
        current,
        date(2026, 1, 1),
        date(2027, 3, 31),
        datetime(2027, 4, 9, tzinfo=UTC),
        closing="-40.00",
        lines=[date(2026, 1, 2), date(2027, 1, 5), date(2027, 2, 5), date(2027, 3, 5)],
    )
    _book(world, first, "-10.00", C.HH_GROCERIES)
    for line in groceries:
        _book(world, line, "-10.00", C.HH_GROCERIES)


def test_the_reserves_need_is_the_budgets_notwendiges_as_stored(world):
    """Phase 10 D305: the reserve's need is the window's mean need-class spending (30 € over
    the twelve months April 2026 to March 2027, 2.50 €; the flat carries itself), named per
    code with its basis. A default scenario moving groceries to Wünsche takes them out of
    the need; a month stored under it is recomputed by health-check with that override, so
    it matches after the scenario is withdrawn while today's need is 2.50 € again."""
    _groceries_in_2027(world)
    got = world.query(health.health, TODAY).reserve
    assert (got.need, got.need_codes) == (Decimal("2.50"), {C.HH_GROCERIES: Decimal("2.50")})
    assert (got.need_basis, got.need_basis_from) == (ReserveNeedBasis.NEEDS, "2026-11")
    world.now = NOW + timedelta(days=1)
    row = world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name="Basis",
            is_default=True,
            levers=HouseholdLevers(budget_class_overrides={C.HH_GROCERIES: BudgetClass.WANT}),
        ),
    )
    moved = world.query(health.health, TODAY).reserve
    assert (moved.need, moved.need_codes, moved.months) == (Decimal("0.00"), {}, None)
    world.cmd(health.store_snapshot, "2027-03")
    world.now = NOW + timedelta(days=2)
    world.cmd(household_scenarios.withdraw_scenario, row.id)
    assert world.query(health.health, TODAY).reserve.need == Decimal("2.50")
    result = world.query(health.health_check, "2027-03", "2027-03", TODAY)
    assert result.match, [r for r in result.rows if not r.equal]
