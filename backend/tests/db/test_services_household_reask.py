"""The one-off re-ask of household lines (docs/phases/phase-6.md D159 amended 13 Nov 2026):
services.matching.reask_household and the `hausbuch household-reask` command.

A private line still proposed in a coarse code („Sonstiges“, „Freizeit und Reisen“ before the
relabel, …) is asked of the model again, and the answer supersedes the proposal; a confirmed
line, a possible purchase cost of the flat and a line already asked with the current prompt
are left alone, and nothing is queued while the switch is off. Invented names and amounts
only; the model is a FakeModelClient."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_private_matching import (
    NOW,
    OWNER_A,
    Private,
    _answer,
    _consent_both,
    _deps,
    _rows,
)
from sqlalchemy import insert, select, text

from hausbuch import cli
from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import property_acquisition_cost as acquisition_cost_table
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.services import allocations, matching, workspace_settings
from hausbuch.services.matching import ReaskResult, reask_household

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _no_leftover_jobs(app_engine):
    """These tests read procrastinate_jobs; the queue is session-wide (test_jobs_queue)."""

    def clear():
        with app_engine.begin() as conn:
            conn.execute(text("DELETE FROM procrastinate_jobs"))

    clear()
    yield
    clear()


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def _propose(world: World, tx, amount: str, category: str, *, method=AllocationMethod.LLM):
    """A proposal as an earlier model answer left it (no ai_call: asked with an old prompt)."""
    found = AllocationSet(
        transaction_id=tx,
        method=method,
        confidence=Decimal("0.700"),
        reasoning="Nicht eindeutig.",
        allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
    )
    world.cmd(allocations.record_set, found, confirm=False, auto=False)


def _jobs(world: World):
    return world.query(
        lambda c: c.execute(
            text("SELECT task_name, args FROM procrastinate_jobs ORDER BY id")
        ).all()
    )


def _reask(world: World, tx, deps):
    return run_match_transaction(
        deps, workspace_id=world.ws, transaction_id=tx, attempt=0, reask=True
    )


def _all_rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table).where(allocation_table.c.transaction_id == tx)
        ).all()
    )


def test_a_parked_line_is_asked_again_and_the_answer_supersedes_the_proposal(
    world, private, app_engine, tmp_path
):
    _consent_both(world)
    tx = private.line(date(2026, 10, 4), "-24.00", name="Salon Beispiel", purpose="Karte")
    _propose(world, tx, "-24.00", "hh_other")

    result = world.cmd(reask_household)
    assert result == ReaskResult(queued=1, held_back=0, purchase_costs=0, already_asked=0)
    ((task, args),) = _jobs(world)
    assert task == "match_transaction"
    assert args == {"workspace_id": str(world.ws), "transaction_id": str(tx), "reask": "1"}

    client = FakeModelClient([text_response(_answer("hh_personal_care", "-24.00"))])
    deps = _deps(app_engine, tmp_path, client)
    plan = _reask(world, tx, deps)
    assert plan.outcome == "written"
    (request,) = client.requests
    assert request.prompt_version == matching.household_prompt_version()
    (row,) = _rows(world, tx)
    assert (row.category, row.method, row.status) == ("hh_personal_care", "llm", "proposed")
    # Append-only: the old proposal stays, superseded (both rows share the test clock).
    old, new = sorted(_all_rows(world, tx), key=lambda r: r.supersedes_id is not None)
    assert (old.category, old.status) == ("hh_other", "superseded")
    assert new.supersedes_id == old.id

    # Idempotent: a second run queues nothing, and a job queued twice asks nothing again.
    assert world.cmd(reask_household).already_asked == 0  # hh_personal_care: not parked
    plan = _reask(world, tx, deps)
    assert (plan.outcome, plan.reason) == ("skipped", "matching.asked_already")
    assert len(client.requests) == 1


def test_a_line_answered_the_same_again_is_not_asked_a_third_time(
    world, private, app_engine, tmp_path
):
    """The model keeps „Sonstiges“: the proposal is unchanged, but the call is recorded with
    the current prompt, so the next run and a duplicate job leave the line alone."""
    _consent_both(world)
    tx = private.line(date(2026, 10, 5), "-9.99", name="Händler Beispiel")
    _propose(world, tx, "-9.99", "hh_other")
    same = _answer("hh_other", "-9.99").replace('"recurring": true', '"recurring": false')
    client = FakeModelClient([text_response(same)])
    deps = _deps(app_engine, tmp_path, client)
    world.cmd(reask_household)
    assert _reask(world, tx, deps).reason == "matching.unchanged"
    assert world.cmd(reask_household) == ReaskResult(0, 0, 0, 1)
    assert _reask(world, tx, deps).reason == "matching.asked_already"
    assert len(client.requests) == 1
    calls = world.query(
        lambda c: c.execute(select(ai_call_table).where(ai_call_table.c.transaction_id == tx)).all()
    )
    assert [c.prompt_version for c in calls] == [matching.household_prompt_version()]


def test_confirmed_lines_other_codes_and_the_rental_account_are_never_taken(world, private):
    _consent_both(world)
    confirmed = private.line(date(2026, 10, 6), "-30.00", name="Laden Beispiel")
    _propose(world, confirmed, "-30.00", "hh_other")
    world.cmd(allocations.confirm_proposed, confirmed)
    groceries = private.line(date(2026, 10, 7), "-50.00", name="Supermarkt Beispiel")
    _propose(world, groceries, "-50.00", "hh_groceries")
    rental = world.line(date(2026, 10, 8), "-12.00", name="Händler Beispiel")
    _propose(world, rental, "-12.00", "uncategorized")
    unanswered = private.line(date(2026, 10, 9), "-5.00", name="Kiosk Beispiel")  # no rows

    assert world.cmd(reask_household) == ReaskResult(0, 0, 0, 0)
    assert _jobs(world) == []
    assert unanswered  # „KI-Vorschläge holen“ asks for a line without rows, not this


def test_a_person_confirming_before_the_job_runs_wins(world, private, app_engine, tmp_path):
    _consent_both(world)
    tx = private.line(date(2026, 10, 10), "-15.00", name="Café Beispiel")
    _propose(world, tx, "-15.00", "hh_leisure_travel")
    assert world.cmd(reask_household).queued == 1
    world.cmd(allocations.confirm_proposed, tx)
    client = FakeModelClient()  # any request fails the test
    plan = _reask(world, tx, _deps(app_engine, tmp_path, client))
    assert (plan.outcome, plan.reason) == ("skipped", "matching.already_allocated")
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_leisure_travel", "confirmed")


@pytest.mark.parametrize(
    ("name", "purpose"),
    [
        ("Notariat Dr. Beispiel", "Rechnung 2026-117"),
        ("Landesjustizkasse Beispielstadt", "Kostenrechnung"),
        ("Finanzamt Beispielstadt", "Grunderwerbsteuer StNr 000/000"),
        ("Immobilien Beispiel GmbH", "Maklerprovision Wohnung"),
        ("Bank Beispiel", "Bereitstellungszinsen Darlehen 4711"),
    ],
)
def test_a_possible_purchase_cost_of_the_flat_is_left_alone(world, private, name, purpose):
    _consent_both(world)
    tx = private.line(date(2026, 10, 11), "-412.34", name=name, purpose=purpose)
    _propose(world, tx, "-412.34", "hh_other")
    assert world.cmd(reask_household) == ReaskResult(0, 0, 1, 0)
    assert _jobs(world) == []


def test_a_line_of_a_nebenkosten_rows_amount_is_left_alone(world, private):
    """A bill paid privately whose text names nothing: its amount equals a Nebenkosten row's
    to the cent, so it may be that cost (D161's re-sort stays with a person)."""
    _consent_both(world)
    world.query(
        lambda c: c.execute(
            insert(acquisition_cost_table).values(
                workspace_id=world.ws,
                property_id=world.property_id,
                kind="notary",
                amount=Decimal("1234.56"),
            )
        )
    )
    tx = private.line(date(2026, 10, 12), "-1234.56", name="Überweisung", purpose="RE 88")
    _propose(world, tx, "-1234.56", "hh_other")
    near = private.line(date(2026, 10, 12), "-1234.60", name="Überweisung", purpose="RE 89")
    _propose(world, near, "-1234.60", "hh_other")
    assert world.cmd(reask_household) == ReaskResult(1, 0, 1, 0)


def test_nothing_is_queued_while_the_switch_is_off(world, private):
    tx = private.line(date(2026, 10, 13), "-60.00", name="Händler Beispiel")
    _propose(world, tx, "-60.00", "hh_subscriptions")
    assert world.cmd(reask_household) == ReaskResult(0, 1, 0, 0)
    assert _jobs(world) == []
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_A)
    assert world.cmd(reask_household).queued == 1


def test_the_job_never_reaches_the_model_while_the_switch_is_off(
    world, private, app_engine, tmp_path
):
    """Switched off between the run and the job: the line waits for a person as before."""
    _consent_both(world)
    tx = private.line(date(2026, 10, 14), "-8.00", name="Händler Beispiel")
    _propose(world, tx, "-8.00", "hh_housing")
    world.cmd(reask_household)
    world.cmd(workspace_settings.update_settings, ai_private_accounts=False, actor=OWNER_A)
    client = FakeModelClient()
    plan = _reask(world, tx, _deps(app_engine, tmp_path, client))
    assert plan.outcome == "skipped" and client.requests == []
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_housing", "proposed")


def test_the_command_counts_with_dry_run_and_then_queues(dbname, world, private, capsys):
    _consent_both(world)
    tx = private.line(date(2026, 10, 15), "-19.00", name="Händler Beispiel")
    _propose(world, tx, "-19.00", "uncategorized")
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["household-reask", "--workspace", str(world.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    assert "would queue 1 line(s)" in capsys.readouterr().out
    assert _jobs(world) == []

    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "queued 1 line(s)" in out and "postgresql" not in out
    assert len(_jobs(world)) == 1
    assert uuid.UUID(_jobs(world)[0].args["transaction_id"]) == tx


def test_a_retried_job_writes_the_answer_its_first_attempt_lost(
    world, private, app_engine, tmp_path, monkeypatch
):
    """Attempt 0 asks the model (its ai_call commits) and then fails to write: the retry asks
    again and writes, rather than skipping as „asked already“ and losing the answer."""
    from hausbuch.jobs import matching as jobs_matching
    from hausbuch.jobs.document_read import RetryLater

    _consent_both(world)
    tx = private.line(date(2026, 10, 16), "-24.00", name="Salon Beispiel", purpose="Karte")
    _propose(world, tx, "-24.00", "hh_other")
    world.cmd(reask_household)
    client = FakeModelClient(
        [
            text_response(_answer("hh_personal_care", "-24.00")),
            text_response(_answer("hh_personal_care", "-24.00")),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    real = jobs_matching.matching.finish_line
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("connection lost")
        return real(*args, **kwargs)

    monkeypatch.setattr(jobs_matching.matching, "finish_line", fail_once)
    with pytest.raises(RetryLater):
        run_match_transaction(deps, workspace_id=world.ws, transaction_id=tx, attempt=0, reask=True)
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_other", "proposed")

    plan = run_match_transaction(
        deps, workspace_id=world.ws, transaction_id=tx, attempt=1, reask=True
    )
    assert plan.outcome == "written"
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_personal_care", "proposed")
    assert len(client.requests) == 2


# --- A named code (D159 amended 14 Nov 2026, migration 0031: hh_fuel splits hh_mobility) ---


def test_a_mobility_line_is_asked_again_only_when_its_code_is_named(
    world, private, app_engine, tmp_path
):
    """The default run leaves hh_mobility alone; naming it re-asks the line, and the model's
    hh_fuel stays a proposal although hh_fuel's threshold would auto-confirm it."""
    _consent_both(world)
    tx = private.line(date(2026, 10, 17), "-64.20", name="Tankstelle Beispiel", purpose="Karte")
    _propose(world, tx, "-64.20", "hh_mobility")
    taxi = private.line(date(2026, 10, 18), "-18.00", name="Taxi Beispiel")
    _propose(world, taxi, "-18.00", "hh_mobility")
    confirmed = private.line(date(2026, 10, 19), "-50.00", name="Tankstelle Beispiel")
    _propose(world, confirmed, "-50.00", "hh_mobility")
    world.cmd(allocations.confirm_proposed, confirmed)

    assert world.cmd(reask_household) == ReaskResult(0, 0, 0, 0)
    mobility = frozenset({AllocationCategory.HH_MOBILITY})
    assert world.cmd(reask_household, mobility) == ReaskResult(2, 0, 0, 0)
    queued = {uuid.UUID(job.args["transaction_id"]) for job in _jobs(world)}
    assert queued == {tx, taxi}

    client = FakeModelClient([text_response(_answer("hh_fuel", "-64.20"))])
    plan = _reask(world, tx, _deps(app_engine, tmp_path, client))
    assert plan.outcome == "written"
    (row,) = _rows(world, tx)
    assert (row.category, row.method, row.status) == ("hh_fuel", "llm", "proposed")
    (row,) = _rows(world, confirmed)
    assert (row.category, row.status) == ("hh_mobility", "confirmed")


@pytest.mark.parametrize(
    "categories",
    [
        frozenset(),
        frozenset({AllocationCategory.HH_DONATION}),  # tax-relevant
        frozenset({AllocationCategory.HH_WORK_EDUCATION}),  # confirmed line by line
        frozenset({AllocationCategory.INTERNAL_TRANSFER_OUT}),
        frozenset({AllocationCategory.REPAIRS}),  # the rental account's
    ],
)
def test_only_parked_or_plain_household_codes_can_be_named(world, categories):
    with pytest.raises(ValueError, match="parked or plain household codes"):
        world.cmd(reask_household, categories)


def test_the_command_takes_a_named_code_and_refuses_another(dbname, world, private, capsys):
    _consent_both(world)
    tx = private.line(date(2026, 10, 20), "-41.00", name="Tankstelle Beispiel")
    _propose(world, tx, "-41.00", "hh_mobility")
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["household-reask", "--workspace", str(world.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    assert "would queue 0 line(s)" in capsys.readouterr().out
    assert cli.main([*argv, "--category", "hh_mobility", "--dry-run"], environ) == cli.EXIT_OK
    assert "would queue 1 line(s)" in capsys.readouterr().out
    assert _jobs(world) == []

    assert cli.main([*argv, "--category", "hh_donation"], environ) == cli.EXIT_ERROR
    assert "cannot re-ask hh_donation" in capsys.readouterr().err
    assert _jobs(world) == []

    assert cli.main([*argv, "--category", "hh_mobility"], environ) == cli.EXIT_OK
    assert "queued 1 line(s)" in capsys.readouterr().out
    assert [uuid.UUID(job.args["transaction_id"]) for job in _jobs(world)] == [tx]
