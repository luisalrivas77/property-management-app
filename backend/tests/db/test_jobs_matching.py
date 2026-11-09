"""The matching jobs with a scripted model (phase-2.md §5.1, §5.3, §8.1, D17).

FakeModelClient only: no network, no key. The prompt checks read the request body the
client would have sent."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import (
    LOAN_IBAN,
    OTHER_TENANT_IBAN,
    OWNER_IBAN,
    PAINTER_IBAN,
    STADT_IBAN,
    TENANT_IBAN,
    WEG_IBAN,
    World,
)
from sqlalchemy import insert, select, text, update
from test_bank_parsers import CAMT, CSV, build

from hausbuch.ai.client import ModelCallError
from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.ai.provenance import record_ai_call
from hausbuch.ai.requests import categorize_request, explain_request, extract_request
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs.document_read import JobDeps, RetryLater
from hausbuch.jobs.enqueue import import_lock, transaction_lock, waiting_lock
from hausbuch.jobs.matching import (
    run_match_import,
    run_match_transaction,
    run_rematch_on_evidence,
    run_rematch_waiting,
)
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import ConfirmAllocationsBody, InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import DuplicateStatus, ExtractionKind, ImportStatus
from hausbuch.schemas.extraction import EXTRACTION_MODELS
from hausbuch.schemas.property import UnitCreate
from hausbuch.services import documents, loans, matching, review, rules_learned, tenancies
from hausbuch.services.accounts import create_account
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.statements import (
    import_statement,
    list_possible_duplicates,
    resolve_duplicate,
)
from hausbuch.services.tenants import update_tenant
from hausbuch.services.units import create_unit
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
UNMATCHED_TEXT = "Nicht zugeordnet: keine Regel, kein Abgleich und kein Beleg passen."
AI_FAILED_TEXT = "Die KI konnte diese Zahlung nicht einordnen (Anfrage fehlgeschlagen)."


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
    return World(app_engine, fresh_workspaces[0])


def _deps(app_engine, tmp_path, client, **overrides) -> JobDeps:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": TEST_IBAN_KEY,
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": tmp_path / "blobs",
        "ai_enabled": True,
        "anthropic_api_key": "sk-ant-test-not-a-key",
    } | overrides
    return JobDeps(
        engine=app_engine,
        settings=Settings(**values),
        blob_store=None,  # type: ignore[arg-type]  # matching reads no original
        model_client=client,
        clock=lambda: NOW,
    )


def _answer(*parts, confidence="high", ambiguous=False, question=None, model="fake-sonnet"):
    body = {
        "allocations": [
            {"category": c, "amount": a, "service_period": p, "recurring": r}
            for c, a, p, r in parts
        ],
        "confidence": confidence,
        "reasoning": "Versicherungsbeitrag laut Verwendungszweck.",
        "question": question,
        "ambiguous": ambiguous,
    }
    return text_response(json.dumps(body), model=model)


def _run(deps, world: World, tx, attempt=0, **kw):
    return run_match_transaction(
        deps, workspace_id=world.ws, transaction_id=tx, attempt=attempt, **kw
    )


def _rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table)
            .where(
                allocation_table.c.transaction_id == tx,
                allocation_table.c.status != "superseded",
            )
            .order_by(allocation_table.c.recorded_at)
        ).all()
    )


def _calls(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(ai_call_table)
            .where(ai_call_table.c.transaction_id == tx)
            .order_by(ai_call_table.c.created_at, ai_call_table.c.id)
        ).all()
    )


def _events(world: World, tx):
    return world.query(
        lambda c: c.execute(select(event_table).where(event_table.c.entity_id == tx)).all()
    )


def _jobs(world: World):
    return world.query(
        lambda c: c.execute(
            text("SELECT task_name, args, lock FROM procrastinate_jobs ORDER BY id")
        ).all()
    )


def _sent(client: FakeModelClient) -> str:
    return json.dumps([r.body() for r in client.requests], ensure_ascii=False)


# --- The switch and the threshold ----------------------------------------------------------


def test_with_ai_off_nothing_is_sent_and_the_line_stays_unmatched(world, app_engine, tmp_path):
    client = FakeModelClient()
    deps = _deps(app_engine, tmp_path, client, ai_enabled=False)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz", purpose="Beitrag")
    plan = _run(deps, world, tx)
    assert (plan.outcome, plan.reason) == ("skipped", "routing.no_match")
    assert client.requests == [] and _rows(world, tx) == [] and _calls(world, tx) == []
    (item,) = world.query(review.inbox)
    assert item.kind is InboxItemKind.UNMATCHED
    assert item.reasoning == UNMATCHED_TEXT  # no call was made, so none failed


def test_an_llm_set_is_only_proposed_while_auto_accept_llm_is_off(world, app_engine, tmp_path):
    client = FakeModelClient([_answer(("insurance", "-312.40", None, True))])
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz", purpose="Beitrag")
    _run(deps, world, tx)
    (row,) = _rows(world, tx)
    (call,) = _calls(world, tx)
    assert (row.category, row.method, row.status, row.confidence) == (
        "insurance",
        "llm",
        "proposed",
        Decimal("0.900"),
    )
    assert row.ai_call_id == call.id and row.property_id == world.property_id
    assert (call.job, call.document_id, call.error_code) == ("match", None, None)
    assert client.requests[0].model == deps.settings.ai_model_extract
    assert client.requests[0].output_schema is not None
    assert _events(world, tx) == []


def test_an_llm_set_auto_confirms_only_with_the_setting_and_above_the_threshold(
    world, app_engine, tmp_path
):
    world.set_auto_accept_llm(True)
    client = FakeModelClient(
        [
            _answer(("insurance", "-312.40", None, True)),
            _answer(("repairs", "-450.00", None, False)),  # never auto-confirms
            _answer(("insurance", "-99.00", None, True), confidence="medium"),  # 0.6 < 0.9
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    sure = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    repair = world.line(date(2026, 3, 4), "-450.00", name="Maler Schulz")
    unsure = world.line(date(2026, 3, 5), "-99.00", name="Versicherung?")
    for tx in (sure, repair, unsure):
        _run(deps, world, tx)
    assert [r.status for r in _rows(world, sure)] == ["confirmed"]
    assert [r.status for r in _rows(world, repair)] == ["proposed"]
    assert [r.status for r in _rows(world, unsure)] == ["proposed"]
    (event,) = _events(world, sure)
    assert (event.type, event.actor_type, event.actor_id) == (
        "transaction.allocated",
        "ai",
        "fake-sonnet",
    )
    assert event.payload["auto"] is True and event.payload["method"] == "llm"
    assert "tax_year" not in json.dumps(event.payload)


def test_an_llm_set_naming_a_matcher_category_or_splitting_is_never_auto_confirmed(
    world, app_engine, tmp_path
):
    """Review finding 3: with auto_accept_llm on and a high confidence, a model's set still
    goes to review when it names rent, the annuity's parts, Hausgeld or an owner transfer
    (no matcher recognised the line), or when it splits the amount (principle 1)."""
    world.set_auto_accept_llm(True)
    client = FakeModelClient(
        [
            _answer(("rent_cold", "1280.00", None, True)),
            _answer(
                ("loan_interest", "-900.00", None, True), ("loan_principal", "-500.00", None, True)
            ),
            _answer(("insurance", "-200.00", None, True), ("bank_fees", "-12.40", None, True)),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    rent = world.line(date(2026, 3, 2), "1280.00", name="Unbekannt", purpose="Miete")
    annuity = world.line(date(2026, 3, 3), "-1400.00", name="Irgendeine Bank", purpose="Rate")
    split = world.line(date(2026, 3, 4), "-212.40", name="Allianz", purpose="Beitrag")
    for tx in (rent, annuity, split):
        _run(deps, world, tx)
    for tx, parts, question in [
        (rent, 1, matching.LLM_GUARDED_QUESTION),
        (annuity, 2, matching.LLM_GUARDED_QUESTION),
        (split, 2, matching.LLM_SPLIT_QUESTION),
    ]:
        rows = _rows(world, tx)
        assert len(rows) == parts and {r.status for r in rows} == {"proposed"}
        assert {r.question for r in rows} == {question}
        assert {r.method for r in rows} == {"llm"}
        assert _events(world, tx) == []  # proposed rows are review items, no event


def test_a_model_answer_for_a_zero_line_books_itself_without_the_setting(
    world, app_engine, tmp_path
):
    """D52: the account's 0.00 fee line, read as bank_fees, is confirmed with auto_accept_llm
    off and at any confidence; a 0.00 rent answer still goes to review."""
    client = FakeModelClient(
        [
            _answer(("bank_fees", "0.00", None, True), confidence="medium"),
            _answer(("rent_cold", "0.00", None, True)),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    fee = world.line(date(2026, 3, 31), "0.00", name="Sparkasse", purpose="Kontoführung")
    rent = world.line(date(2026, 3, 31), "0.00", name="Unbekannt", purpose="Miete")
    for tx in (fee, rent):
        _run(deps, world, tx)
    (row,) = _rows(world, fee)
    assert (row.category, row.amount, row.method, row.status) == (
        "bank_fees",
        Decimal("0.00"),
        "llm",
        "confirmed",
    )
    (event,) = _events(world, fee)
    assert (event.type, event.actor_type, event.actor_id) == (
        "transaction.allocated",
        "ai",
        "fake-sonnet",
    )
    assert event.payload["auto"] is True and event.payload["method"] == "llm"
    (row,) = _rows(world, rent)
    assert (row.category, row.method, row.status) == ("rent_cold", "llm", "proposed")
    assert _events(world, rent) == []


# --- The one loan on a model suggestion (D51) -------------------------------------------------


def _contract(world: World, *, loan_ids=(), subtype: str = "loan_contract"):
    """A Darlehensvertrag linked to the given loans (and the property), with no reading."""
    with workspace_transaction(world.engine, world.ws) as conn:
        doc = world._document_row(conn, type="contract", subtype=subtype, title=None)
        for link in [{"property_id": world.property_id}] + [{"loan_id": i} for i in loan_ids]:
            conn.execute(
                insert(link_table).values(
                    workspace_id=world.ws, document_id=doc, created_at=world.now, **link
                )
            )
    return doc


def _payout_and_fee(world: World, app_engine, tmp_path):
    """The model names a loan payout and a loan fee; returns their rows."""
    client = FakeModelClient(
        [
            _answer(("loan_disbursement", "300000.00", None, False)),
            _answer(("loan_fees", "-250.00", None, False)),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    payout = world.line(date(2024, 4, 2), "300000.00", purpose="Darlehensauszahlung")
    fee = world.line(date(2024, 4, 3), "-250.00", name="Notar", purpose="Grundschuld")
    for tx in (payout, fee):
        _run(deps, world, tx)
    ((p,), (f,)) = (_rows(world, payout), _rows(world, fee))
    assert (p.method, p.category, f.method, f.category) == (
        "llm",
        "loan_disbursement",
        "llm",
        "loan_fees",
    )
    return p, f


def test_a_model_loan_suggestion_carries_the_one_loan_and_its_contract(world, app_engine, tmp_path):
    """Luis (8 Nov 2026): the payout was suggested without the Darlehen and its contract."""
    loan = world.loan()
    contract = _contract(world, loan_ids=[loan])
    _contract(world, subtype="purchase_contract", loan_ids=[loan])  # not a Darlehensvertrag
    _contract(world)  # a Darlehensvertrag of no loan
    payout, fee = _payout_and_fee(world, app_engine, tmp_path)
    assert (payout.property_id, payout.loan_id, payout.document_id) == (
        world.property_id,
        loan,
        contract,
    )
    assert (fee.loan_id, fee.document_id) == (loan, None)  # the Beleg only on the payout


def test_a_loan_with_two_contracts_gets_no_beleg(world, app_engine, tmp_path):
    loan = world.loan()
    _contract(world, loan_ids=[loan])
    _contract(world, loan_ids=[loan])
    payout, _ = _payout_and_fee(world, app_engine, tmp_path)
    assert (payout.loan_id, payout.document_id) == (loan, None)


def test_with_two_loans_the_model_suggestion_names_none(world, app_engine, tmp_path):
    first = world.loan()
    second = world.loan(iban=None, lender="Zweite Bank eG")
    _contract(world, loan_ids=[first])
    _contract(world, loan_ids=[second])
    payout, fee = _payout_and_fee(world, app_engine, tmp_path)
    for row in (payout, fee):
        assert (row.property_id, row.loan_id, row.document_id) == (world.property_id, None, None)


def test_only_a_loan_category_gets_the_loan(world, app_engine, tmp_path):
    world.loan()
    client = FakeModelClient([_answer(("insurance", "-312.40", None, True))])
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz", purpose="Beitrag")
    _run(_deps(app_engine, tmp_path, client), world, tx)
    (row,) = _rows(world, tx)
    assert (row.category, row.loan_id, row.document_id) == ("insurance", None, None)


# --- Validation and retries ------------------------------------------------------------------


def test_a_set_that_does_not_sum_is_asked_once_more(world, app_engine, tmp_path):
    client = FakeModelClient(
        [
            _answer(("insurance", "-300.00", None, True)),
            _answer(("insurance", "-300.00", None, True), ("bank_fees", "-12.40", None, False)),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    _run(deps, world, tx)
    first, second = _calls(world, tx)
    assert first.error_code == "allocation.sum_mismatch" and second.error_code is None
    rows = _rows(world, tx)
    assert sum(r.amount for r in rows) == Decimal("-312.40")
    assert {r.ai_call_id for r in rows} == {second.id}


def test_two_unusable_answers_leave_the_line_unmatched(world, app_engine, tmp_path):
    client = FakeModelClient([text_response("kein JSON"), text_response('{"allocations": []}')])
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    plan = _run(deps, world, tx)
    assert (plan.outcome, plan.reason) == ("skipped", "ai.unparsable")
    assert [c.error_code for c in _calls(world, tx)] == ["ai.unparsable", "ai.unparsable"]
    assert _rows(world, tx) == []
    assert [i.kind for i in world.query(review.inbox)] == [InboxItemKind.UNMATCHED]
    # D50: an answer unusable twice counts as a failed call too.
    assert world.query(review.inbox)[0].reasoning == AI_FAILED_TEXT


def test_a_refusal_is_asked_once_more(world, app_engine, tmp_path):
    client = FakeModelClient(
        [
            text_response("", stop_reason="refusal"),
            _answer(("insurance", "-312.40", None, True)),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    _run(deps, world, tx)
    assert [c.error_code for c in _calls(world, tx)] == ["ai.refused", None]
    assert len(_rows(world, tx)) == 1


def test_an_ambiguous_answer_is_asked_of_the_hard_model(world, app_engine, tmp_path):
    client = FakeModelClient(
        [
            _answer(("other_expense", "-312.40", None, False), confidence="low", ambiguous=True),
            _answer(("insurance", "-312.40", None, True), model="fake-opus"),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    _run(deps, world, tx)
    assert [r.model for r in client.requests] == [
        deps.settings.ai_model_extract,
        deps.settings.ai_model_hard,
    ]
    (row,) = _rows(world, tx)
    assert row.category == "insurance" and row.ai_call_id == _calls(world, tx)[1].id


def test_an_unusable_hard_answer_keeps_the_first(world, app_engine, tmp_path):
    client = FakeModelClient(
        [
            _answer(("insurance", "-312.40", None, True), confidence="medium", ambiguous=True),
            text_response("x"),
            text_response("y"),
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    _run(deps, world, tx)
    (row,) = _rows(world, tx)
    assert row.ai_call_id == _calls(world, tx)[0].id and row.confidence == Decimal("0.600")


def test_an_unavailable_api_is_retried_then_the_line_stays_unmatched(world, app_engine, tmp_path):
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    busy = ModelCallError(retryable=True, status=529)
    deps = _deps(app_engine, tmp_path, FakeModelClient([busy]))
    with pytest.raises(RetryLater) as retry:
        _run(deps, world, tx, attempt=0)
    assert retry.value.wait_s == 10
    deps = _deps(app_engine, tmp_path, FakeModelClient([busy]))
    plan = _run(deps, world, tx, attempt=3)
    assert plan.outcome == "skipped" and _rows(world, tx) == []
    assert [c.error_code for c in _calls(world, tx)] == ["ai.unavailable", "ai.unavailable"]


def test_a_4xx_is_not_retried(world, app_engine, tmp_path):
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    client = FakeModelClient([ModelCallError(retryable=False, status=400)])
    plan = _run(_deps(app_engine, tmp_path, client), world, tx)
    assert (plan.outcome, plan.reason) == ("skipped", "ai.api_error")
    # D50: the failure is visible in the queue, not the plain no-match text.
    (item,) = world.query(review.inbox)
    assert (item.kind, item.reasoning) == (InboxItemKind.UNMATCHED, AI_FAILED_TEXT)


# --- Explain ------------------------------------------------------------------------------


def test_explain_words_a_matcher_set_bound_for_review(world, app_engine, tmp_path):
    world.tenancy()
    question = "Es fehlen 20,00 € an der Miete für Januar. Mietminderung oder Teilzahlung?"
    client = FakeModelClient(
        [
            text_response(
                json.dumps(
                    {"reasoning": "Miete vom Mieterkonto, 20 € zu wenig.", "question": question}
                )
            )
        ]
    )
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2025, 1, 3), "1260.00", iban=TENANT_IBAN, purpose="Miete Erika")
    _run(deps, world, tx)
    rows = _rows(world, tx)
    assert {r.status for r in rows} == {"proposed"} and {r.method for r in rows} == {"matcher_rent"}
    assert rows[0].question == question
    (request,) = client.requests
    assert request.model == deps.settings.ai_model_classify and request.job.value == "explain"
    sent = _sent(client)
    assert "Erika" not in sent and "Mustermieterin" not in sent and TENANT_IBAN not in sent


def test_explain_failure_keeps_the_template_text(world, app_engine, tmp_path):
    world.tenancy()
    client = FakeModelClient([ModelCallError(retryable=True, status=529)])
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(date(2025, 1, 3), "1260.00", iban=TENANT_IBAN, purpose="Miete")
    _run(deps, world, tx)  # no retry: the template is the fallback
    rows = _rows(world, tx)
    assert {r.status for r in rows} == {"proposed"} and "statt" in rows[0].question


# --- Data minimization -----------------------------------------------------------------------


def test_the_prompt_carries_no_tenant_name_and_no_plaintext_iban(world, app_engine, tmp_path):
    world.tenancy(name="Erika Mustermieterin")
    second = world.cmd(
        create_unit,
        UnitCreate(property_id=world.property_id, label="WE 4", floor_area_m2=Decimal("50.00")),
    ).id
    world.tenancy(name="Otto Zweitmieter", iban=OTHER_TENANT_IBAN, unit_id=second)
    client = FakeModelClient([_answer(("deposit_in", "3000.00", None, False))])
    deps = _deps(app_engine, tmp_path, client)
    tx = world.line(
        date(2025, 1, 2),
        "3000.00",
        iban=TENANT_IBAN,
        name="Erika Mustermieterin",
        purpose=f"Kaution fuer Mustermieterin, Rueckzahlung an {PAINTER_IBAN} wie Otto",
    )
    _run(deps, world, tx)
    sent = _sent(client)
    for secret in ("Erika", "Mustermieterin", "Otto", "Zweitmieter"):
        assert secret not in sent
    for iban in (TENANT_IBAN, OTHER_TENANT_IBAN, PAINTER_IBAN):
        assert iban not in sent and iban[4:12] not in sent
    assert "Mieter (Mietverhältnis WE 3)" in sent and "[IBAN]" in sent


# --- match_import, the hooks and rematch_on_evidence ------------------------------------


def test_match_import_refuses_an_unreconciled_import_and_fans_out_a_reconciled_one(
    world, app_engine, tmp_path
):
    deps = _deps(app_engine, tmp_path, FakeModelClient(), ai_enabled=False)
    lines = [world.line(date(2026, 3, d), "-10.00", name="X") for d in (3, 4)]
    world.set_import_status(ImportStatus.UNRECONCILED)
    assert run_match_import(deps, workspace_id=world.ws, import_id=world.import_id, attempt=0) == 0
    assert _jobs(world) == []
    world.set_import_status(ImportStatus.RECONCILED)
    assert run_match_import(deps, workspace_id=world.ws, import_id=world.import_id, attempt=0) == 2
    jobs = _jobs(world)
    assert [(j.task_name, j.lock) for j in jobs] == [
        ("match_transaction", transaction_lock(tx)) for tx in lines
    ]


def _account(world: World):
    data = AccountCreate(
        label="Mietkonto",
        bank="Sparkasse",
        iban="DE50100000000000000001",
        opening_balance=Decimal("0.00"),
        opening_balance_on=date(2026, 3, 31),
    )
    return world.cmd(create_account, data, iban_key=TEST_IBAN_KEY)


def _import(world: World, blob_store, account_id, data, media_type):
    return world.cmd(
        import_statement,
        blob_store,
        account_id,
        data,
        media_type=media_type,
        iban_key=TEST_IBAN_KEY,
        original_filename="export",
    )


def _case_bytes(fixture, case_id):
    return build(next(c for c in fixture.cases if c.id == case_id).input)


def test_a_reconciled_statement_is_matched_end_to_end(
    app_engine, fresh_workspaces, blob_store, tmp_path
):
    world = World(app_engine, fresh_workspaces[1])
    account = _account(world)
    zipped = _case_bytes(CAMT, "sparkasse_zip_of_daily_reports")
    imported = _import(world, blob_store, account.id, zipped, "application/zip")
    assert imported.status is ImportStatus.RECONCILED
    assert [(j.task_name, j.lock) for j in _jobs(world)] == [
        ("match_import", import_lock(imported.id))
    ]
    deps = _deps(app_engine, tmp_path, FakeModelClient(), ai_enabled=False)
    count = run_match_import(deps, workspace_id=world.ws, import_id=imported.id, attempt=0)
    assert count == imported.lines_total
    queued = [j for j in _jobs(world) if j.task_name == "match_transaction"]
    for job in queued:
        _run(deps, world, job.args["transaction_id"])
    kinds = [i.kind for i in world.query(review.inbox)]
    assert kinds == [InboxItemKind.UNMATCHED] * imported.lines_total


def test_kept_both_queues_the_later_line(app_engine, fresh_workspaces, blob_store):
    world = World(app_engine, fresh_workspaces[1])
    account = _account(world)
    _import(world, blob_store, account.id, _case_bytes(CSV, "sparkasse_export"), "text/csv")
    _import(
        world,
        blob_store,
        account.id,
        _case_bytes(CAMT, "sparkasse_zip_of_daily_reports"),
        "application/zip",
    )
    pair = world.query(list_possible_duplicates)[0]
    before = len(_jobs(world))
    world.cmd(resolve_duplicate, pair.id, DuplicateStatus.KEPT_BOTH)
    jobs = _jobs(world)
    assert len(jobs) == before + 1
    assert (jobs[-1].task_name, jobs[-1].lock) == (
        "match_transaction",
        transaction_lock(pair.other_transaction_id),
    )


def test_a_new_reading_queues_a_rematch_that_matches_without_the_model(world, app_engine, tmp_path):
    tx = world.line(date(2026, 3, 12), "-1234.56", iban=PAINTER_IBAN)
    fields = {
        "title": "Malerrechnung",
        "document_date": "2026-03-10",
        "subject": "Malerarbeiten",
        "amount_total": "1234.56",
        "iban": PAINTER_IBAN,
    }
    doc = world.document("invoice", {"title": "Malerrechnung"})  # nothing to match on yet
    deps = _deps(app_engine, tmp_path, FakeModelClient())  # AI on, nothing scripted
    with workspace_transaction(app_engine, world.ws) as conn:
        request = extract_request(
            deps.settings, ExtractionKind.INVOICE, {"type": "text", "text": "x"}
        )
        call = record_ai_call(
            conn,
            workspace_id=world.ws,
            request=request,
            document_id=doc,
            response=text_response("x"),
        )
    model = EXTRACTION_MODELS[ExtractionKind.INVOICE]
    reading = model.model_validate(
        {k: {"value": v, "confidence": "high", "checks": ["validates"]} for k, v in fields.items()}
    )
    world.cmd(documents.record_ai_extraction, doc, reading, ai_call_id=call)
    rematch = [j for j in _jobs(world) if j.task_name == "rematch_on_evidence"]
    assert [j.lock for j in rematch] == [f"doc:{doc}"]

    plans = run_rematch_on_evidence(deps, workspace_id=world.ws, document_id=doc, attempt=0)
    assert [p.outcome for p in plans] == ["written"]
    (row,) = _rows(world, tx)
    assert row.document_id == doc and row.status == "proposed"
    assert deps.model_client.requests == []  # type: ignore[union-attr]


# --- rematch_waiting ------------------------------------------------------------------------


def _put(world: World, tx, *parts, learn_rule: bool, **scope):
    answer = AllocationSet(
        transaction_id=tx,
        allocations=[
            AllocationDraft(amount=Decimal(amount), category=AllocationCategory(category), **scope)
            for amount, category in parts
        ],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )
    return world.cmd(
        review.allocate_transaction,
        tx,
        ConfirmAllocationsBody(allocation_set=answer, learn_rule=learn_rule),
        iban_key=TEST_IBAN_KEY,
    )


def _all_rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table)
            .where(allocation_table.c.transaction_id == tx)
            .order_by(allocation_table.c.recorded_at, allocation_table.c.id)
        ).all()
    )


def _hit_count(world: World, rule_id) -> int:
    return world.query(
        lambda c: c.execute(
            select(rule_table.c.hit_count).where(rule_table.c.id == rule_id)
        ).scalar_one()
    )


def _unreconciled_import(world: World):
    with workspace_transaction(world.engine, world.ws) as conn:
        doc = world._document_row(conn, type="statement", subtype=None, title=None)
        return conn.execute(
            insert(import_table)
            .values(
                workspace_id=world.ws,
                account_id=world.account_id,
                document_id=doc,
                format="camt",
                status=ImportStatus.UNRECONCILED.value,
                error_code="statement.unreconciled",
                balance_source="file",
                declared_closing=Decimal("0.00"),
                computed_closing=Decimal("1.00"),
                created_at=world.now,
                updated_at=world.now,
            )
            .returning(import_table.c.id)
        ).scalar_one()


def _rematch(deps, world: World, kind: str, entity_id):
    return run_rematch_waiting(
        deps, workspace_id=world.ws, trigger=f"{kind}:{entity_id}", attempt=0
    )


def _rematch_jobs(world: World):
    return [j for j in _jobs(world) if j.task_name == "rematch_waiting"]


def test_a_learned_rule_books_the_payers_waiting_lines_without_the_model(
    world, app_engine, tmp_path
):
    """Months imported at once, nothing answered yet: answering one Grundsteuer line with
    "Künftig immer so zuordnen" books the Stadtkasse's other waiting lines, and only those."""

    def stadt(day: date, amount: str = "-120.00", **kw):
        return world.line(day, amount, iban=STADT_IBAN, purpose="Grundsteuer", **kw)

    first = stadt(date(2026, 2, 16))
    no_rows = stadt(date(2026, 5, 15))
    other_proposal = stadt(date(2026, 8, 17))  # the model proposed another category
    same_proposal = stadt(date(2026, 8, 18))  # the model proposed exactly the rule's split
    by_person = stadt(date(2026, 11, 16))  # answered earlier, by hand
    refund = stadt(date(2026, 6, 1), "120.00")  # the other sign
    painter = world.line(date(2026, 5, 15), "-120.00", iban=PAINTER_IBAN, purpose="Grundsteuer")
    unreconciled = stadt(date(2026, 9, 15), import_id=_unreconciled_import(world))

    ai = FakeModelClient(
        [
            _answer(("other_expense", "-120.00", None, False)),
            _answer(("grundsteuer", "-120.00", None, False)),
        ]
    )
    ai_deps = _deps(app_engine, tmp_path, ai)
    _run(ai_deps, world, other_proposal)
    _run(ai_deps, world, same_proposal)
    for tx, category in ((other_proposal, "other_expense"), (same_proposal, "grundsteuer")):
        (row,) = _rows(world, tx)
        assert (row.method, row.status, row.category) == ("llm", "proposed", category)
    _put(world, by_person, ("-120.00", "other_expense"), learn_rule=False)
    assert _rematch_jobs(world) == []  # no rule, no job

    rule = _put(
        world, first, ("-120.00", "grundsteuer"), learn_rule=True, property_id=world.property_id
    ).rule
    assert rule is not None and rule.split_method.value == "single_category"
    (job,) = _rematch_jobs(world)
    assert job.lock == waiting_lock(f"rule:{rule.id}")
    assert job.args == {"workspace_id": str(world.ws), "trigger": f"rule:{rule.id}"}

    picked = world.query(matching.waiting_lines, "rule", rule.id)
    assert picked == [no_rows, other_proposal, same_proposal]

    client = FakeModelClient()  # AI on, nothing scripted: any call would fail the test
    deps = _deps(app_engine, tmp_path, client)
    plans = run_rematch_waiting(deps, workspace_id=world.ws, trigger=job.args["trigger"], attempt=0)
    assert [(p.outcome, p.reason) for p in plans] == [("written", "routing.auto_confirm")] * 3
    assert client.requests == []

    for tx in (no_rows, other_proposal, same_proposal):
        (row,) = _rows(world, tx)
        assert (row.method, row.status, row.category, row.rule_id) == (
            "rule",
            "confirmed",
            "grundsteuer",
            rule.id,
        )
        assert row.confirmed_by is None and row.amount == Decimal("-120.00")
    for tx in (other_proposal, same_proposal):  # the proposal is superseded, not edited
        rows = _all_rows(world, tx)  # one clock here: recorded_at does not order them
        assert sorted((r.method, r.status) for r in rows) == [
            ("llm", "superseded"),
            ("rule", "confirmed"),
        ]
        (old,) = [r for r in rows if r.status == "superseded"]
        (new,) = [r for r in rows if r.status == "confirmed"]
        assert new.supersedes_id == old.id
    (kept,) = _rows(world, by_person)
    assert (kept.method, kept.status, kept.category) == ("user", "confirmed", "other_expense")
    for tx in (refund, painter, unreconciled):
        assert _rows(world, tx) == []
    assert _hit_count(world, rule.id) == 3

    # The booked lines left the inbox; a second run finds nothing waiting.
    listed = {i.transaction_id for i in world.query(review.inbox)}
    assert listed.isdisjoint({first, no_rows, other_proposal, same_proposal, by_person})
    assert {refund, painter} <= listed
    assert _rematch(deps, world, "rule", rule.id) == []


def test_a_rule_disabled_before_its_job_runs_rematches_nothing(world, app_engine, tmp_path):
    first = world.line(date(2026, 2, 16), "-120.00", iban=STADT_IBAN, purpose="Grundsteuer")
    waiting = world.line(date(2026, 5, 15), "-120.00", iban=STADT_IBAN, purpose="Grundsteuer")
    rule = _put(world, first, ("-120.00", "grundsteuer"), learn_rule=True).rule
    world.cmd(rules_learned.disable_rule, rule.id)
    assert world.query(matching.waiting_lines, "rule", rule.id) == []
    client = FakeModelClient()
    deps = _deps(app_engine, tmp_path, client)
    assert _rematch(deps, world, "rule", rule.id) == []
    assert _rows(world, waiting) == [] and client.requests == []


def test_a_same_amount_only_rule_books_only_the_waiting_lines_of_its_amount(
    world, app_engine, tmp_path
):
    """D49 (8): answered "Nein, nur Zahlungen über 120,00 €", the rule's re-check books the
    waiting lines of that amount; a line of another amount from the same IBAN is left as it
    was (no proposal on a single-kind history, D49 (5)) and stays in the inbox."""
    first = world.line(date(2026, 2, 16), "-120.00", iban=STADT_IBAN, purpose="Grundsteuer")
    same = world.line(date(2026, 5, 15), "-120.00", iban=STADT_IBAN, purpose="Grundsteuer")
    other = world.line(date(2026, 5, 20), "-45.00", iban=STADT_IBAN, purpose="Gebuehren")
    answer = AllocationSet(
        transaction_id=first,
        allocations=[
            AllocationDraft(amount=Decimal("-120.00"), category=AllocationCategory("grundsteuer"))
        ],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )
    rule = world.cmd(
        review.allocate_transaction,
        first,
        ConfirmAllocationsBody(allocation_set=answer, learn_rule=True, same_amount_only=True),
        iban_key=TEST_IBAN_KEY,
    ).rule
    assert rule is not None and rule.same_amount_only is True
    (job,) = _rematch_jobs(world)

    client = FakeModelClient()  # any model call would fail the test
    deps = _deps(app_engine, tmp_path, client)
    plans = run_rematch_waiting(deps, workspace_id=world.ws, trigger=job.args["trigger"], attempt=0)
    assert sorted((p.outcome, p.reason) for p in plans) == [
        ("skipped", "routing.no_match"),
        ("written", "routing.auto_confirm"),
    ]
    assert client.requests == []
    (booked,) = _rows(world, same)
    assert (booked.status, booked.rule_id) == ("confirmed", rule.id)
    assert _rows(world, other) == []
    assert _hit_count(world, rule.id) == 1
    assert other in {i.transaction_id for i in world.query(review.inbox)}


def test_a_rent_rule_books_the_waiting_rent_that_fits_the_schedule_exactly(
    world, app_engine, tmp_path
):
    """The rent comes from an account the tenancy does not know (the rent matcher misses
    it), so the lines wait. A two-row answer becomes a by_rent_schedule rule; it books the
    months paid in full and leaves the one paid 20 € short waiting."""
    tenancy = world.tenancy()  # 1050.00 + 230.00; the tenant's own IBAN is TENANT_IBAN

    def rent(month: int, amount: str = "1280.00"):
        return world.line(
            date(2026, month, 2), amount, iban=OTHER_TENANT_IBAN, purpose=f"Miete {month:02d}/2026"
        )

    january, february, march, april = rent(1), rent(2), rent(3, "1260.00"), rent(4)
    rule = _put(
        world,
        january,
        ("1050.00", "rent_cold"),
        ("230.00", "operating_costs_prepayment"),
        learn_rule=True,
        property_id=world.property_id,
        unit_id=world.unit_id,
        tenancy_id=tenancy,
    ).rule
    assert rule.split_method.value == "by_rent_schedule" and rule.tenancy_id == tenancy
    assert world.query(matching.waiting_lines, "rule", rule.id) == [february, march, april]

    client = FakeModelClient()
    deps = _deps(app_engine, tmp_path, client)
    plans = _rematch(deps, world, "rule", rule.id)
    assert [p.outcome for p in plans] == ["written", "skipped", "written"]
    assert client.requests == []
    for tx, month in ((february, 2), (april, 4)):
        rows = _rows(world, tx)
        assert sorted((r.category, r.amount, r.method, r.status) for r in rows) == [
            ("operating_costs_prepayment", Decimal("230.00"), "rule", "confirmed"),
            ("rent_cold", Decimal("1050.00"), "rule", "confirmed"),
        ]
        assert {(r.service_period_start, r.tenancy_id) for r in rows} == {
            (date(2026, month, 1), tenancy)
        }
    assert _rows(world, march) == []  # 20 € short: still waiting, nothing asked of the model
    (item,) = [i for i in world.query(review.inbox) if i.transaction_id == march]
    assert item.kind is InboxItemKind.UNMATCHED


def test_a_split_answer_books_the_next_line_of_the_same_amount_as_it_was_split(
    world, app_engine, tmp_path
):
    """D51, Luis (8 Nov 2026): the Hausverwaltung's Hausgeld, split by its purpose text into
    the reserve and the operating costs, could not be learned. Answered once with "Künftig
    immer so zuordnen", the rule (no amounts) copies that split onto the payer's waiting
    line of the same amount and onto a later line; another amount stays waiting."""

    def hv(day: date, amount: str = "-380.00"):
        return world.line(day, amount, iban=WEG_IBAN, name="Hausverwaltung", purpose="Hausgeld")

    first = hv(date(2026, 2, 1))
    same = hv(date(2026, 3, 2))
    other = hv(date(2026, 4, 1), "-395.00")
    fee = world.line(date(2026, 3, 3), "-29.75", iban=WEG_IBAN, purpose="Mietverwaltung")

    result = _put(
        world,
        first,
        ("-260.00", "hausgeld_umlagefaehig"),
        ("-120.00", "hausgeld_ruecklage"),
        learn_rule=True,
        property_id=world.property_id,
    )
    rule = result.rule
    assert rule is not None and rule.split_method.value == "as_last_split"
    assert rule.categories == [
        AllocationCategory.HAUSGELD_RUECKLAGE,
        AllocationCategory.HAUSGELD_UMLAGEFAEHIG,
    ]
    stored = world.query(
        lambda c: c.execute(select(rule_table).where(rule_table.c.id == rule.id)).one()
    )
    assert stored.categories == ["hausgeld_ruecklage", "hausgeld_umlagefaehig"]
    assert [c for c in stored._fields if "amount" in c] == ["same_amount_only"]  # D25

    client = FakeModelClient()  # AI on, nothing scripted: any call would fail the test
    deps = _deps(app_engine, tmp_path, client)
    plans = _rematch(deps, world, "rule", rule.id)
    assert sorted(p.outcome for p in plans) == ["skipped", "skipped", "written"]
    assert client.requests == []

    def copied(tx):
        rows = _rows(world, tx)
        assert {(r.method, r.status, r.rule_id, r.confirmed_by) for r in rows} == {
            ("rule", "confirmed", rule.id, None)
        }
        assert {(r.property_id, r.document_id) for r in rows} == {(world.property_id, None)}
        return sorted((r.category, r.amount) for r in rows)

    expected = [
        ("hausgeld_ruecklage", Decimal("-120.00")),
        ("hausgeld_umlagefaehig", Decimal("-260.00")),
    ]
    assert copied(same) == expected
    assert _rows(world, other) == [] and _rows(world, fee) == []

    later = hv(date(2026, 5, 4))
    assert _run(_deps(app_engine, tmp_path, client, ai_enabled=False), world, later).outcome == (
        "written"
    )
    assert copied(later) == expected
    assert _hit_count(world, rule.id) == 2


# --- rematch_waiting on master data entered after the import ------------------------------


def _waiting_jobs(world: World, trigger: str):
    return [j for j in _jobs(world) if j.lock == waiting_lock(trigger)]


def test_a_loan_entered_after_the_import_books_the_instalments_that_fit(
    world, app_engine, tmp_path
):
    may = world.line(date(2024, 5, 2), "-1400.00", iban=LOAN_IBAN, purpose="Rate")
    june = world.line(date(2024, 6, 3), "-1400.00", iban=LOAN_IBAN, purpose="Rate")
    july = world.line(date(2024, 7, 1), "-1450.00", iban=LOAN_IBAN, purpose="Rate")  # no fit
    august = world.line(date(2024, 8, 1), "-1400.00", iban=LOAN_IBAN, purpose="Rate")
    _put(world, august, ("-1400.00", "other_expense"), learn_rule=False)  # a person's answer
    credit = world.line(date(2024, 5, 2), "50.00", iban=LOAN_IBAN, purpose="Gutschrift")

    loan = world.loan()  # account LOAN_IBAN, 1400.00 a month from 1 May 2024
    trigger = f"loan:{loan}"
    (job,) = _waiting_jobs(world, trigger)
    assert job.task_name == "rematch_waiting"
    assert job.args == {"workspace_id": str(world.ws), "trigger": trigger}
    assert world.query(matching.waiting_lines, "loan", loan) == [may, june, july]

    client = FakeModelClient()
    deps = _deps(app_engine, tmp_path, client)
    plans = _rematch(deps, world, "loan", loan)
    assert [p.outcome for p in plans] == ["written", "written", "skipped"]
    assert client.requests == []
    entries = world.query(loans.loan_schedule, loan).entries
    for tx, entry in ((may, entries[0]), (june, entries[1])):
        rows = _rows(world, tx)
        assert sorted((r.category, r.amount, r.method, r.status, r.loan_id) for r in rows) == [
            ("loan_interest", -entry.interest, "matcher_loan", "confirmed", loan),
            ("loan_principal", -entry.principal, "matcher_loan", "confirmed", loan),
        ]
    assert _rows(world, july) == [] and _rows(world, credit) == []
    (kept,) = _rows(world, august)
    assert (kept.method, kept.category) == ("user", "other_expense")


def test_an_interest_only_loan_books_the_waiting_interest_its_text_states(
    world, app_engine, tmp_path
):
    """A loan paid out in parts: before the first annuity the bank debits interest only, a
    different amount each month, and states it in the purpose. Entered after the import with
    interest_only_before_first_payment, the loan books those debits as loan_interest; a debit
    whose stated interest is not the amount stays waiting."""
    july = world.line(
        date(2026, 7, 30),
        "-787.43",
        iban=LOAN_IBAN,
        purpose="Rechnung Darl.-Leistung 7000000001 Für 01.07.2026 - 30.07.2026 "
        "Saldo: 262.477,00- Zinsen 787,43 20260730-XX001-00000000001",
    )
    october = world.line(
        date(2026, 10, 30),
        "-1061.82",
        iban=LOAN_IBAN,
        purpose="Rechnung Darl.-Leistung 7000000001 Für 01.10.2026 - 30.10.2026 Zinsen 1.061,82",
    )
    differs = world.line(
        date(2026, 8, 30),
        "-800.00",
        iban=LOAN_IBAN,
        purpose="Rechnung Darl.-Leistung 7000000001 Für 01.08.2026 - 30.08.2026 Zinsen 787,43",
    )

    loan = world.loan(
        principal=Decimal("360000.00"),
        monthly_payment=Decimal("1600.00"),
        disbursed_on=date(2026, 6, 30),
        first_payment_on=date(2026, 12, 30),
        fixed_until=None,
        interest_only_before_first_payment=True,
    )
    assert len(_waiting_jobs(world, f"loan:{loan}")) == 1

    client = FakeModelClient()
    plans = _rematch(_deps(app_engine, tmp_path, client), world, "loan", loan)
    # Booking date order: July, August (stated interest differs), October.
    assert [p.outcome for p in plans] == ["written", "skipped", "written"]
    assert client.requests == []
    for tx, amount, start, end in (
        (july, "-787.43", date(2026, 7, 1), date(2026, 7, 30)),
        (october, "-1061.82", date(2026, 10, 1), date(2026, 10, 30)),
    ):
        (row,) = _rows(world, tx)
        assert (
            row.category,
            row.amount,
            row.method,
            row.status,
            row.loan_id,
            row.service_period_start,
            row.service_period_end,
            row.recurring,
        ) == ("loan_interest", Decimal(amount), "matcher_loan", "confirmed", loan, start, end, True)
    assert _rows(world, differs) == []


def test_a_burst_of_loan_edits_queues_one_job_and_the_contract_number_none(world):
    loan = world.loan()
    trigger = f"loan:{loan}"
    world.cmd(loans.update_loan, loan, monthly_payment=Decimal("1500.00"))
    world.cmd(loans.update_loan, loan, lender="Andere Bank AG")
    assert len(_waiting_jobs(world, trigger)) == 1  # the waiting job reads the loan when it runs
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    world.cmd(loans.update_loan, loan, contract_number="DA-4711")
    assert _waiting_jobs(world, trigger) == []  # the matcher does not read it
    world.cmd(loans.update_loan, loan, account_iban=PAINTER_IBAN, iban_key=TEST_IBAN_KEY)
    assert len(_waiting_jobs(world, trigger)) == 1


def test_rent_paid_before_the_tenants_iban_was_saved_is_booked_once_it_is(
    world, app_engine, tmp_path
):
    tenancy = world.tenancy(iban=None)  # 1050.00 + 230.00; the tenant's IBAN not known yet
    tenant_id = world.query(tenancies.get_tenancy, tenancy).parties[0].tenant_id

    def rent(month: int):
        return world.line(
            date(2026, month, 2), "1280.00", iban=TENANT_IBAN, purpose=f"Miete {month:02d}/2026"
        )

    january, february, march = rent(1), rent(2), rent(3)
    _put(world, march, ("1280.00", "other_income"), learn_rule=False)  # answered by hand
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))

    world.cmd(update_tenant, tenant_id, iban=TENANT_IBAN, iban_key=TEST_IBAN_KEY)
    trigger = f"tenant:{tenant_id}"
    assert len(_waiting_jobs(world, trigger)) == 1
    assert world.query(matching.waiting_lines, "tenant", tenant_id) == [january, february]

    client = FakeModelClient()
    deps = _deps(app_engine, tmp_path, client)
    assert [p.outcome for p in _rematch(deps, world, "tenant", tenant_id)] == ["written"] * 2
    assert client.requests == []
    for tx, month in ((january, 1), (february, 2)):
        rows = _rows(world, tx)
        assert sorted((r.category, r.amount, r.method, r.status) for r in rows) == [
            ("operating_costs_prepayment", Decimal("230.00"), "matcher_rent", "confirmed"),
            ("rent_cold", Decimal("1050.00"), "matcher_rent", "confirmed"),
        ]
        assert {r.service_period_start for r in rows} == {date(2026, month, 1)}
    (kept,) = _rows(world, march)
    assert (kept.method, kept.category) == ("user", "other_income")


def test_a_new_tenancy_with_its_rent_queues_one_job(world):
    tenancy = world.tenancy()
    assert len(_waiting_jobs(world, f"tenancy:{tenancy}")) == 1  # the initial rent row


def test_a_later_move_out_books_the_rent_now_inside_the_tenancy(world, app_engine, tmp_path):
    tenancy = world.tenancy()  # 1050.00 + 230.00 from 1 Jan 2024
    world.cmd(tenancies.end_tenancy, tenancy, date(2026, 6, 30))
    july = world.line(date(2026, 7, 2), "1280.00", iban=TENANT_IBAN, purpose="Miete 07/2026")
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    trigger = f"tenancy:{tenancy}"

    world.cmd(tenancies.correct_move_out, tenancy, date(2026, 6, 15))
    assert _waiting_jobs(world, trigger) == []  # an earlier move-out books nothing new
    world.cmd(tenancies.correct_move_out, tenancy, date(2026, 7, 31))
    assert len(_waiting_jobs(world, trigger)) == 1

    client = FakeModelClient()
    deps = _deps(app_engine, tmp_path, client)
    assert [p.outcome for p in _rematch(deps, world, "tenancy", tenancy)] == ["written"]
    assert client.requests == []
    rows = _rows(world, july)
    assert sorted((r.category, r.method, r.status) for r in rows) == [
        ("operating_costs_prepayment", "matcher_rent", "confirmed"),
        ("rent_cold", "matcher_rent", "confirmed"),
    ]


def test_an_owner_account_added_later_books_the_waiting_transfers(world, app_engine, tmp_path):
    top_up = world.line(date(2026, 3, 2), "500.00", iban=OWNER_IBAN, purpose="Einlage")
    withdrawal = world.line(date(2026, 3, 30), "-200.00", iban=OWNER_IBAN, purpose="Entnahme")
    answered = world.line(date(2026, 4, 2), "300.00", iban=OWNER_IBAN, purpose="Einlage")
    _put(world, answered, ("300.00", "other_income"), learn_rule=False)
    painter = world.line(date(2026, 3, 2), "-80.00", iban=PAINTER_IBAN)

    def account(label: str, iban: str, owner: bool) -> AccountCreate:
        return AccountCreate(
            label=label,
            bank="Sparkasse",
            iban=iban,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 1, 1),
            is_owner_account=owner,
        )

    world.cmd(
        create_account,
        account("Tagesgeld", "DE50100000000000000001", False),
        iban_key=TEST_IBAN_KEY,
    )
    assert _rematch_jobs(world) == []  # not an owner account: nothing to re-match
    owner = world.cmd(create_account, account("Privat", OWNER_IBAN, True), iban_key=TEST_IBAN_KEY)
    assert len(_waiting_jobs(world, f"account:{owner.id}")) == 1
    assert world.query(matching.waiting_lines, "account", owner.id) == [top_up, withdrawal]

    client = FakeModelClient()
    deps = _deps(app_engine, tmp_path, client)
    assert [p.outcome for p in _rematch(deps, world, "account", owner.id)] == ["written"] * 2
    assert client.requests == []
    for tx, category in ((top_up, "owner_transfer_in"), (withdrawal, "owner_transfer_out")):
        (row,) = _rows(world, tx)
        assert (row.category, row.method, row.status) == (category, "matcher_owner", "confirmed")
    (kept,) = _rows(world, answered)
    assert (kept.method, kept.category) == ("user", "other_income")
    assert _rows(world, painter) == []


# --- Asking the model again: POST /inbox/ai-retry (D50) ------------------------------------


def _clear_jobs(world: World) -> None:
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(text("DELETE FROM procrastinate_jobs"))


def test_ai_retry_queues_only_the_listed_lines_without_rows(world, app_engine, tmp_path):
    bare = world.line(date(2026, 3, 3), "-312.40", name="Allianz", purpose="Beitrag")
    proposed = world.line(date(2026, 3, 4), "-50.00", name="Stadtwerke")
    client = FakeModelClient([_answer(("insurance", "-50.00", None, True))])
    _run(_deps(app_engine, tmp_path, client), world, proposed)
    assert _rows(world, proposed)[0].status == "proposed"
    confirmed = world.line(date(2026, 3, 5), "-20.00", name="Sparkasse")
    _put(world, confirmed, ("-20.00", "bank_fees"), learn_rule=False)
    excluded = world.line(date(2026, 3, 3), "-312.40", name="Allianz", purpose="Beitrag")
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(transaction_table)
            .where(transaction_table.c.id == excluded)
            .values(excluded_as_duplicate_of=bare)
        )
    _clear_jobs(world)

    result = world.cmd(review.request_ai_suggestions, ai_enabled=True)
    assert result.queued == 1
    assert [(j.task_name, j.lock) for j in _jobs(world)] == [
        ("match_transaction", transaction_lock(bare))
    ]
    assert _events(world, bare) == []

    # The queued job is the normal one (not a rematch), so the model is asked.
    (job,) = _jobs(world)
    client = FakeModelClient([_answer(("insurance", "-312.40", None, True))])
    _run(_deps(app_engine, tmp_path, client), world, job.args["transaction_id"])
    assert len(client.requests) == 1
    (row,) = _rows(world, bare)
    assert (row.method, row.status) == ("llm", "proposed")
    _clear_jobs(world)
    assert world.cmd(review.request_ai_suggestions, ai_enabled=True).queued == 0


def test_ai_retry_is_refused_while_the_ai_layer_is_off(world):
    world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    _clear_jobs(world)
    with pytest.raises(RuleViolationError) as info:
        world.cmd(review.request_ai_suggestions, ai_enabled=False)
    assert info.value.code == "ai.disabled"
    assert _jobs(world) == []


def test_only_the_latest_categorize_call_decides_the_failure_text(world, app_engine, tmp_path):
    """A failure followed by a usable call (whose answer was, say, not written) is not shown
    as a failure; an explain call does not count either way."""
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz")
    settings = _deps(app_engine, tmp_path, None).settings
    request = categorize_request(settings, {}, hard=False)
    explain = explain_request(settings, {})
    for call, kwargs in (
        (request, {"failure": ModelCallError(retryable=False, status=400)}),
        (request, {"response": text_response("{}")}),
        (explain, {"failure": ModelCallError(retryable=True, status=529)}),
    ):
        with workspace_transaction(world.engine, world.ws) as conn:
            record_ai_call(conn, workspace_id=world.ws, request=call, transaction_id=tx, **kwargs)
    (item,) = world.query(review.inbox)
    assert item.reasoning == UNMATCHED_TEXT
    with workspace_transaction(world.engine, world.ws) as conn:
        record_ai_call(
            conn,
            workspace_id=world.ws,
            request=request,
            transaction_id=tx,
            failure=ModelCallError(retryable=True, status=529),
        )
    (item,) = world.query(review.inbox)
    assert item.reasoning == AI_FAILED_TEXT
