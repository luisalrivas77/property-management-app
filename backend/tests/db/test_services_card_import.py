"""The credit card's PDF statement imported end to end, as hausbuch_app under RLS (phase 6
step 2b, D176 (2)-(4); fixture tests/fixtures/phase6/card_pdf.yaml, its `import` half).

Each case is imported onto a card account made from `bases.account`, after the cases its
`import.after` names. A reconciled import's lines are then matched with the model off: the
interest line is booked by code to hh_interest_fees and confirmed, every other line waits. The
first import sets the account's reference; the printed payment IBAN is only proposed until a
person confirms it from the statement's original.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import select
from test_card_pdf import BUILT, CARD, case_bytes

from hausbuch.db.tables import event as event_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import ACCOUNT_KIND_NOT_AVAILABLE, RuleError
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.bank import AccountCreate, ConfirmPaymentIbanBody, TransactionAllocationStatus
from hausbuch.schemas.enums import AccountKind, AccountPurpose, ImportStatus
from hausbuch.schemas.events import EventType
from hausbuch.services import proposals, statements
from hausbuch.services.accounts import confirm_payment_iban, create_account, get_account
from hausbuch.services.errors import NotFoundError, RuleViolationError
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


def test_the_payment_iban_is_confirmed_from_the_statement(
    run, read, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    case = _first()
    card = _card(run, ws)
    imported = _import(run, blob_store, ws, card.id, case)
    proposal = read(ws, get_account, card.id).payment_iban_proposal
    assert proposal.import_id == imported.id

    body = ConfirmPaymentIbanBody(import_id=imported.id)
    confirmed = run(ws, confirm_payment_iban, blob_store, card.id, body, iban_key=TEST_IBAN_KEY)
    assert confirmed.payment_iban_masked == case.import_.payment_iban_masked
    assert confirmed.payment_iban_proposal is None
    again = run(ws, confirm_payment_iban, blob_store, card.id, body, iban_key=TEST_IBAN_KEY)
    assert again == confirmed

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
