"""Phase 9 step 4b, the explanation's service, job and daily hook (docs/phases/phase-9.md §8.6,
§11, D252–D257), over invented data (World: owners A and B, a flat with its rental account).

The pure rules (facts, validator, render, template) are held by tests/rules; these tests hold
what the service and the job promise around them: no request while ai_private_accounts is
off, no label in a request, one row per run with codes only for a rejected text, an event
without amounts or labels, an append-only table, the GET rendered from the stored facts with
`stale`, the advice mode's own rows, and one daily job per run that stored a snapshot.
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from db.matching_support import World
from db.test_services_health import _account
from db.test_services_health_snapshots import NOW, TODAY, _closed_history
from db.test_services_net_worth import _asset, _held, _price
from db.test_services_private_matching import OWNER_A, OWNER_B, _deps
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from hausbuch.ai.client import ModelCallError
from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import plan_explanation as explanation_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.enqueue import EXPLAIN_PLAN_TASK
from hausbuch.jobs.explain_plan import run_explain_plan
from hausbuch.jobs.health_daily import run_health_daily
from hausbuch.rules.advice import template
from hausbuch.rules.advice.validator import validate
from hausbuch.schemas.advice import AdviceMode, ExplanationReason, ExplanationStatus
from hausbuch.schemas.enums import AccountKind
from hausbuch.services import advice, net_worth, workspace_settings

# Distinctive invented labels: none of them may ever reach a request, a row's violations or
# an event.
ACCOUNT_LABEL = "Quokkakonto Zebrastreifen"
EXCHANGE_LABEL = "Wombatbörse Quasar"
ASSET_NAME = "Pangolinmünze Xylophon"
LABELS = (ACCOUNT_LABEL, EXCHANGE_LABEL, ASSET_NAME)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture(autouse=True)
def _no_jobs_left(app_engine):
    yield
    with app_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM procrastinate_jobs WHERE task_name = :t"), {"t": EXPLAIN_PLAN_TASK}
        )


def _household(world: World) -> None:
    """Twelve and more closed months, the rental account renamed to a distinctive label, and
    an exchange holding a coin with a distinctive name."""
    _closed_history(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(account_table)
            .where(account_table.c.kind == "bank_current")
            .values(label=ACCOUNT_LABEL)
        )
    exchange = _account(world, EXCHANGE_LABEL, AccountKind.EXCHANGE)

    def held(ctx):
        coin = _asset(ctx, kind="crypto", symbol="PXY", name=ASSET_NAME)
        _held(ctx, exchange, coin, "2")
        _price(ctx, coin, date(2027, 4, 5), "100.00", "user")

    world.cmd(held)


def _consent(world: World) -> None:
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_A)
    world.cmd(workspace_settings.record_ai_consent, actor=OWNER_B)


def _facts(world: World):
    return world.query(advice.explanation_facts, TODAY, None)


def _answer(paragraphs) -> str:
    return json.dumps({"paragraphs": [p.model_dump(mode="json") for p in paragraphs]})


def _honest(world: World) -> str:
    """The template's own slotted text as the model's answer: it passes the validator."""
    facts = _facts(world)
    paragraphs = template.render(facts, facts.mode)
    assert validate(paragraphs, facts, facts.mode).ok
    return _answer(paragraphs)


REJECTED = json.dumps({"paragraphs": [{"topic": "surplus", "text": "Ihr habt 500 Euro frei."}]})


def _run(app_engine, tmp_path, world: World, client):
    return run_explain_plan(
        replace(_deps(app_engine, tmp_path, client), clock=lambda: NOW),
        workspace_id=world.ws,
        scenario_id=None,
    )


def _rows(world: World):
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(explanation_table).order_by(explanation_table.c.created_at)
        ).all()


def _events(world: World):
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            select(event_table.c.payload).where(event_table.c.type == "plan.explanation_stored")
        ).all()


def _queued(app_engine) -> int:
    with app_engine.begin() as conn:
        return conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = :t"),
            {"t": EXPLAIN_PLAN_TASK},
        ).scalar_one()


def _request_text(request) -> str:
    return json.dumps(request.body(), ensure_ascii=False)


# --- Consent (D253) ---------------------------------------------------------------------


def test_with_consent_off_nothing_is_queued_and_the_model_is_never_called(
    world, app_engine, tmp_path
):
    _household(world)
    queued = world.cmd(advice.request, None)
    assert queued.queued is False and _queued(app_engine) == 0
    shown = world.query(advice.explanation, TODAY, None)
    assert (shown.id, shown.status, shown.reason) == (
        None,
        ExplanationStatus.TEMPLATE,
        ExplanationReason.CONSENT_OFF,
    )
    assert shown.paragraphs and not shown.stale
    # A job queued before consent was switched off: the template, no request built.
    client = FakeModelClient([])
    result = _run(app_engine, tmp_path, world, client)
    assert client.requests == []
    assert (result.status, result.reason, result.ai_call_ids) == (
        ExplanationStatus.TEMPLATE,
        ExplanationReason.CONSENT_OFF,
        [],
    )
    (row,) = _rows(world)
    assert (row.status, row.reason, row.prompt_version, row.ai_call_ids) == (
        "template",
        "consent_off",
        None,
        [],
    )


def test_with_consent_on_the_request_is_queued_once(world, app_engine):
    _household(world)
    _consent(world)
    queued = world.cmd(advice.request, None, actor=OWNER_A)
    assert queued.queued is True and _queued(app_engine) == 1
    with app_engine.begin() as conn:
        args = conn.execute(
            text("SELECT args FROM procrastinate_jobs WHERE task_name = :t"),
            {"t": EXPLAIN_PLAN_TASK},
        ).scalar_one()
    assert set(args) == {"workspace_id", "scenario_id", "requested_by"}  # ids only


# --- The request carries no label (D253) ------------------------------------------------


def test_a_passing_answer_is_stored_and_no_label_reaches_the_request(world, app_engine, tmp_path):
    _household(world)
    _consent(world)
    facts = _facts(world)
    assert facts.labels.accounts and facts.labels.assets  # the labels exist beside the facts
    assert ASSET_NAME in facts.labels.assets.values()
    client = FakeModelClient([text_response(_honest(world))])
    result = _run(app_engine, tmp_path, world, client)
    (request,) = client.requests
    body = _request_text(request)
    for label in LABELS:
        assert label not in body
    assert request.job.value == "explain" and "haiku" in request.model  # one tier, no second
    assert result.status is ExplanationStatus.MODEL and result.reason is None
    (row,) = _rows(world)
    assert row.status == "model" and row.reason is None and row.violations == []
    assert row.ai_call_ids == result.ai_call_ids and len(row.ai_call_ids) == 1
    assert row.prompt_version == request.prompt_version
    assert ASSET_NAME in json.dumps(row.labels, ensure_ascii=False)  # stored beside, for render
    assert ASSET_NAME not in json.dumps(row.facts, ensure_ascii=False)
    shown = world.query(advice.explanation, TODAY, None)
    assert shown.id == row.id and shown.status is ExplanationStatus.MODEL
    assert not shown.stale
    assert all("{" not in p.text for p in shown.paragraphs)  # every slot filled


# --- Rejected twice ---------------------------------------------------------------------


def test_rejected_then_rejected_stores_the_template_with_codes_only(world, app_engine, tmp_path):
    _household(world)
    _consent(world)
    client = FakeModelClient([text_response(REJECTED), text_response(REJECTED)])
    result = _run(app_engine, tmp_path, world, client)
    assert (result.status, result.reason) == (
        ExplanationStatus.TEMPLATE,
        ExplanationReason.REJECTED,
    )
    first, second = client.requests
    retry = _request_text(second)
    assert "500 Euro" not in retry  # the retry names the codes, never the rejected text
    assert "previous answer was rejected" in retry and "previous" not in _request_text(first)
    (row,) = _rows(world)
    assert (row.status, row.reason) == ("template", "rejected")
    assert len(row.ai_call_ids) == 2
    assert row.violations and {v["attempt"] for v in row.violations} == {1, 2}
    for v in row.violations:
        assert set(v) == {"attempt", "code", "paragraph", "start", "end"}
        assert isinstance(v["code"], str) and v["code"] in retry
    assert "Euro" not in json.dumps(row.violations)
    # the paragraphs stored are the template's, slotted
    facts = _facts(world)
    assert row.paragraphs == [p.model_dump(mode="json") for p in template.render(facts, facts.mode)]
    (payload,) = _events(world)
    assert set(payload.payload) == {"status", "reason", "mode", "violation_codes"}
    assert payload.payload["status"] == "template" and payload.payload["reason"] == "rejected"
    assert payload.payload["violation_codes"]
    with workspace_transaction(world.engine, world.ws) as conn:
        calls = conn.execute(
            select(ai_call_table.c.job).where(ai_call_table.c.id.in_(row.ai_call_ids))
        ).scalars()
        assert set(calls) == {"explain"}


def test_a_rejection_then_a_pass_is_stored_as_the_model_text(world, app_engine, tmp_path):
    _household(world)
    _consent(world)
    client = FakeModelClient([text_response(REJECTED), text_response(_honest(world))])
    result = _run(app_engine, tmp_path, world, client)
    assert result.status is ExplanationStatus.MODEL
    (row,) = _rows(world)
    assert {v["attempt"] for v in row.violations} == {1}  # the first attempt's codes kept


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ([text_response("{}", stop_reason="refusal")], ExplanationReason.REFUSED),
        ([text_response('{"paragraphs": [', stop_reason="max_tokens")], ExplanationReason.REFUSED),
        ([ModelCallError(retryable=False, status=402)], ExplanationReason.LIMIT),
        ([ModelCallError(retryable=True, status=529)], ExplanationReason.ERROR),
        ([text_response("not json")], ExplanationReason.ERROR),
    ],
)
def test_a_refusal_a_cut_off_the_limit_or_an_error_ends_in_the_template(
    world, app_engine, tmp_path, script, reason
):
    _household(world)
    _consent(world)
    client = FakeModelClient(script)
    result = _run(app_engine, tmp_path, world, client)
    assert len(client.requests) == 1  # no retry
    assert (result.status, result.reason) == (ExplanationStatus.TEMPLATE, reason)
    (row,) = _rows(world)
    assert row.reason == reason.value and len(row.ai_call_ids) == 1


# --- The event --------------------------------------------------------------------------


def test_the_event_holds_no_amount_and_no_label(world, app_engine, tmp_path):
    _household(world)
    _consent(world)
    _run(app_engine, tmp_path, world, FakeModelClient([text_response(_honest(world))]))
    (payload,) = _events(world)
    dumped = json.dumps(payload.payload, ensure_ascii=False)
    assert set(payload.payload) == {"status", "reason", "mode", "violation_codes"}
    for label in LABELS:
        assert label not in dumped
    assert not any(ch.isdigit() for ch in dumped)  # no amount, no figure


# --- Append-only ------------------------------------------------------------------------


def test_plan_explanation_is_append_only(world, app_engine, tmp_path):
    _household(world)
    _run(app_engine, tmp_path, world, FakeModelClient([]))
    with pytest.raises(DBAPIError), workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(explanation_table).values(status="model", reason=None))
    with pytest.raises(DBAPIError), workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(delete(explanation_table))
    assert len(_rows(world)) == 1


# --- GET: the stored facts, stale and the advice mode -----------------------------------


def test_get_renders_from_the_stored_facts_and_sets_stale(world, app_engine, tmp_path):
    _household(world)
    _consent(world)
    _run(app_engine, tmp_path, world, FakeModelClient([text_response(_honest(world))]))
    first = world.query(advice.explanation, TODAY, None)
    assert not first.stale
    # Another day with the same figures is not stale: as_of is not what is compared.
    later = world.query(advice.explanation, TODAY + timedelta(days=1), None)
    assert later.id == first.id and not later.stale
    # A label renamed: rendered from the stored labels, still not stale (labels are no fact).
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(account_table)
            .where(account_table.c.label == EXCHANGE_LABEL)
            .values(label="Umbenannt")
        )
    renamed = world.query(advice.explanation, TODAY, None)
    assert renamed.paragraphs == first.paragraphs and not renamed.stale
    # A figure changed (a second coin bought: a new asset is held): stale, same text.
    exchange = world.query(
        lambda conn: conn.execute(
            select(account_table.c.id).where(account_table.c.label == "Umbenannt")
        ).scalar_one()
    )

    def more(ctx):
        coin = _asset(ctx, kind="crypto", symbol="ZZZ", name="Zweite Münze")
        _held(ctx, exchange, coin, "1")
        _price(ctx, coin, date(2027, 4, 5), "10.00", "user")

    world.cmd(more)
    changed = world.query(advice.explanation, TODAY, None)
    assert changed.id == first.id and changed.stale
    assert changed.paragraphs == first.paragraphs


def test_a_row_of_the_other_mode_is_not_shown(world, app_engine, tmp_path):
    _household(world)
    _consent(world)
    _run(app_engine, tmp_path, world, FakeModelClient([text_response(_honest(world))]))
    assert world.query(advice.explanation, TODAY, None).id is not None
    world.cmd(workspace_settings.update_settings, advice_mode=AdviceMode.GENERIC, actor=OWNER_A)
    shown = world.query(advice.explanation, TODAY, None)
    assert shown.id is None and shown.mode is AdviceMode.GENERIC
    assert (shown.status, shown.reason) == (ExplanationStatus.TEMPLATE, None)
    world.cmd(workspace_settings.update_settings, advice_mode=AdviceMode.OWN_USE, actor=OWNER_A)
    assert world.query(advice.explanation, TODAY, None).id is not None


def test_an_unknown_scenario_is_not_found(world):
    import uuid

    from hausbuch.services.errors import NotFoundError

    with pytest.raises(NotFoundError):
        world.query(advice.explanation, TODAY, uuid.uuid4())


# --- health_daily (§11.5) ---------------------------------------------------------------


def _daily_deps(world: World) -> JobDeps:
    return JobDeps(
        engine=world.engine,
        settings=None,
        blob_store=None,
        model_client=None,
        clock=lambda: datetime(2027, 4, 11, 8, 30, tzinfo=UTC),
    )


def test_health_daily_queues_one_explanation_once(world, app_engine):
    _household(world)
    _consent(world)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    first = run_health_daily(_daily_deps(world), workspace_ids=[world.ws])
    assert first.stored > 0 and first.explanations_queued == 1
    again = run_health_daily(_daily_deps(world), workspace_ids=[world.ws])
    assert (again.stored, again.explanations_queued) == (0, 0)
    assert _queued(app_engine) == 1


def test_health_daily_queues_nothing_without_consent(world, app_engine):
    _household(world)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    first = run_health_daily(_daily_deps(world), workspace_ids=[world.ws])
    assert first.stored > 0 and first.explanations_queued == 0
    assert _queued(app_engine) == 0
