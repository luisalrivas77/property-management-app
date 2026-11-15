"""`hausbuch repair-periods` (docs/phases/phase-2.md D27 amended, 15 Nov 2026).

A house number in a card line („2093 … Pike“) had been read as the year a payment is for, so
the model's proposals carried a period for 2093. The command proposes such a line again with
its period read by the current rule, keeping everything else; a confirmed line and a plausible
period are left alone. Invented names only."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_private_matching import NOW, Private, _answer_line, _propose, _rows

from hausbuch import cli
from hausbuch.schemas.allocation import AllocationDraft, AllocationMethod, AllocationSet
from hausbuch.services import allocations, matching

pytestmark = pytest.mark.db

STREET = "EXAMPLE CLOUD 2093 Market Pike Unit 12"
FAR = {"service_period_start": date(2093, 1, 1), "service_period_end": date(2093, 12, 31)}


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def test_repair_periods_reproposes_a_far_period_and_leaves_the_rest(dbname, world, private, capsys):
    once = private.line(date(2026, 7, 17), "-44.54", name="EXAMPLE CLOUD", purpose=STREET)
    _propose(world, once, "-44.54", "uncategorized", **FAR)
    monthly = private.line(date(2026, 10, 19), "-45.34", name="EXAMPLE CLOUD", purpose=STREET)
    _propose(world, monthly, "-45.34", "hh_cloud", recurring=True, **FAR)
    plausible = private.line(date(2026, 3, 12), "-80.00", name="Stadtwerke", purpose="NK 2025")
    _propose(
        world,
        plausible,
        "-80.00",
        "hh_energy",
        service_period_start=date(2025, 1, 1),
        service_period_end=date(2025, 12, 31),
    )
    answered = private.line(date(2026, 8, 17), "-44.06", name="EXAMPLE CLOUD", purpose=STREET)
    _propose(world, answered, "-44.06", "uncategorized", **FAR)
    _answer_line(world, answered, "-44.06", "hh_cloud")  # a person's answer stays as given
    (kept_answer,) = _rows(world, answered)
    (kept_plausible,) = _rows(world, plausible)
    (old,) = _rows(world, monthly)

    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["repair-periods", "--workspace", str(world.ws)]
    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    assert "would propose 2 line(s) again" in capsys.readouterr().out
    assert _rows(world, monthly) == [old]

    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "proposed 2 line(s) again" in out and "postgresql" not in out

    (row,) = _rows(world, once)  # not recurring: no period at all
    assert (row.status, row.method, row.category) == ("proposed", "llm", "uncategorized")
    assert row.service_period_start is None and row.service_period_end is None
    (row,) = _rows(world, monthly)  # recurring: its booking month
    assert (row.status, row.method, row.category, row.recurring) == (
        "proposed",
        "llm",
        "hh_cloud",
        True,
    )
    assert (row.service_period_start, row.service_period_end) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
    assert row.supersedes_id == old.id and row.reasoning == old.reasoning
    assert _rows(world, answered) == [kept_answer]
    assert _rows(world, plausible) == [kept_plausible]

    assert cli.main(argv, environ) == cli.EXIT_OK  # a second run finds nothing
    assert "proposed 0 line(s) again" in capsys.readouterr().out


def test_an_answer_given_meanwhile_is_never_reproposed(world, private, monkeypatch):
    """Review F1: a person's answer that commits after the candidate read is seen under the
    line's lock, and the line is left alone."""
    line = private.line(date(2026, 8, 17), "-44.06", name="EXAMPLE CLOUD", purpose=STREET)
    _propose(world, line, "-44.06", "uncategorized", **FAR)
    real = matching.load_line
    answered = []

    def answer_first(conn, transaction_id, *, lock=False):
        if not answered:
            answered.append(True)
            _answer_line(world, line, "-44.06", "hh_cloud")  # commits on another connection
        return real(conn, transaction_id, lock=lock)

    monkeypatch.setattr(matching, "load_line", answer_first)
    assert world.cmd(matching.repair_periods) == 0
    (row,) = _rows(world, line)
    assert (row.status, row.category) == ("confirmed", "hh_cloud")


def test_a_plausible_period_beside_a_far_one_is_kept(world, private):
    """Review F4: only the row whose period is implausible is read again."""
    line = private.line(date(2026, 7, 17), "-100.00", name="EXAMPLE CLOUD", purpose=STREET)
    july = {"service_period_start": date(2026, 7, 1), "service_period_end": date(2026, 7, 31)}
    two = AllocationSet(
        transaction_id=line,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.700"),
        reasoning="Zwei Teile.",
        allocations=[
            AllocationDraft(amount=Decimal("-60.00"), category="hh_cloud", **july),
            AllocationDraft(amount=Decimal("-40.00"), category="uncategorized", **FAR),
        ],
    )
    world.cmd(allocations.record_set, two, confirm=False, auto=False)
    assert world.cmd(matching.repair_periods) == 1
    rows = {r.category: r for r in _rows(world, line)}
    assert (rows["hh_cloud"].service_period_start, rows["hh_cloud"].service_period_end) == (
        date(2026, 7, 1),
        date(2026, 7, 31),
    )
    assert rows["uncategorized"].service_period_start is None
