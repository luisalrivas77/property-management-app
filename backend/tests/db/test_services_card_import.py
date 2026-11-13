"""The credit card's PDF statement imported end to end, as hausbuch_app under RLS (phase 6
step 2b, D176 (2)-(4); fixture tests/fixtures/phase6/card_pdf.yaml, its `import` half).

Each case is imported onto a card account made from `bases.account`, after the cases its
`import.after` names. A reconciled import's lines are then matched with the model off: the
interest line is booked by code to hh_interest_fees and confirmed, the payment line
„Bezahlung“ as internal_transfer_in (D176 (4)), every other line waits. The
first import sets the account's reference; the printed payment IBAN is only proposed until a
person confirms it from the statement's original.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import insert, select, text
from test_card_pdf import BUILT, CARD, case_bytes
from test_jobs_document_read import _settings as _test_settings

from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.jobs import enqueue
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import ACCOUNT_KIND_NOT_AVAILABLE, RuleError
from hausbuch.schemas.allocation import (
    AllocationCategory,
    MatchingRuleCreate,
    RuleMatcher,
    SplitMethod,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate, ConfirmPaymentIbanBody, TransactionAllocationStatus
from hausbuch.schemas.enums import (
    AccountKind,
    AccountPurpose,
    ActorType,
    DocumentSource,
    ImportStatus,
    ProposalStatus,
)
from hausbuch.schemas.events import EventType
from hausbuch.services import documents, proposals, review, rules_learned, statements
from hausbuch.services.accounts import (
    confirm_payment_iban,
    create_account,
    get_account,
    update_account,
)
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.iban import protect_iban
from hausbuch.services.statements import import_statement, list_imports, list_transactions
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
STORED = [c for c in BUILT if c.import_.status is not None]
REFUSED = [c for c in BUILT if c.import_.error is not None]


@pytest.fixture
def hook_calls(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        statements, "_on_reconciled", lambda ctx, import_id: calls.append(import_id)
    )
    return calls


def _deps(app_engine, tmp_path) -> JobDeps:
    settings = Settings(
        database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
        iban_hmac_key=TEST_IBAN_KEY,
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root=tmp_path / "blobs",
        ai_enabled=False,
    )
    return JobDeps(
        engine=app_engine,
        settings=settings,
        blob_store=None,  # type: ignore[arg-type]  # matching reads no original
        model_client=None,  # type: ignore[arg-type]  # the model is off
        clock=lambda: T0,
    )


def _card(run, ws, **overrides):
    base = CARD.bases["account"]
    data = {
        "label": "Kreditkarte",
        "bank": "Beispielbank",
        "kind": AccountKind(base["kind"]),
        "provider": base["provider"],
        "opening_balance": Decimal(base["opening_balance"]),
        "opening_balance_on": base["opening_balance_on"],
    } | overrides
    return run(ws, create_account, AccountCreate(**data), iban_key=TEST_IBAN_KEY, now=T0)


def _import(run, blob_store, ws, account_id, case, n=0):
    return run(
        ws,
        import_statement,
        blob_store,
        account_id,
        case_bytes(case),
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        original_filename="abrechnung.pdf",
        now=T0 + timedelta(minutes=n),
    )


def _after(run, blob_store, ws, account_id, case):
    for n, name in enumerate(case.import_.after):
        before = next(c for c in CARD.cases if c.id == name)
        _import(run, blob_store, ws, account_id, before, n)
    return len(case.import_.after)


@pytest.mark.parametrize("case", STORED, ids=lambda c: c.id)
def test_a_card_statement_imports_as_the_fixture_says(
    case, run, read, blob_store, fresh_workspaces, hook_calls, app_engine, tmp_path
):
    ws, _ = fresh_workspaces
    outcome = case.import_
    account = _card(run, ws)
    assert account.purpose is AccountPurpose.PRIVATE and account.iban_masked is None
    n = _after(run, blob_store, ws, account.id, case)
    hook_calls.clear()
    imported = _import(run, blob_store, ws, account.id, case, n)

    assert imported.format.value == "card_pdf"
    assert imported.status is outcome.status
    assert imported.error_code == outcome.error_code
    assert (imported.lines_new, imported.lines_skipped) == (
        outcome.lines_new,
        outcome.lines_skipped,
    )
    after = read(ws, get_account, account.id)
    if outcome.account_ref_masked is not None:
        assert after.external_ref_masked == outcome.account_ref_masked
    if outcome.payment_iban_masked is not None:
        assert after.payment_iban_masked is None  # only proposed (principle 3)
        assert after.payment_iban_proposal.payment_iban_masked == outcome.payment_iban_masked
    if case.expect.card_last4 is not None:
        assert after.card_last4 == case.expect.card_last4

    reconciled = imported.status is ImportStatus.RECONCILED
    if not reconciled:
        assert hook_calls == []  # D34: a broken file's lines are never matched
    elif imported.lines_new:
        assert hook_calls == [imported.id]
    lines = read(ws, list_transactions, account.id, import_id=imported.id)
    deps = _deps(app_engine, tmp_path)
    for line in lines:
        run_match_transaction(deps, workspace_id=ws, transaction_id=line.id, attempt=0)
    lines = {t.bank_reference: t for t in read(ws, list_transactions, account.id)}
    pinned = {p.index: p.line for p in case.expect.lines}
    booked = {pinned[b.index].bank_reference: b.category for b in outcome.booked}
    statement = statements.parse_statement(case_bytes(case), "application/pdf").statement
    for parsed in statement.lines:
        line = lines[parsed.bank_reference]
        if outcome.none_matched:
            assert line.allocation_status is TransactionAllocationStatus.NOT_RECONCILED
        elif parsed.bank_reference in booked:
            assert line.allocation_status is TransactionAllocationStatus.CONFIRMED
            assert line.allocation_categories == [AllocationCategory(booked[parsed.bank_reference])]
        elif imported.lines_new:
            assert line.allocation_status is TransactionAllocationStatus.OPEN


@pytest.mark.parametrize("case", REFUSED, ids=lambda c: c.id)
def test_a_card_statement_of_another_customer_is_refused(
    case, run, read, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    account = _card(run, ws)
    n = _after(run, blob_store, ws, account.id, case)
    with pytest.raises(RuleError) as caught:
        _import(run, blob_store, ws, account.id, case, n)
    assert caught.value.code == case.import_.error
    assert len(read(ws, list_imports, account.id)) == n


def test_every_built_case_is_covered():
    assert {c.id for c in STORED} | {c.id for c in REFUSED} == {c.id for c in BUILT}
    assert len(BUILT) == len(CARD.cases)


def _first(case_id="plain_month"):
    return next(c for c in CARD.cases if c.id == case_id)


def test_a_card_statement_goes_onto_a_card_account_only(run, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    bank = run(
        ws,
        create_account,
        AccountCreate(
            label="Girokonto",
            bank="Beispielbank",
            iban="DE89370400440532013000",
            opening_balance=Decimal("0.00"),
            opening_balance_on=T0.date(),
            purpose=AccountPurpose.PRIVATE,
        ),
        iban_key=TEST_IBAN_KEY,
    )
    with pytest.raises(RuleError) as caught:
        _import(run, blob_store, ws, bank.id, _first())
    assert caught.value.code == rules.ACCOUNT_MISMATCH


def test_a_second_card_account_cannot_take_the_first_ones_statements(
    run, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    first, second = _card(run, ws), _card(run, ws, label="Zweite Karte")
    _import(run, blob_store, ws, first.id, _first())
    with pytest.raises(RuleError) as caught:
        _import(run, blob_store, ws, second.id, _first("foreign_currency"))
    assert caught.value.code == rules.ACCOUNT_MISMATCH


def test_a_card_account_is_private_and_its_typed_number_is_masked(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    card = _card(run, ws, external_ref="1234567")
    assert card.external_ref_masked == "**** 4567"
    assert card.purpose is AccountPurpose.PRIVATE
    with pytest.raises(RuleError) as caught:
        _card(run, ws, purpose=AccountPurpose.RENTAL, label="Mietkarte")
    assert caught.value.code == ACCOUNT_KIND_NOT_AVAILABLE
    payloads = read(
        ws,
        lambda conn: (
            conn.execute(select(event_table.c.payload).where(event_table.c.entity_id == card.id))
            .scalars()
            .all()
        ),
    )
    assert "1234567" not in str(payloads)


def test_a_card_account_stays_private(run, fresh_workspaces):
    """Review F4: a new purpose on a card is refused, as on create; its label still changes."""
    ws, _ = fresh_workspaces
    card = _card(run, ws)
    with pytest.raises(RuleError) as caught:
        run(ws, update_account, card.id, purpose=AccountPurpose.RENTAL, now=T0)
    assert caught.value.code == ACCOUNT_KIND_NOT_AVAILABLE
    renamed = run(ws, update_account, card.id, label="Karte", purpose=AccountPurpose.PRIVATE)
    assert (renamed.label, renamed.purpose) == ("Karte", AccountPurpose.PRIVATE)


def test_the_payment_iban_is_confirmed_from_the_statement(
    run, read, blob_store, fresh_workspaces, hook_calls, app_engine
):
    ws, _ = fresh_workspaces
    case = _first()
    card = _card(run, ws)
    imported = _import(run, blob_store, ws, card.id, case)
    proposal = read(ws, get_account, card.id).payment_iban_proposal
    assert proposal.import_id == imported.id

    body = ConfirmPaymentIbanBody(import_id=imported.id)
    result = run(ws, confirm_payment_iban, blob_store, card.id, body, iban_key=TEST_IBAN_KEY)
    confirmed = result.account
    assert confirmed.payment_iban_masked == case.import_.payment_iban_masked
    assert confirmed.payment_iban_proposal is None
    assert result.rules_disabled == 0
    # D176 (4): the debits to the confirmed IBAN that wait are re-checked (rematch_waiting).
    lock = enqueue.waiting_lock(enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, card.id))
    with app_engine.begin() as conn:
        queued = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE lock = :lock"), {"lock": lock}
        ).scalar_one()
        conn.execute(text("DELETE FROM procrastinate_jobs WHERE lock = :lock"), {"lock": lock})
    assert queued == 1
    again = run(ws, confirm_payment_iban, blob_store, card.id, body, iban_key=TEST_IBAN_KEY)
    assert (again.account, again.rules_disabled) == (confirmed, 0)

    events = read(
        ws,
        lambda conn: (
            conn.execute(
                select(event_table.c.payload).where(
                    event_table.c.entity_id == card.id,
                    event_table.c.type == EventType.ACCOUNT_UPDATED.value,
                )
            )
            .scalars()
            .all()
        ),
    )
    changed = [set(p["changes"]) for p in events]
    assert changed == [{"external_ref_masked"}, {"payment_iban_masked"}]
    assert case.expect.payment_iban not in str(events)


def _rule_on(run, ws, iban: str, category: AllocationCategory):
    matcher = RuleMatcher(counterparty_iban_hmac=protect_iban(iban, TEST_IBAN_KEY).hmac.hex())
    data = MatchingRuleCreate(
        matcher=matcher, split_method=SplitMethod.SINGLE_CATEGORY, category=category
    )
    return run(ws, rules_learned.create_rule, data)


def test_confirming_the_payment_iban_switches_off_the_rules_on_it(
    run, read, blob_store, fresh_workspaces, hook_calls, app_engine
):
    """A card bill answered as spending with „Künftig immer so zuordnen“ before the card
    account existed left a rule on the issuer's IBAN; a rule books before the transfer
    matcher, so confirming the IBAN switches it off (rule.disabled) and says how many. A rule
    on another IBAN stays."""
    ws, _ = fresh_workspaces
    case = _first()
    on_card = _rule_on(run, ws, case.expect.payment_iban, AllocationCategory.HH_CREDIT_CARD)
    other = _rule_on(run, ws, "DE50100000000000003008", AllocationCategory.HH_GROCERIES)
    card = _card(run, ws)
    imported = _import(run, blob_store, ws, card.id, case)
    body = ConfirmPaymentIbanBody(import_id=imported.id)
    result = run(ws, confirm_payment_iban, blob_store, card.id, body, iban_key=TEST_IBAN_KEY)
    assert result.rules_disabled == 1
    enabled = {r.id for r in read(ws, rules_learned.enabled_rules)}
    assert on_card.id not in enabled and other.id in enabled
    disabled = read(
        ws,
        lambda conn: (
            conn.execute(
                select(event_table.c.entity_id).where(
                    event_table.c.type == EventType.RULE_DISABLED.value
                )
            )
            .scalars()
            .all()
        ),
    )
    assert disabled == [on_card.id]
    with app_engine.begin() as conn:
        conn.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'rematch_waiting'"))


def test_the_payment_iban_needs_an_import_of_this_card(
    run, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    first, second = _card(run, ws), _card(run, ws, label="Zweite Karte")
    imported = _import(run, blob_store, ws, first.id, _first())
    body = ConfirmPaymentIbanBody(import_id=imported.id)
    with pytest.raises(NotFoundError):
        run(ws, confirm_payment_iban, blob_store, second.id, body, iban_key=TEST_IBAN_KEY)


def test_a_card_statement_is_never_sent_to_the_model(run, blob_store, fresh_workspaces, hook_calls):
    ws, _ = fresh_workspaces
    card = _card(run, ws)
    imported = _import(run, blob_store, ws, card.id, _first())
    with pytest.raises(RuleViolationError) as caught:
        run(ws, proposals.request_read, imported.document_id)
    assert caught.value.code == proposals.PROPOSAL_BANK_FILE_NOT_READ


def _read_as_a_document(app_engine, blob_store, ws, tmp_path):
    """The first card statement uploaded through Dokumente with ai on, and its read run: the
    document, its proposal and the fake model client."""
    actor = Actor(ActorType.USER, "user:test")
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, actor, now=T0)
        doc = documents.upload_document(
            ctx,
            blob_store,
            case_bytes(_first()),
            media_type="application/pdf",
            source=DocumentSource.UPLOAD,
        )
        queued = proposals.request_read(ctx, doc.id)
    assert queued.status is ProposalStatus.QUEUED
    client = FakeModelClient([])
    deps = JobDeps(
        engine=app_engine,
        settings=_test_settings(tmp_path),
        blob_store=blob_store,
        model_client=client,
        clock=lambda: T0,
    )
    try:
        run_document_read(
            deps, workspace_id=ws, document_id=doc.id, proposal_id=queued.id, attempt=0
        )
    finally:
        with app_engine.begin() as conn:
            conn.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'document_read'"))
    return doc, queued, client


def test_a_card_statement_uploaded_as_a_document_is_never_sent_to_the_model(
    app_engine, blob_store, fresh_workspaces, tmp_path
):
    """Review F1: POST /documents with ai on (Dokumente, the capture) has no import yet, so the
    request is accepted; the job recognises the card layout and stops before any model call."""
    ws, _ = fresh_workspaces
    _, queued, client = _read_as_a_document(app_engine, blob_store, ws, tmp_path)
    assert client.requests == []
    with workspace_transaction(app_engine, ws) as conn:
        failed = proposals.get_proposal(conn, queued.id)
        calls = conn.execute(select(ai_call_table.c.id)).all()
    assert failed.status is ProposalStatus.FAILED
    assert failed.error_code == proposals.PROPOSAL_BANK_FILE_NOT_READ
    assert calls == []


def test_importing_it_on_the_card_ends_the_couldnt_read_card(
    run, app_engine, blob_store, fresh_workspaces, tmp_path, hook_calls
):
    """Review of step 2b: the person follows the failed card's text and imports the file on
    the card's page; the „couldn't read this“ card then leaves Zu prüfen (its proposal
    superseded, proposal.superseded). A failure with another code on the same file stays."""
    ws, _ = fresh_workspaces
    doc, queued, _ = _read_as_a_document(app_engine, blob_store, ws, tmp_path)
    with workspace_transaction(app_engine, ws) as conn:
        other = conn.execute(
            insert(proposal_table)
            .values(
                workspace_id=ws,
                document_id=doc.id,
                status=ProposalStatus.FAILED.value,
                error_code="ai.disabled",
                created_at=T0,
                updated_at=T0,
            )
            .returning(proposal_table.c.id)
        ).scalar_one()
        before = [i.kind for i in review.inbox(conn)]
    assert before.count(InboxItemKind.READ_FAILED) == 2
    card = _card(run, ws)
    imported = _import(run, blob_store, ws, card.id, _first())
    assert imported.document_id == doc.id
    with workspace_transaction(app_engine, ws) as conn:
        assert proposals.get_proposal(conn, queued.id).status is ProposalStatus.SUPERSEDED
        assert proposals.get_proposal(conn, other).status is ProposalStatus.FAILED
        after = [i.kind for i in review.inbox(conn)]
        superseded = (
            conn.execute(
                select(event_table.c.entity_id).where(
                    event_table.c.type == EventType.PROPOSAL_SUPERSEDED.value
                )
            )
            .scalars()
            .all()
        )
    assert after.count(InboxItemKind.READ_FAILED) == 1
    assert superseded == [queued.id]
