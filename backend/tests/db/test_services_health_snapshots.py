"""Phase 9 step 1, the service side (docs/phases/phase-9.md §5.5, §13, D242, D259 amended):
GET /health, the months on read, the stored snapshot of a closed month, the daily task's step
and `hausbuch health-check`, over invented data (owners A and B, a flat and its rental
account). The arithmetic is held by the rule fixtures; these tests hold the reads and writes."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from sqlalchemy import func, select, update

from hausbuch import cli
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import health_snapshot as health_snapshot_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.health_daily import run_health_daily
from hausbuch.rules.errors import (
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
from hausbuch.schemas.health import HealthSnapshotOutcome, HealthTodoCode
from hausbuch.services import allocations, health, net_worth
from hausbuch.services.errors import RuleViolationError

C = AllocationCategory
NOW = datetime(2027, 4, 10, 8, 0, tzinfo=UTC)


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


def test_an_unclosed_month_or_one_without_a_run_is_refused(world):
    """POST /health/snapshots: a month still running, a month with a waiting line and a month
    whose month-end has no run are refused, nothing written."""
    _closed_history(world)
    world.line(date(2027, 2, 12), "-30.00")
    world.cmd(net_worth.capture_month_end, date(2027, 2, 28))
    for month, code in [
        ("2027-04", HEALTH_MONTH_NOT_CLOSED),
        ("2025-12", HEALTH_MONTH_NOT_CLOSED),
        ("2027-02", HEALTH_MONTH_NOT_CLOSED),
        ("2027-01", HEALTH_MONTH_END_MISSING),
    ]:
        with pytest.raises(RuleViolationError) as refused:
            world.cmd(health.store_snapshot, month)
        assert list(refused.value.codes) == [code]
    assert _snapshots(world) == 0


def test_the_daily_step_stores_each_missing_month_once(world):
    """§11.5: every ended, closed month with a run and no snapshot gets one; a second run
    stores nothing; a month without a run waits."""
    _closed_history(world)
    for day in (date(2027, 1, 31), date(2027, 3, 31)):
        world.cmd(net_worth.capture_month_end, day)
    stored = world.cmd(health.store_missing_snapshots)
    assert [r.snapshot.figures.month.month for r in stored] == ["2027-01", "2027-03"]
    assert world.cmd(health.store_missing_snapshots) == []
    assert (_snapshots(world), _events(world)) == (2, 2)


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
    assert (first.stored, first.failed_workspaces) == (1, 0)
    again = run_health_daily(deps, workspace_ids=[world.ws])
    assert (again.stored, again.failed_workspaces) == (0, 0)
    with workspace_transaction(world.engine, world.ws) as conn:
        actors = conn.execute(
            select(event_table.c.actor_type, event_table.c.actor_id).where(
                event_table.c.type == "health.snapshot_stored"
            )
        ).all()
    assert [(a.actor_type, a.actor_id) for a in actors] == [("system", "health_daily")]


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
    assert views[0].todo.href == "/?month_end=2027-02-28" and views[1].todo is None
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


def test_health_check_recomputes_each_stored_month(world):
    """D259 amended: each stored figure equals its recomputation from the stored rows; a line
    booked later in a stored month makes the check name the figures that moved."""
    _closed_history(world)
    for day in (date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)):
        world.cmd(net_worth.capture_month_end, day)
    world.cmd(health.store_missing_snapshots)
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
    for day in (date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)):
        world.cmd(net_worth.capture_month_end, day)
    world.cmd(health.store_missing_snapshots)
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
