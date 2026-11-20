"""`hausbuch recapture-months` (docs/phases/phase-6.md D265 (5)) as the schema owner: per month
„unverändert“, „neu festgehalten“ or the refusal with its codes, never an amount; a month
without a stored run skipped; insert-only, the old runs and snapshots kept; --dry-run writes
nothing. Invented owners, bank and amounts only (the World of tests/db/matching_support.py)."""

import re
from datetime import date

import pytest
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_health_snapshots import NOW, _April, _booked, _closed_history, _snapshots
from sqlalchemy import func, select

from hausbuch import cli
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import valuation_snapshot as snapshot_table
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.services import health, net_worth

pytestmark = pytest.mark.db


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _runs(world) -> int:
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(func.count(func.distinct(snapshot_table.c.net_worth_run_id)))
        ).scalar_one()


def _cli(world, dbname, *extra):
    argv = [
        "recapture-months",
        "--workspace",
        str(world.ws),
        "--from",
        "2026-12",
        "--to",
        "2027-03",
        *extra,
    ]
    return cli.main(argv, {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)})


def test_recapture_months_prints_each_month_and_never_an_amount(world, dbname, monkeypatch, capsys):
    _closed_history(world)
    world.cmd(net_worth.capture_month_end, date(2027, 1, 31))  # a run, no key figures
    for month in ("2027-02", "2027-03"):
        world.cmd(health.store_snapshot, month)
    monkeypatch.setattr(cli, "datetime", _April)
    before = (_runs(world), _snapshots(world))
    assert before == (3, 2)

    # Nothing changed since: every stored month reads the same; December has no run.
    assert _cli(world, dbname) == cli.EXIT_OK
    assert capsys.readouterr().out.splitlines() == [
        "2026-12: kein gespeicherter Monatsabschluss, übersprungen",
        "2027-01: Monatsabschluss unverändert",
        "2027-02: Monatsabschluss unverändert; Kennzahlen unverändert",
        "2027-03: Monatsabschluss unverändert; Kennzahlen unverändert",
    ]
    assert (_runs(world), _snapshots(world)) == before

    # A cost booked late in February moves February's and March's balance and key figures.
    _booked(world, date(2027, 2, 25), "-100.00", C.INSURANCE)
    changed = [
        "2026-12: kein gespeicherter Monatsabschluss, übersprungen",
        "2027-01: Monatsabschluss unverändert",
        "2027-02: Monatsabschluss neu festgehalten; Kennzahlen neu festgehalten",
        "2027-03: Monatsabschluss neu festgehalten; Kennzahlen neu festgehalten",
    ]
    assert _cli(world, dbname, "--dry-run") == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out == [*changed, "Probelauf: nichts gespeichert"]
    assert (_runs(world), _snapshots(world)) == before  # rolled back

    assert _cli(world, dbname) == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out == changed
    assert (_runs(world), _snapshots(world)) == (5, 4)  # the old rows kept
    assert not any(re.search(r"\d+[.,]\d{2}\b|€", line) for line in out)

    # A line waiting in March leaves the month not closed: the run is captured again, the
    # key figures are refused by code and nothing of them is written.
    world.line(date(2027, 3, 12), "-30.00")
    assert _cli(world, dbname) == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out[3] == (
        "2027-03: Monatsabschluss neu festgehalten; Kennzahlen abgelehnt (health.month_not_closed)"
    )
    assert (_runs(world), _snapshots(world)) == (6, 4)


def test_recapture_months_refuses_a_reversed_range(world, dbname, capsys):
    argv = [
        "recapture-months",
        "--workspace",
        str(world.ws),
        "--from",
        "2027-03",
        "--to",
        "2027-01",
    ]
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    assert cli.main(argv, environ) == cli.EXIT_ERROR
    assert "--to is before --from" in capsys.readouterr().err
